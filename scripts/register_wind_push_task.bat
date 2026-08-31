@echo off
rem One-time helper: register the daily Wind push task in Windows Task Scheduler.
rem Default run time 17:30, change /ST as needed. Requires the .venv in this repo.
schtasks /Create /TN "juyuan_wind_push_daily" /TR "%~dp0wind_push_daily.bat" /SC DAILY /ST 17:30 /F
schtasks /Query /TN "juyuan_wind_push_daily"
pause
