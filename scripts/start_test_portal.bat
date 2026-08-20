@echo off
REM 启动隔离的测试版门户（端口 5010，独立数据目录 .test_runtime）
REM 正式服务（CreditToolsPortal，端口 5000）不受影响。
REM 用法：
REM   scripts\init_test_runtime.bat   首次运行或需要重置测试数据时先执行
REM   scripts\start_test_portal.bat   启动测试实例
setlocal
chcp 65001 >nul
set "TEST_DIR=%~dp0.."
for %%i in ("%TEST_DIR%") do set "TEST_DIR=%%~fi"

REM 复用正式环境的虚拟环境解释器（无需另装依赖）
set "PYTHON=D:\信用债研究\完整网页内容\juyuan_credit_tools_portal\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [错误] 未找到正式环境 python：%PYTHON%
    pause
    exit /b 1
)

set "PORT=5010"
set "FLASK_DEBUG=0"
set "PORTAL_DATA_ROOT=%TEST_DIR%\.test_runtime"

if not exist "%TEST_DIR%\.env" (
    > "%TEST_DIR%\.env" (
        echo PORTAL_DATA_ROOT=%TEST_DIR%\.test_runtime
        echo SITE_PASSWORD=test2026
    )
    echo [提示] 已生成测试 .env（站点密码 test2026），可按需编辑：%TEST_DIR%\.env
)

cd /d "%TEST_DIR%"
echo 测试实例启动中：http://localhost:5010/internal-knowledge-base/
echo 按 Ctrl+C 停止。
"%PYTHON%" app.py
pause
