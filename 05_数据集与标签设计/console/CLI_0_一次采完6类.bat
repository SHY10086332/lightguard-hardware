@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   One-click capture for ALL 6 classes  (no browser needed)
echo   Step: put ONE sheet under the camera, press ENTER.
echo   One ENTER = about 4 labels saved.
echo   Strip turns off automatically at the end of each class.
echo ============================================================
echo.
py -3.11 cli_capture.py --class normal --count 40 --brightness 25 --columns 2,3
echo.
echo --- Next class: BROKENLINE (duan xian). Get those sheets ready. ---
pause >nul
py -3.11 cli_capture.py --class brokenline --count 20 --brightness 25 --columns 2,3
echo.
echo --- Next class: FAINT (que mo) ---
pause >nul
py -3.11 cli_capture.py --class faint --count 20 --brightness 25 --columns 2,3
echo.
echo --- Next class: STAIN (wu dian) ---
pause >nul
py -3.11 cli_capture.py --class stain --count 20 --brightness 25 --columns 2,3
echo.
echo --- Next class: SCRATCH (hua hen) ---
pause >nul
py -3.11 cli_capture.py --class scratch --count 20 --brightness 25 --columns 2,3
echo.
echo --- Next class: OFFSET (pian wei chong ying) ---
pause >nul
py -3.11 cli_capture.py --class offset --count 20 --brightness 25 --columns 2,3
echo.
echo ============================================================
echo   ALL DONE. Dataset is in .\dataset\images\
echo   Tell the agent: capture finished.
echo ============================================================
pause
