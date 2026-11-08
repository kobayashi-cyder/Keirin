@echo off
setlocal
cd /d "%~dp0"
echo Kdreams site terms restrict unauthorized copying and reuse.
echo Taking responsibility does NOT provide operator permission or eliminate risk.
echo No bypass of robots or access controls; monitoring stops on restrictions.
echo Run only after reviewing applicable terms and deciding on your own use.
set "CONSENT="
set /p "CONSENT=Type I_AGREE to acknowledge the site terms and risks: "
if /I not "%CONSENT%"=="I_AGREE" (
  echo Terms/risk acknowledgment missing. No site requests sent.
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
py -3 tools\browser_odds_watch.py --url "%RACE_URL%" --deadline-jst "%DEADLINE%" --cars "%CARS%" --interval 10 --cycles 60 --acknowledge-terms-and-risks
echo Monitoring finished or stopped. See messages above.
pause
