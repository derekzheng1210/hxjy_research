from __future__ import annotations

import json
import os
import tempfile
import threading
import traceback
from datetime import datetime

from paths import DATA_DIR

from .db import connect, latest_cnbd_valuation_date
from .generators import generate_bond_picker_yields
from .oracle_bonds import refresh_oracle_bond_universe

# 管理页“应用 Oracle 候选池”后台任务：强制应用债券池并重建择券估值缓存。
# 债券池切换保护（差异超阈值只写候选不应用）用于首次 Excel→Oracle 割接与
# Oracle 数据异常兜底；本任务提供人工核对后的显式应用入口，避免候选池长期
# 挂起导致新发债进不了池。状态落盘（原子替换），多请求可见。
STATUS_FILE = DATA_DIR / "oracle_pool_apply_status.json"
STALE_RUNNING_SECONDS = 1800

_lock = threading.Lock()
_running = False

_default_status = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "error": None,
    "log": [],
}


def load_status() -> dict:
    try:
        with open(STATUS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return dict(_default_status, log=[])
    if not isinstance(data, dict):
        return dict(_default_status, log=[])
    status = dict(_default_status)
    status.update({key: data.get(key, value) for key, value in _default_status.items()})
    if not isinstance(status["log"], list):
        status["log"] = []
    return status


def _save_status(status: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".oracle_pool_apply-", dir=STATUS_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(status, handle, ensure_ascii=False)
        os.replace(tmp_name, STATUS_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _stale_running(status: dict) -> bool:
    if not status.get("running"):
        return False
    try:
        started = datetime.strptime(status.get("started_at") or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (datetime.now() - started).total_seconds() > STALE_RUNNING_SECONDS


def _worker() -> None:
    global _running
    status = load_status()
    status.update({
        "running": True,
        "ok": None,
        "error": None,
        "log": status.get("log") or [],
    })
    try:
        status["log"].append(f"{datetime.now().strftime('%H:%M:%S')} 从 Oracle 重建债券池（强制应用）")
        _save_status(status)
        with connect() as conn:
            as_of = latest_cnbd_valuation_date(conn)
            result = refresh_oracle_bond_universe(conn, as_of, force=True)
        if not result.get("applied"):
            raise RuntimeError("债券池未被应用，请查看对账报告 oracle_bond_reconciliation.json")
        status["log"].append(
            f"{datetime.now().strftime('%H:%M:%S')} 债券池已应用：{result.get('total_bonds', 0):,} 只"
            f"（数据日 {result.get('as_of_date')}）"
        )
        _save_status(status)
        status["log"].append(f"{datetime.now().strftime('%H:%M:%S')} 重建择券工具估值缓存")
        _save_status(status)
        yields = generate_bond_picker_yields()
        status["log"].append(
            f"{datetime.now().strftime('%H:%M:%S')} 估值缓存完成：{len(yields.get('yields') or {}):,} 条"
        )
        status["ok"] = True
        status["log"].append(f"{datetime.now().strftime('%H:%M:%S')} 应用完成")
    except Exception as exc:
        status["ok"] = False
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["log"].append(traceback.format_exc(limit=6))
    finally:
        status["running"] = False
        status["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _lock:
            _running = False
            _save_status(status)


def start_apply() -> tuple[bool, str]:
    global _running
    with _lock:
        persisted = load_status()
        if _running or (persisted.get("running") and not _stale_running(persisted)):
            return False, "已有 Oracle 债券池应用任务正在运行"
        _running = True
        # running 状态在返回给调用方之前同步落盘：管理页/脚本紧接着轮询
        # 状态接口时必须能看到"运行中"，否则会把启动间隙误判为已结束。
        _save_status({
            **persisted,
            "running": True,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "ok": None,
            "error": None,
            "log": [],
        })
    thread = threading.Thread(target=_worker, name="oracle-pool-apply", daemon=True)
    thread.start()
    return True, "Oracle 债券池应用任务已启动（含择券估值重建，约需数分钟）"
