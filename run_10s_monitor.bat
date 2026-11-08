@echo off
setlocal
cd /d "%~dp0"
echo This tool is for OPERATOR-AUTHORIZED local monitoring only.
echo Kdreams restricts unauthorized copying; browser access does not grant a license.
echo Before running, confirm explicit permission for this polling and data use.
set "CONSENT="
set /p "CONSENT=Type YES if you have permission (otherwise press Enter): "
if /I not "%CONSENT%"=="YES" (
  echo No permission confirmed. No site requests sent.
  pause
  exit /b 2
)
set "RACE_URL="
set /p "RACE_URL=Paste a FUTURE public race odds URL: "
set "DEADLINE="
set /p "DEADLINE=Voting deadline e.g. 2026-11-08T20:20:00+09:00: "
set "CARS=7"
set /p "CARS=Number of cars 7, 8 or 9 (default 7): "
if "%CARS%"=="" set "CARS=7"
echo Monitoring one race page no faster than every 10 seconds, up to 60 cycles.
py -3 tools\browser_odds_watch.py --url "%RACE_URL%" --deadline-jst "%DEADLINE%" --cars "%CARS%" --interval 10 --cycles 60 --permission-confirmed
echo Monitoring finished or stopped. See messages above.
pause
