@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d D:\hxjy_research
echo ============================================
echo  固收部 + 资配部 新报告 -> 公司知识库 同步
echo ============================================
python scripts\sync_fim_new_reports.py
echo.
echo 同步结束，窗口可关闭
pause
