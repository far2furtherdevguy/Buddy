# Buddy Assistant v2.0.1 (Vosk wake word)

1. Create a GitHub repo, upload everything in this folder (keep the hidden `.github` folder).
2. Repo Settings -> Secrets and variables -> Actions -> New secret: `GEMINI_API_KEY` (free key from aistudio.google.com).
3. Push to `main` (or Actions -> Build APK -> Run workflow). The workflow downloads the free Vosk model (~40 MB) automatically.
4. Download `buddy-apk` from the finished run, install, tap START LISTENING, say "Hey Buddy", wait for "Yes?", then ask.

Notes:
- `permission_handler` and `http` are pinned to older lines because `vosk_flutter` requires them. Do not bump them.
- Gemini is called over plain REST (no Google SDK), model set by `geminiModel` in `lib/main.dart`.
