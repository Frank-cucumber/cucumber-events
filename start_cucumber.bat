@echo off
cd /d "C:\Users\joe\Downloads\Events project"
if not exist logs mkdir logs

:: API tokens
set NANO_BANANA_KEY=AIzaSyDTthx8BsgWIz3vJ8sFjcHQ_Tz2HgGnSBM

:: Start Flask server
start "" /B python app.py > logs\flask.log 2>&1
timeout /t 4 /nobreak > nul

:: Start tunnel and capture URL, then update GitHub Pages redirect
python update_tunnel.py
