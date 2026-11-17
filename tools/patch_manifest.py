p = 'android/app/src/main/AndroidManifest.xml'
s = open(p).read()

perms = ['RECORD_AUDIO', 'INTERNET', 'FOREGROUND_SERVICE',
         'FOREGROUND_SERVICE_MICROPHONE', 'POST_NOTIFICATIONS', 'WAKE_LOCK',
         'REQUEST_IGNORE_BATTERY_OPTIMIZATIONS']
add = ''.join(
    f'    <uses-permission android:name="android.permission.{x}"/>\n'
    for x in perms if f'android.permission.{x}"' not in s)
if add:
    s = s.replace('<application', add + '    <application', 1)

svc = ('<service android:name="com.pravera.flutter_foreground_task.service.ForegroundService" '
       'android:foregroundServiceType="microphone" android:exported="false"/>')
if 'ForegroundService' not in s:
    s = s.replace('</application>', '        ' + svc + '\n    </application>')

# speech recognition visibility (Android 11+)
intent = '<intent><action android:name="android.speech.RecognitionService"/></intent>'
if 'android.speech.RecognitionService' not in s:
    if '<queries>' in s:
        s = s.replace('<queries>', '<queries>\n        ' + intent, 1)
    else:
        s = s.replace('</manifest>', '    <queries>\n        ' + intent + '\n    </queries>\n</manifest>')

open(p, 'w').write(s)
print('manifest patched')
