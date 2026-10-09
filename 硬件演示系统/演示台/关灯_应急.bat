@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   Emergency: turn the LED strip OFF (LIGHT 0)
echo   Reads the brightness back from the board to confirm.
echo ============================================================
echo.
py -3.11 light_off.py %*
echo.
pause
