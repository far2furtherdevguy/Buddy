import 'dart:async';
import 'dart:convert';
import 'dart:isolate';
import 'package:flutter/material.dart';
import 'package:speech_to_text/speech_to_text.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:vosk_flutter/vosk_flutter.dart' as vosk;

// Injected at build time: flutter build apk --dart-define=GEMINI_API_KEY=xxx
const String geminiApiKey = String.fromEnvironment('GEMINI_API_KEY');
const String wakePhrase = 'hey buddy';
const String geminiModel = 'gemini-2.5-flash';
const String systemPrompt =
    'You are Buddy, a helpful voice assistant. Plain text only, no markdown. '
    'Keep answers short, max 2 sentences, conversational.';
// Downloaded into assets/models/ by the GitHub Actions workflow.
const String voskModelAsset = 'assets/models/vosk-model-small-en-us-0.15.zip';

// ---- Foreground service: keeps the process (and mic) alive in background ----
@pragma('vm:entry-point')
void startCallback() {
  FlutterForegroundTask.setTaskHandler(_KeepAliveHandler());
}

class _KeepAliveHandler extends TaskHandler {
  // flutter_foreground_task 6.x signatures (SendPort-based).
  @override
  Future<void> onStart(DateTime timestamp, SendPort? sendPort) async {}
  @override
  Future<void> onRepeatEvent(DateTime timestamp, SendPort? sendPort) async {}
  @override
  Future<void> onDestroy(DateTime timestamp, SendPort? sendPort) async {}
}

void main() => runApp(const BuddyApp());

class BuddyApp extends StatelessWidget {
  const BuddyApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: ThemeData.dark(),
        home: const AssistantPage(),
      );
}

class AssistantPage extends StatefulWidget {
  const AssistantPage({super.key});
  @override
  State<AssistantPage> createState() => _AssistantPageState();
}

class _AssistantPageState extends State<AssistantPage> {
  // Command capture (Android speech recognizer, used only after the wake word)
  final SpeechToText _stt = SpeechToText();
  final FlutterTts _tts = FlutterTts();

  // Wake word (Vosk, offline, no beeps)
  final vosk.VoskFlutterPlugin _vosk = vosk.VoskFlutterPlugin.instance();
  vosk.Model? _voskModel;
  vosk.SpeechService? _speech;
  StreamSubscription<String>? _partialSub;
  StreamSubscription<String>? _resultSub;

  String _status = 'Tap START LISTENING';
  String _lastHeard = '';
  String _lastReply = '';
  bool _running = false;
  bool _busy = false; // handling a wake word
  Completer<String>? _cmdCompleter;
  DateTime _ignoreWakeUntil = DateTime.fromMillisecondsSinceEpoch(0);

  @override
  void initState() {
    super.initState();
    _tts.setLanguage('en-US');
    _tts.setSpeechRate(0.5);
    _tts.awaitSpeakCompletion(true);
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'buddy_channel',
        channelName: 'Buddy Assistant',
        channelDescription: 'Keeps Buddy listening',
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: const ForegroundTaskOptions(
        interval: 60000, // nothing to repeat; keep wakeups rare
        isOnceEvent: false,
        autoRunOnBoot: false,
        allowWakeLock: true,
        allowWifiLock: false,
      ),
    );
  }

  void _setStatus(String s) {
    if (mounted) setState(() => _status = s);
  }

  // ---------- start / stop ----------
  Future<void> _start() async {
    if (geminiApiKey.isEmpty) {
      _setStatus('No API key. Add GEMINI_API_KEY secret and rebuild.');
      return;
    }
    final mic = await Permission.microphone.request();
    await Permission.notification.request();
    if (!mic.isGranted) {
      _setStatus('Microphone permission denied');
      return;
    }
    if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
      await FlutterForegroundTask.requestIgnoreBatteryOptimization();
    }

    final sttOk = await _stt.initialize(
      onError: (e) {
        final c = _cmdCompleter;
        if (c != null && !c.isCompleted) c.complete('');
      },
    );
    if (!sttOk) {
      _setStatus('Speech recognition unavailable');
      return;
    }

    _setStatus('Loading voice model...');
    try {
      if (_voskModel == null) {
        final path = await vosk.ModelLoader().loadFromAssets(voskModelAsset);
        _voskModel = await _vosk.createModel(path);
      }
      // Grammar limits Vosk to the wake phrase: fast, light, accurate.
      final recognizer = await _vosk.createRecognizer(
        model: _voskModel!,
        sampleRate: 16000,
        grammar: [wakePhrase, '[unk]'],
      );
      _speech = await _vosk.initSpeechService(recognizer);
    } catch (e) {
      _setStatus('Voice model failed to load: $e');
      return;
    }

    await FlutterForegroundTask.startService(
      notificationTitle: 'Buddy is listening',
      notificationText: 'Say "Hey Buddy"',
      callback: startCallback,
    );

    _partialSub = _speech!.onPartial().listen(_onVoskText);
    _resultSub = _speech!.onResult().listen(_onVoskText);
    _running = true;
    await _speech!.start();
    if (mounted) setState(() => _status = 'Say "Hey Buddy"...');
  }

  Future<void> _stop() async {
    _running = false;
    final c = _cmdCompleter;
    if (c != null && !c.isCompleted) c.complete('');
    await _partialSub?.cancel();
    await _resultSub?.cancel();
    _partialSub = null;
    _resultSub = null;
    try {
      await _speech?.stop();
      await _speech?.dispose();
    } catch (_) {}
    _speech = null;
    await _stt.cancel();
    await _tts.stop();
    await FlutterForegroundTask.stopService();
    _busy = false;
    _setStatus('Stopped');
  }

  // ---------- wake word ----------
  void _onVoskText(String json) {
    String text = '';
    try {
      final m = jsonDecode(json) as Map<String, dynamic>;
      text = ((m['partial'] ?? m['text'] ?? '') as String).toLowerCase();
    } catch (_) {
      return;
    }
    if (text.isEmpty) return;
    if (DateTime.now().isBefore(_ignoreWakeUntil)) return;
    if (mounted && !_busy) setState(() => _lastHeard = text);
    if (text.contains(wakePhrase)) _onWake();
  }

  Future<void> _onWake() async {
    if (_busy || !_running) return;
    _busy = true;
    try {
      await _speech?.stop(); // release the mic for the Android recognizer
      _setStatus("Yes? I'm listening...");
      await _tts.speak('Yes?');
      await Future.delayed(const Duration(milliseconds: 300));
      final command = await _captureCommand();
      if (command.trim().isNotEmpty) {
        if (mounted) setState(() { _lastHeard = command; _status = 'Thinking...'; });
        final reply = await _askGemini(command);
        if (mounted) setState(() { _lastReply = reply; _status = 'Speaking...'; });
        await _tts.speak(reply);
      }
    } catch (_) {
      // fall through and resume wake listening
    } finally {
      if (_running) {
        try {
          await Future.delayed(const Duration(milliseconds: 300));
          _ignoreWakeUntil = DateTime.now().add(const Duration(milliseconds: 1200));
          await _speech?.reset();
          await _speech?.start();
        } catch (_) {}
        _setStatus('Say "Hey Buddy"...');
      }
      _busy = false;
    }
  }

  Future<String> _captureCommand() async {
    final completer = Completer<String>();
    _cmdCompleter = completer;
    _stt.listen(
      onResult: (r) {
        if (r.finalResult && !completer.isCompleted) {
          completer.complete(r.recognizedWords);
        }
      },
      listenFor: const Duration(seconds: 12),
      pauseFor: const Duration(seconds: 3),
    );
    try {
      return await completer.future.timeout(
        const Duration(seconds: 15),
        onTimeout: () => '',
      );
    } finally {
      _cmdCompleter = null;
      await _stt.stop();
    }
  }

  Future<String> _askGemini(String prompt) async {
    try {
      final uri = Uri.parse(
          'https://generativelanguage.googleapis.com/v1beta/models/$geminiModel:generateContent');
      final res = await http
          .post(
            uri,
            headers: {
              'Content-Type': 'application/json',
              'x-goog-api-key': geminiApiKey,
            },
            body: jsonEncode({
              'systemInstruction': {
                'parts': [
                  {'text': systemPrompt}
                ]
              },
              'contents': [
                {
                  'role': 'user',
                  'parts': [
                    {'text': prompt}
                  ]
                }
              ],
              'generationConfig': {
                'maxOutputTokens': 300,
                'temperature': 0.7,
                'thinkingConfig': {'thinkingBudget': 0},
              },
            }),
          )
          .timeout(const Duration(seconds: 20));

      if (res.statusCode == 429) {
        return "I'm being rate limited. Try again in a minute.";
      }
      if (res.statusCode == 400 || res.statusCode == 403) {
        return 'There is a problem with the API key.';
      }
      if (res.statusCode != 200) {
        return 'Sorry, I had a problem connecting. Try again.';
      }
      final data = jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>;
      final parts = (data['candidates'] as List).first['content']['parts'] as List;
      final text = parts
          .map((p) => (p['text'] ?? '') as String)
          .join(' ')
          .replaceAll(RegExp(r'[*#_`]'), '')
          .trim();
      return text.isEmpty ? "Sorry, I didn't get that." : text;
    } catch (e) {
      return 'Sorry, I had a problem connecting. Try again.';
    }
  }

  @override
  void dispose() {
    _running = false;
    _partialSub?.cancel();
    _resultSub?.cancel();
    _speech?.dispose();
    _stt.cancel();
    _tts.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return WithForegroundTask(
      child: Scaffold(
        appBar: AppBar(title: const Text('Buddy - AI Assistant')),
        body: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            children: [
              Icon(Icons.mic, size: 100, color: _running ? Colors.green : Colors.grey),
              const SizedBox(height: 20),
              Text(_status, style: const TextStyle(fontSize: 18), textAlign: TextAlign.center),
              const SizedBox(height: 20),
              Text('Heard: $_lastHeard'),
              const SizedBox(height: 10),
              Text('Reply: $_lastReply'),
              const SizedBox(height: 30),
              ElevatedButton(
                onPressed: _running ? _stop : _start,
                child: Text(_running ? 'STOP' : 'START LISTENING'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
