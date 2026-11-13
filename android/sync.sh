#!/usr/bin/env bash
# Copies the Python app and its screen into the Android project (run before building).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
python_dir="$here/app/src/main/python"
web_dir="$here/app/src/main/assets/web"
rm -rf "$python_dir" "$web_dir"
mkdir -p "$python_dir" "$web_dir"
cp "$repo/app.py" "$python_dir/"
cp -r "$repo/saver" "$python_dir/"
find "$python_dir" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp "$repo/ui.html" "$web_dir/"
cp -r "$repo/static" "$web_dir/"
echo "Copied app.py, saver/, ui.html and static/ into the Android project."
