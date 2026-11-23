#!/bin/bash
# Double-click to open Social Saver on a Mac. The first run makes a private Python environment in this
# folder (.venv) and installs what Social Saver needs; later runs start at once.
cd "$(dirname "$0")" || exit 1

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3.10 python3 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        found=$(command -v "$candidate" 2>/dev/null) || continue
        if "$found" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            echo "$found"
            return 0
        fi
    done
    return 1
}

if [ ! -x .venv/bin/python ]; then
    python=$(find_python)
    if [ -z "$python" ]; then
        echo "Social Saver needs Python 3.10 or newer, and this Mac has none."
        echo "Get it from https://www.python.org/downloads/ (the macOS installer), then double-click this file again."
        open "https://www.python.org/downloads/"
        read -r -p "Press Return to close this window. "
        exit 1
    fi
    echo "First run: installing what Social Saver needs, please wait..."
    "$python" -m venv .venv || { echo "Could not make the Python environment."; read -r -p "Press Return to close. "; exit 1; }
fi

if ! .venv/bin/python -c "import flask, requests, webview, gallery_dl, yt_dlp, segno" 2>/dev/null; then
    .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt || {
        echo "Install failed. Check the internet connection and double-click this file again."
        read -r -p "Press Return to close this window. "
        exit 1
    }
fi

exec .venv/bin/python app.py "$@"
