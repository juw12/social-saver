@echo off
rem Double-click to open Social Saver. The first run installs what it needs.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python is not installed. Get it from https://www.python.org/downloads/ and run this again.
    pause
    exit /b 1
)

python -c "import flask, requests, webview, gallery_dl, yt_dlp" >nul 2>nul
if errorlevel 1 (
    echo First run: installing what Social Saver needs, please wait...
    python -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Install failed. Check the internet connection and run this again.
        pause
        exit /b 1
    )
)

start "" pythonw app.py
