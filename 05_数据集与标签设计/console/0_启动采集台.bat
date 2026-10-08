@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   LightGuard dataset console (standalone)
echo   Browser: http://127.0.0.1:8789/   (use 127.0.0.1, not a LAN IP)
echo   Ctrl+C to stop (the strip is turned off automatically)
echo ============================================================
echo.
py -3.11 dataset_console.py %*
echo.
echo [exit] console stopped.
pause
