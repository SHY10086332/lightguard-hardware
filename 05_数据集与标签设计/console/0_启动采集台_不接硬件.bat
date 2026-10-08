@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   LightGuard dataset console  --  NO HARDWARE MODE
echo   (lux is left blank; everything else works, good for practice)
echo   Browser: http://127.0.0.1:8789/
echo ============================================================
echo.
py -3.11 dataset_console.py --no-serial %*
echo.
echo [exit] console stopped.
pause
