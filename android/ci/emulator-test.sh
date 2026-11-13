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

# Wait until Python finished the self-test AND the screen's JavaScript loaded the app state.
for _ in $(seq 1 100); do
    log=$(adb logcat -d -s SocialSaver)
    if echo "$log" | grep -q "SOCIAL_SAVER_FAILED"; then
        break
    fi
    if echo "$log" | grep -q "SELFTEST" && echo "$log" | grep -q "SOCIAL_SAVER_UI_READY"; then
        break
    fi
    sleep 3
done
sleep 5
adb exec-out screencap -p > "$out/android-screen.png" || true
adb logcat -d -s SocialSaver python.stdout python.stderr | tail -n 80 | tee "$out/android-log.txt"

check_app() {
    adb logcat -d -s SocialSaver | grep -q "SELFTEST OK" || { echo "self-test did not pass"; return 1; }
    adb logcat -d -s SocialSaver | grep -q "SOCIAL_SAVER_UI_READY" || { echo "screen did not load its state"; return 1; }
    if adb logcat -d -s SocialSaver | grep -q "console: Uncaught"; then
        echo "JavaScript error on the screen"
        return 1
    fi
    url=$(adb logcat -d -s SocialSaver | grep -o "SOCIAL_SAVER_READY http://[^ ]*" | tail -n 1 | cut -d" " -f2)
    port=$(echo "$url" | sed -E 's#http://127\.0\.0\.1:([0-9]+)/.*#\1#')
    query="${url#*\?}"
    adb forward tcp:9100 "tcp:$port" || return 1
    curl -fsS "http://127.0.0.1:9100/healthz" || return 1
    curl -fsS "http://127.0.0.1:9100/?$query" | grep -q 'const MODE = "android"' || { echo "screen not served"; return 1; }
    echo "EMULATOR TEST PASSED"
}
check_app
result=$?

# Stop everything ourselves: the emulator runner can hang for a long time while it waits for
# the emulator to exit, so kill the test server and the emulator here.
pkill -f "http.server 8000" || true
adb emu kill || true
sleep 5
pkill -9 -f qemu-system || true
exit $result
