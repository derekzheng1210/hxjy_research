"""Windows 生产入口：以 waitress（生产级 WSGI 服务器）运行门户。

gunicorn 不支持 Windows，本机生产部署统一使用本脚本：

    python run_production.py

- 不启用 Flask 调试器 / 热重载（FLASK_DEBUG 强制为 0）
- 监听端口由 PORT 环境变量控制，默认 5000
- 并发默认 16 线程（WAITRESS_THREADS 可覆盖）：AI 流式回答、LibreOffice 预览转换、
  文件传输等长请求均为 I/O 等待为主，宽线程池可避免任务队列积压（队列深度告警）
  导致的请求排队与连接中断
- 日志建议重定向到数据目录 logs/（NSSM 部署时由 AppStdout/AppStderr 接管）

Linux / 云端生产环境仍按 README 使用 gunicorn：
    gunicorn -c gunicorn.conf.py app:app
"""
import os

from dotenv import load_dotenv

load_dotenv()
os.environ["FLASK_DEBUG"] = "0"

from waitress import serve

from app import app

from fair_value_observation.scheduler import start as start_fair_value_observation
start_fair_value_observation()

# 经纪商行情调度器：app.py 开发模式下在 __main__ 启动，waitress 部署时在此启动。
# BROKER_SCHEDULER_ENABLED=0 可关闭；锁文件被其他进程持有时说明已有实例在调度，跳过。
if os.environ.get("BROKER_SCHEDULER_ENABLED", "1") == "1":
    from broker_market import start_scheduler as start_broker_scheduler

    if start_broker_scheduler():
        print("[production] 经纪商行情调度器已启动", flush=True)
    else:
        print("[production] 经纪商行情调度器锁被占用（已有实例在运行），本进程跳过启动", flush=True)

if os.environ.get("BOND_MONITOR_SCHEDULERS_ENABLED", "1") == "1":
    from interest_bond import init_bond_switch, init_issuance, init_spread

    init_spread()
    init_bond_switch()
    init_issuance()
    print("[production] 利率债研究模块调度器已启动", flush=True)

# 信用债一级发行看板（Node 服务）：启动时预热拉起；失败不阻断门户，
# 代理层（/bond-dashboard/）在请求期仍会兜底重试
if os.environ.get("BOND_DASHBOARD_AUTOSTART", "1") == "1":
    from bond_dashboard_proxy import ensure_server as ensure_bond_dashboard

    if ensure_bond_dashboard():
        print("[production] 一级发行看板 Node 服务已就绪", flush=True)
    else:
        print("[production] 一级发行看板 Node 服务未能自动拉起，代理层将在请求期重试", flush=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    threads = int(os.environ.get("WAITRESS_THREADS", "16"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"[production] waitress serving on {host}:{port} ({threads} threads)", flush=True)
    serve(app, host=host, port=port, threads=threads)
