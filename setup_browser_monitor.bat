@echo off
setlocal
cd /d "%~dp0"
echo Installing Playwright for this Windows Python...
py -3 -m pip install playwright
if errorlevel 1 goto :failed
py -3 -m playwright install chromium
if errorlevel 1 goto :failed
echo Setup complete.
pause
exit /b 0
:failed
echo Setup failed. Check Python and the error above.
pause
exit /b 1
