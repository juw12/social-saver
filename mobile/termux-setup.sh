#!/data/data/com.termux/files/usr/bin/bash
# Social Saver on an Android phone, no PC needed.
# 1. Install Termux from F-Droid or GitHub (the Play Store build is too old).
# 2. In Termux run:
#    curl -fsSL https://raw.githubusercontent.com/juw12/social-saver/main/mobile/termux-setup.sh | bash
# Then: share a post or profile from Instagram, Threads, X or TikTok to "Termux" and it is saved,
# or type `social-saver` to open the full screen in your browser.
set -e

echo "== Social Saver: installing Python and git =="
pkg update -y
pkg install -y python git

echo "== Allow storage so the zips can go to Download/SocialSaver =="
if [ ! -d "$HOME/storage/downloads" ]; then
    termux-setup-storage || true
    sleep 4
fi

echo "== Installing the download engines =="
python -m pip install --upgrade gallery-dl yt-dlp flask requests segno

APP="$HOME/social-saver"
if [ -d "$APP/.git" ]; then
    git -C "$APP" pull --ff-only
else
    git clone --depth 1 https://github.com/juw12/social-saver "$APP"
fi

mkdir -p "$HOME/bin"
cat > "$HOME/bin/termux-url-opener" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
# Runs when a link is shared to Termux: save it with Social Saver.
cd "$HOME/social-saver" && python app.py --save "$1"
echo
read -n 1 -s -r -p "Press any key to close."
EOF
cat > "$HOME/bin/social-saver" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
# Opens Social Saver in the phone browser. Keep Termux open while it downloads.
cd "$HOME/social-saver" && git pull --ff-only -q 2>/dev/null; python app.py --serve
EOF
chmod +x "$HOME/bin/termux-url-opener" "$HOME/bin/social-saver"
grep -q 'HOME/bin' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"

# Let "Share to Termux" run the opener script.
mkdir -p "$HOME/.termux"
touch "$HOME/.termux/termux.properties"
grep -q '^allow-external-apps' "$HOME/.termux/termux.properties" \
    || echo 'allow-external-apps = true' >> "$HOME/.termux/termux.properties"
termux-reload-settings 2>/dev/null || true

echo
echo "Ready."
echo " - Share a post or profile link to Termux: it is saved to Download/SocialSaver."
echo "   (If sharing does nothing: Android Settings > Apps > Termux > allow 'Display over other apps'.)"
echo " - Or type: social-saver   (opens the full screen in your browser)"
