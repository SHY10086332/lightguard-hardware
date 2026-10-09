@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p CLS=Class (normal/brokenline/faint/stain/scratch/offset):
set /p CNT=Target count (e.g. 20):
echo.
echo Auto mode: one shot every 8 seconds. Just keep swapping sheets.
echo Ctrl+C to stop.
echo.
py -3.11 cli_capture.py --class %CLS% --count %CNT% --brightness 25 --columns 2,3 --auto 8
pause
