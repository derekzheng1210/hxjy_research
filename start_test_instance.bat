@echo off
REM 启动隔离的测试版门户（端口 5090，独立数据目录 D:\hxjy_test_data）
REM 正式服务（CreditToolsPortal，端口 5000，读 D:\hxjy_research）不受影响。
REM 测试数据是正式库的快照副本：测试期间的上传/修改都只写测试库。
setlocal
chcp 65001 >nul
set "TEST_DIR=%~dp0"

REM 与正式服务相同的解释器
set "PYTHON=D:\Python312\python.exe"
if not exist "%PYTHON%" (
    echo [错误] 未找到 python：%PYTHON%
    pause
    exit /b 1
)

set "PORT=5090"
set "FLASK_DEBUG=0"
set "PORTAL_DATA_ROOT=D:\hxjy_test_data"

if not exist "%TEST_DIR%.env" (
    > "%TEST_DIR%.env" (
        echo PORTAL_DATA_ROOT=D:\hxjy_test_data
        echo SITE_PASSWORD=test2026
        echo SELF_LLM_API_KEY=sk-RM9HPWKHRXGmXS67qSBk3A
        echo SELF_LLM_BASE_URL=http://10.9.50.201:3005/v1
        echo SELF_LLM_MODEL=glm-5.2
    )
    echo [提示] 已生成测试 .env（站点密码 test2026），可按需编辑：%TEST_DIR%.env
)

cd /d "%TEST_DIR%"
echo 测试实例启动中：http://localhost:5090/internal-knowledge-base/
echo 站点密码：test2026（登录后用团队成员账号进入项目知识库）
echo 按 Ctrl+C 停止。
"%PYTHON%" run_production.py
pause
