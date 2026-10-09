@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   Lux calibration (re-run after moving the sensor!)
echo   Default target: 900 lx.  It turns the strip OFF when done.
echo ============================================================
echo.
py -3.11 calibrate_lux.py %*
echo.
pause
