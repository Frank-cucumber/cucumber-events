@echo off
cd /d "C:\Users\joe\Downloads\Events project"
if not exist logs mkdir logs

:: API tokens loaded from .env by app.py on startup

:: Start Flask server
start "" /B python app.py > logs\flask.log 2>&1
timeout /t 4 /nobreak > nul

:: Start tunnel and capture URL, then update GitHub Pages redirect
python update_tunnel.py
