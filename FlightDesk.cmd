@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"

where python >nul 2>nul
if errorlevel 1 (
  echo Flight Desk needs Python 3.11 or newer.
  echo Install it from https://www.python.org/downloads/windows/ with "Add python.exe to PATH".
  pause
  exit /b 1
)

if /i "%~1"=="--watch-usb" goto watch

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Flight Desk needs Python 3.11 or newer.
  pause
  exit /b 1
)

echo Starting Flight Desk...
python -m flightdesk serve %*
if errorlevel 1 (
  echo.
  echo Flight Desk stopped with an error. Run "python -m flightdesk selftest" for details.
  pause
)
exit /b 0

:watch
echo Flight Desk USB watch started in the background.
start "" /min python -m flightdesk serve --no-browser --watch-usb
exit /b 0
