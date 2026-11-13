#!/usr/bin/env bash
# Runs on an Android emulator in CI: installs the APK, starts the app, lets Python download one
# file through the real engine (in-process), and checks the app's own screen answers.
set -uo pipefail
apk="$1"
out="${GITHUB_WORKSPACE:-.}"

mkdir -p /tmp/served
printf 'fake jpeg bytes' > /tmp/served/test.jpg
(cd /tmp/served && nohup python3 -m http.server 8000 > /dev/null 2>&1 &)
sleep 1

adb install -r "$apk" || exit 1
adb logcat -c
adb shell am start -n com.juw12.socialsaver/.MainActivity --es selftest_url http://10.0.2.2:8000/test.jpg

for _ in $(seq 1 100); do
    if adb logcat -d -s SocialSaver | grep -qE "SELFTEST|SOCIAL_SAVER_FAILED"; then
        break
    fi
    sleep 3
done
sleep 5
adb exec-out screencap -p > "$out/android-screen.png" || true
adb logcat -d -s SocialSaver python.stdout python.stderr | tail -n 80 | tee "$out/android-log.txt"

adb logcat -d -s SocialSaver | grep -q "SELFTEST OK" || { echo "self-test did not pass"; exit 1; }
url=$(adb logcat -d -s SocialSaver | grep -o "SOCIAL_SAVER_READY http://[^ ]*" | tail -n 1 | cut -d" " -f2)
port=$(echo "$url" | sed -E 's#http://127\.0\.0\.1:([0-9]+)/.*#\1#')
query="${url#*\?}"
adb forward tcp:9100 "tcp:$port" || exit 1
curl -fsS "http://127.0.0.1:9100/healthz" || exit 1
curl -fsS "http://127.0.0.1:9100/?$query" | grep -q 'const MODE = "android"' || { echo "screen not served"; exit 1; }
echo "EMULATOR TEST PASSED"
