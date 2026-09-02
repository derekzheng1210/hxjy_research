@echo off
rem One-time helper: register the daily Wind push task in Windows Task Scheduler.
rem Default run time 09:00, change /ST as needed. Requires the .venv in this repo.
schtasks /Create /TN "juyuan_wind_push_daily" /TR "%~dp0wind_push_daily.bat" /SC DAILY /ST 09:00 /F
schtasks /Query /TN "juyuan_wind_push_daily"
pause
