@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   LightGuard dataset capture - Web UI
echo ============================================
echo.
echo Starting local server: http://127.0.0.1:8788/
start "LightGuard Capture Server" /min node "%~dp0web\server.js"
timeout /t 2 >nul
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "%CHROME%" ( start "" "%CHROME%" "http://127.0.0.1:8788/" ) else ( start "" "http://127.0.0.1:8788/" )
exit