@echo off
rem Double-click to open Social Saver. The first run installs what it needs (also Python, if missing).
cd /d "%~dp0"
set "PY312=%LOCALAPPDATA%\Programs\Python\Python312"
if exist "%PY312%\python.exe" set "PATH=%PY312%;%PY312%\Scripts;%PATH%"

rem The "python" Microsoft Store shortcut is not real Python, so ask Python for its version instead.
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Social Saver needs Python 3.10 or newer. Installing it now, please wait...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    if exist "%PY312%\python.exe" set "PATH=%PY312%;%PY312%\Scripts;%PATH%"
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo Could not install Python by itself. Get it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH", then double-click this file again.
        pause
        exit /b 1
    )
)

python -c "import flask, requests, webview, gallery_dl, yt_dlp, segno" >nul 2>nul
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
