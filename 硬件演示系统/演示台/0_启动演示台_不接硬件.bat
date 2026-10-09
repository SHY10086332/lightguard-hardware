@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   LightGuard demo console  --  NO HARDWARE MODE
echo   (UI and detection still work; lux stays blank)
echo   Browser: http://127.0.0.1:8790/
echo ============================================================
echo.
py -3.11 demo_console.py --no-serial %*
echo.
echo [exit] demo console stopped.
pause
