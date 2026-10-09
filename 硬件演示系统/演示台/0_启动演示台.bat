@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   LightGuard demo console  (closed-loop light + verdict)
echo   Browser: http://127.0.0.1:8790/   (use 127.0.0.1, not a LAN IP)
echo   Ctrl+C to stop (strip turns off and indicator resets)
echo ============================================================
echo.
py -3.11 demo_console.py %*
echo.
echo [exit] demo console stopped.
pause
