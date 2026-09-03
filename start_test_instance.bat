@echo off
REM Start the isolated test portal (port 5090, data root D:\hxjy_test_data).
REM Production service (CreditToolsPortal, port 5000, runs from D:\hxjy_research
REM against D:\juyuan_credit_data) is NOT affected.
REM Test data is a mirror snapshot of production: uploads/edits during testing
REM only touch the test data root.
REM
REM Run sync_test_data.bat first if you want to reset test data to the latest
REM production snapshot (files + sqlite DBs, takes a few minutes).
REM
REM Background schedulers:
REM - Broker quotes scheduler is ENABLED in the test instance so the DM account
REM   pool / rotation / anti-detection stack can be verified end to end. It only
REM   writes snapshots into the TEST data root (D:\hxjy_test_data).
REM - Interest bond monitors stay DISABLED (Oracle jobs): production keeps sole
REM   ownership of those; test pages read the snapshot.
setlocal
set "TEST_DIR=%~dp0"

set "PYTHON=D:\Python312\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] python not found: %PYTHON%
    pause
    exit /b 1
)

if not exist "%TEST_DIR%.env" (
    echo [ERROR] %TEST_DIR%.env not found.
    echo         Copy the production .env here and make sure its first line is
    echo         PORTAL_DATA_ROOT=D:\hxjy_test_data
    pause
    exit /b 1
)

set "PORT=5090"
set "FLASK_DEBUG=0"
set "PORTAL_DATA_ROOT=D:\hxjy_test_data"
set "BROKER_SCHEDULER_ENABLED=1"
set "BOND_MONITOR_SCHEDULERS_ENABLED=0"

cd /d "%TEST_DIR%"
echo Test portal starting: http://localhost:5090/
echo Site password: same as production (.env SITE_PASSWORD or the built-in default).
echo Press Ctrl+C to stop.
"%PYTHON%" run_production.py
pause
