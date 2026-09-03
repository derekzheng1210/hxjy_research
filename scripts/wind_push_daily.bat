@echo off
rem Daily scheduled runner: pull Wind data and push to the portal server.
rem Register with Windows Task Scheduler (see deploy/wind_push_setup.md):
rem   schtasks /Create /TN "juyuan_wind_push_daily" /TR "%~f0" /SC DAILY /ST 09:00 /F
cd /d "%~dp0.."
if not exist logs mkdir logs
echo ==================== %date% %time% ==================== >> logs\wind_push.log
".venv\Scripts\python.exe" scripts\push_ipm_wind.py >> logs\wind_push.log 2>&1
exit /b %errorlevel%
