@echo off
cd /d "%~dp0"
title Avatar Democracy server
echo =============================================
echo    Avatar Democracy
echo    Starting server on http://127.0.0.1:8000
echo    Leave this window open. Close it to STOP.
echo =============================================
echo.

REM Demo-friendly mode: no cooling-off delays, so delegates activate and bills
REM advance to voting immediately. Delete the next three lines for faithful timings.
set "BILL_COOLING_OFF_SECONDS=0"
set "DELEGATE_COOLING_OFF_SECONDS=0"
set "EMERGENCY_COOLING_OFF_SECONDS=0"

REM Open the browser a few seconds after the server comes up.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8000'"

REM Use the py launcher: plain "python" can resolve to an unrelated venv on PATH.
py -3 main.py

echo.
echo Server stopped.
pause