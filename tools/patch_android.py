"""Patches the generated android/ project so old plugins (vosk_flutter,
permission_handler 10.x) build with AGP 8 / Flutter 3.24. Idempotent."""
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
        // Never go above 34: AGP 8.1's aapt2 cannot read the android-35 platform jar.
        // Old plugins that compile against < 34 are raised to 34.
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
            // androidx/other libs may ship newer Kotlin metadata than the compiler expects.
            try {
                t.kotlinOptions.freeCompilerArgs = t.kotlinOptions.freeCompilerArgs + ['-Xskip-metadata-version-check']
            } catch (Throwable ignored) {}
        }
    }
}
// --- end buddy ---
'''
if os.path.exists(ROOT_GRADLE):
    r = open(ROOT_GRADLE).read()
    if MARK in r:  # drop any older copy of our block, then re-insert the current one
        r = re.sub(re.escape(MARK) + r'.*?// --- end buddy ---\n', '', r, flags=re.S)
    open(ROOT_GRADLE, 'w').write(HOOK + '\n' + r)
    print('root build.gradle patched')
else:
    print('ERROR: android/build.gradle not found (Kotlin DSL?). Pin Flutter 3.24.0.', file=sys.stderr)
    sys.exit(1)

# ---------- 4. Kotlin Gradle plugin: template's 1.8.22 is too old for these plugins ----------
KOTLIN = '1.9.24'
done = False
for f in ('android/settings.gradle', 'android/settings.gradle.kts'):
    if os.path.exists(f):
        t = open(f).read()
        t2 = re.sub(r'(org\.jetbrains\.kotlin\.android["\']\)?\s*version\s*["\'])[0-9][^"\']*(["\'])',
                    r'\g<1>' + KOTLIN + r'\g<2>', t)
        open(f, 'w').write(t2)
        print('kotlin plugin ->', KOTLIN, '(' + f + '):', 'changed' if t != t2 else 'unchanged/not found')
        done = done or (KOTLIN in t2)
if os.path.exists(ROOT_GRADLE):  # pre-3.19 style projects
    t = open(ROOT_GRADLE).read()
    t2 = re.sub(r"ext\.kotlin_version\s*=\s*['\"][^'\"]+['\"]", "ext.kotlin_version = '" + KOTLIN + "'", t)
    if t != t2:
        open(ROOT_GRADLE, 'w').write(t2)
        print('ext.kotlin_version ->', KOTLIN)
        done = True
if not done:
    print('WARNING: could not find the Kotlin plugin version to bump', file=sys.stderr)

# ---------- 5. gradle.properties ----------
GP = 'android/gradle.properties'
props = open(GP).read() if os.path.exists(GP) else ''
for line in ('android.defaults.buildfeatures.buildconfig=true',):  # old plugins that use BuildConfig
    if line.split('=')[0] not in props:
        props += ('' if props.endswith('\n') or not props else '\n') + line + '\n'
open(GP, 'w').write(props)
print('gradle.properties patched')
