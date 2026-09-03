@echo off
REM Mirror production data dir to test data dir (for the 5090 test instance).
REM Usage: double-click or run via cmd. Re-runnable (incremental).
REM NOTE: /MIR deletes test files that no longer exist in production; logs dir is NOT synced.
setlocal
set "SRC=D:\juyuan_credit_data"
set "DST=D:\hxjy_test_data"

if not exist "%SRC%\internal_knowledge_base\knowledge_base.db" (
    echo [ERROR] production db not found: %SRC%\internal_knowledge_base\knowledge_base.db
    exit /b 1
)

for %%d in (data interest_bond primary_market_pricing uploads internal_knowledge_base) do (
    REM /XF *.lock: scheduler.lock is held exclusively by the production scheduler process
    robocopy "%SRC%\%%d" "%DST%\%%d" /MIR /R:2 /W:2 /XF *.lock /NFL /NDL /NP /NJH
    if errorlevel 8 (
        echo [FAIL] %%d
        exit /b 1
    ) else (
        echo [OK] %%d
    )
)
copy /Y "%SRC%\page_visibility.json" "%DST%\page_visibility.json" >nul
echo [OK] page_visibility.json

set "PYTHON=D:\Python312\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0refresh_test_dbs.py"
) else (
    echo [WARN] python not found: %PYTHON% - DB snapshot refresh SKIPPED
)
echo [DONE] test data mirrored to %DST%
