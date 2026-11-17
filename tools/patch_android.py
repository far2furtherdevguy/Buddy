"""Patches the generated android/ project so old plugins (vosk_flutter,
permission_handler 10.x) build with AGP 8 / Flutter 3.24. Idempotent.

Also supplies ext.flutter so speech_to_text 7.x (and similar plugins) can
resolve flutter.compileSdkVersion / flutter.ndkVersion on Flutter 3.24."""
import os, re, sys

MANIFEST = 'android/app/src/main/AndroidManifest.xml'
ROOT_GRADLE = 'android/build.gradle'
APP_GRADLE = 'android/app/build.gradle'
APP_GRADLE_KTS = 'android/app/build.gradle.kts'
MARK = '// --- buddy: injected by tools/patch_android.py ---'

# ---------- 1. AndroidManifest ----------
s = open(MANIFEST).read()
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

intent = '<intent><action android:name="android.speech.RecognitionService"/></intent>'
if 'android.speech.RecognitionService' not in s:
    if '<queries>' in s:
        s = s.replace('<queries>', '<queries>\n        ' + intent, 1)
    else:
        s = s.replace('</manifest>', '    <queries>\n        ' + intent + '\n    </queries>\n</manifest>')
open(MANIFEST, 'w').write(s)
print('manifest patched')

# ---------- 2. minSdk 24 (speech_to_text / foreground_task / vosk need >= 21-24) ----------
if os.path.exists(APP_GRADLE):
    g = open(APP_GRADLE).read()
    g2 = re.sub(r'minSdk(?:Version)?\s*=?\s*flutter\.minSdkVersion', 'minSdkVersion 24', g)
    open(APP_GRADLE, 'w').write(g2)
    print('minSdk patched (groovy):', g != g2)
elif os.path.exists(APP_GRADLE_KTS):
    g = open(APP_GRADLE_KTS).read()
    g2 = re.sub(r'minSdk\s*=\s*flutter\.minSdkVersion', 'minSdk = 24', g)
    open(APP_GRADLE_KTS, 'w').write(g2)
    print('minSdk patched (kts):', g != g2)

# ---------- 3. root build.gradle: make old plugins AGP-8 compatible ----------
HOOK = MARK + '''
ext.flutter = [
    compileSdkVersion: 34,
    minSdkVersion: 24,
    targetSdkVersion: 34,
    ndkVersion: "25.1.8937393"
]

allprojects {
    repositories {
        maven { url 'https://alphacephei.com/maven/' }
    }
}
subprojects { sub ->
    sub.afterEvaluate {
        def androidExt = sub.extensions.findByName('android')
        if (androidExt == null) return
        // AGP 8 requires a namespace; old plugins only declare package= in their manifest.
        try {
            if (!androidExt.namespace) {
                def mf = sub.file('src/main/AndroidManifest.xml')
                if (mf.exists()) {
                    def pkg = new XmlSlurper().parse(mf).@package.text()
                    if (pkg) androidExt.namespace = pkg
                }
            }
        } catch (Throwable ignored) {}
        // Plugins compiled against SDK 33 break against newer androidx.
        try {
            if ((androidExt.compileSdk ?: 0) < 34) androidExt.compileSdk = 34
        } catch (Throwable ignored) {}
        // Keep Java and Kotlin JVM targets identical (avoids "Inconsistent JVM-target").
        try {
            androidExt.compileOptions.sourceCompatibility = JavaVersion.VERSION_17
            androidExt.compileOptions.targetCompatibility = JavaVersion.VERSION_17
        } catch (Throwable ignored) {}
        sub.tasks.matching { it.class.name.contains('KotlinCompile') }.configureEach { t ->
            try { t.kotlinOptions.jvmTarget = '17' } catch (Throwable ignored) {}
        }
    }
}
// --- end buddy ---
'''
if os.path.exists(ROOT_GRADLE):
    r = open(ROOT_GRADLE).read()
    if MARK not in r:
        open(ROOT_GRADLE, 'w').write(HOOK + '\n' + r)
        print('root build.gradle patched')
    else:
        print('root build.gradle already patched')
else:
    print('ERROR: android/build.gradle not found (Kotlin DSL?). Pin Flutter 3.24.0.', file=sys.stderr)
    sys.exit(1)
