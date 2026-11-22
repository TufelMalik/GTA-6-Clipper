@echo off
cd /d "%~dp0"
echo Starting Clip Desk server on http://127.0.0.1:5000...
start "" "http://127.0.0.1:5000"
python server.py
pause
