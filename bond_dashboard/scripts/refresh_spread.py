# -*- coding: utf-8 -*-
"""
spread 板块估值缓存 —— 每日 09:30 自动刷新（计划任务 BondSpreadRefresh）
================================================================
背景（2026-09-11 用户要求）：spread 板块不再每次打开都强制刷新 DM，
改为「每日 09:30 自动刷新一次 + 页面手动刷新按钮」。

本脚本调用本地看板接口 /api/spread/recommended?refresh=1：
  · 服务端按增量从 DM 拉取全部推荐个券的上市后估值序列，写入 data/cache/own_series.json
  · 页面（mode=cached）随后直接读缓存，不再重复请求 DM

依赖：看板服务已在 3000 端口监听（由计划任务 BondDashboardServer 常驻）。
日志：logs/spread_refresh_YYYYMMDD.log（成功/失败、耗时、命中数、估值截至日）
用法: "<venv>/python.exe" scripts/refresh_spread.py
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LOGS = os.path.join(ROOT, "logs")
os.makedirs(LOGS, exist_ok=True)
BASE = os.environ.get("BOND_DASHBOARD_URL", "http://localhost:3000")
TODAY = dt.date.today()
LOG_FILE = os.path.join(LOGS, f"spread_refresh_{TODAY.isoformat()}.log")


def log(msg):
    line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass  # pythonw 下无 stdout


def get_json(url, timeout=900):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    log(f"===== spread 估值缓存刷新开始（{BASE}）=====")

    # 1) 等待服务就绪（开机后或刚重启时可能尚未监听），最多 3 分钟
    ready = False
    for i in range(18):
        try:
            get_json(f"{BASE}/api/bids?type=won", timeout=10)
            ready = True
            break
        except Exception:
            if i == 0:
                log("看板服务未就绪，等待中…")
            time.sleep(10)
    if not ready:
        log(f"✗ 看板服务不可用（{BASE}），本次跳过。请确认计划任务 BondDashboardServer 正在运行。")
        return 1

    # 2) 触发全量刷新（refresh=1 = 忽略缓存重拉，服务端内部按券商增量写缓存）
    t0 = time.time()
    try:
        j = get_json(f"{BASE}/api/spread/recommended?refresh=1")
    except urllib.error.URLError as e:
        log(f"✗ 请求失败: {e}")
        return 2
    except Exception as e:
        log(f"✗ 异常: {repr(e)[:200]}")
        return 2

    meta = j.get("meta") or {}
    cost = time.time() - t0
    log(f"✓ 刷新完成，耗时 {cost:.1f}s")
    log(f"  推荐券 {meta.get('total')} 只 | 已上市有估值 {meta.get('valued')} 只 | "
        f"未上市/无估值 {meta.get('noVal')} 只 | 缺票面 {meta.get('noCoupon')} 只")
    log(f"  估值截至 {meta.get('latestDate')} | 缓存更新于 {meta.get('cacheUpdatedAt')} | mode={meta.get('mode')}")
    log(f"  未参与复盘：错失浮盈 {meta.get('missedCount')} 只 / 躲过亏损 {meta.get('avoidedCount')} 只 | "
        f"自测表 {meta.get('selfTestCount')} 只（浮盈 {meta.get('selfTestWin')} / 亏损 {meta.get('selfTestLoss')}）")
    if not meta.get("valued"):
        log("⚠ 估值为 0：可能尚未上市或缓存异常，请在看板点「手动刷新估值」复核。")
    log("===== 结束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
