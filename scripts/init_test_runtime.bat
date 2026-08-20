@echo off
REM 初始化/重置测试运行时数据（从生产数据目录复制快照，生产目录只读不受影响）
REM --db-only：仅刷新数据库（重置测试期改动，不重新复制上传文件）
setlocal
chcp 65001 >nul
set "TEST_DIR=%~dp0.."
for %%i in ("%TEST_DIR%") do set "TEST_DIR=%%~fi"
set "PYTHON=D:\信用债研究\完整网页内容\juyuan_credit_tools_portal\.venv\Scripts\python.exe"
"%PYTHON%" "%TEST_DIR%\scripts\init_test_runtime.py" %*
pause
