@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   LightGuard dataset capture
echo   (press ENTER for each sample; close window to stop)
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File ".\capture_dataset.ps1" -DryRun
echo.
echo ---- finished, press any key to close ----
pause >nul