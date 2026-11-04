@echo off
cd /d "%~dp0"
title Avatar Democracy LAN server
echo =============================================
echo    Avatar Democracy LAN server
echo    Starts on http://0.0.0.0:8000
echo    Use your PC IPv4 address from the list below
echo    on Android, e.g. http://192.168.1.50:8000
echo =============================================
echo.

for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4 Address" /c:"IPv4-adresse"') do echo PC address:%%A:8000
echo.

set "HOST=0.0.0.0"
set "PORT=8000"
set "BILL_COOLING_OFF_SECONDS=0"
set "DELEGATE_COOLING_OFF_SECONDS=0"
set "EMERGENCY_COOLING_OFF_SECONDS=0"

REM Use the py launcher: plain "python" can resolve to an unrelated venv on PATH.
py -3 main.py

echo.
echo Server stopped.
pause
