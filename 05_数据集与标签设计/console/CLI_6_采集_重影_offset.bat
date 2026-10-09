@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   CLI capture (NO browser needed)   class: offset
echo   Put one label sheet, press ENTER.  Type q + ENTER to stop.
echo   The strip is turned OFF automatically when finished.
echo ============================================================
echo.
py -3.11 cli_capture.py --class offset --count 20 --brightness 25 --columns 2,3
echo.
echo [exit] capture finished.
pause
