from __future__ import annotations

from datetime import datetime
import json
import os
import tempfile
import threading
import traceback

from .generators import run_all
from paths import DATA_DIR

DEFAULT_MODULES = [
    "bond_picker", "spread_monitor", "strategy_dashboard", "credit_std_dev",
    "institution_flow_rates", "primary_market_pricing", "rate_spread",
    "rate_bond_switch", "rate_issuance",
]
# 行业景气高频数据（ipm_tracker）不纳入一键更新：本机无 Wind 终端（WindPy），
# 该数据由 Wind 远程推送通道（/api/ingest/ipm + IPM_INGEST_TOKEN）维护。
VALID_MODULES = set(DEFAULT_MODULES)

# 状态持久化：gunicorn 多 worker 下，启动更新的 worker 与轮询进度的 worker
# 可能不是同一进程，内存态状态会互相不可见。这里把状态写到数据目录的
# update_status.json（原子替换），get_status() 始终从文件读取。
STATUS_FILE = DATA_DIR / "update_status.json"

_lock = threading.Lock()
_default_status = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "step": "未运行",
    "percent": 0,
    "ok": None,
    "error": None,
    "log": [],
    "modules": DEFAULT_MODULES,
}


def _load_status() -> dict:
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
    fd, tmp_name = tempfile.mkstemp(prefix=".update_status-", dir=STATUS_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(status, handle, ensure_ascii=False)
        os.replace(tmp_name, STATUS_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def get_status() -> dict:
    with _lock:
        status = _load_status()
    return dict(status, log=list(status["log"][-200:]))


def _append(message: str, percent: int | None = None) -> None:
    with _lock:
        status = _load_status()
        if percent is not None:
            status["percent"] = max(0, min(100, int(percent)))
        status["step"] = message
        pct = f" [{status['percent']}%]" if status.get("percent") else ""
        status["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {message}{pct}")
        _save_status(status)


def start_update(modules: list[str] | None = None) -> tuple[bool, str]:
    selected = [m for m in (modules or DEFAULT_MODULES) if m in VALID_MODULES]
    if not selected:
        return False, "未选择任何更新模块"
    with _lock:
        status = _load_status()
        if status["running"]:
            return False, "已有数据库更新任务正在运行"
        status.update({
            "running": True,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "step": "准备开始",
            "percent": 0,
            "ok": None,
            "error": None,
            "log": [],
            "modules": selected,
        })
        _save_status(status)
    thread = threading.Thread(target=_worker, args=(selected,), name="juyuan-update", daemon=True)
    thread.start()
    return True, "数据库更新任务已启动"


def _worker(modules: list[str]) -> None:
    try:
        _append("开始数据库更新", 1)
        standard_modules = [module for module in modules if module not in {"primary_market_pricing", "rate_spread", "rate_bond_switch", "rate_issuance", "ipm_tracker"}]
        if standard_modules:
            run_all(progress=_append, modules=standard_modules)
        if "primary_market_pricing" in modules:
            from primary_market_pricing.cache_builder import build_cache_once

            build_cache_once(
                progress=lambda message, percent=None: _append(
                    message,
                    90 if percent is None else 90 + int(max(0, min(100, percent)) * 0.08),
                )
            )
        if "ipm_tracker" in modules:
            from ipm_tracker.updater import run_update as run_ipm_update

            _append("开始更新行业景气高频数据")
            result = run_ipm_update(progress=_append)
            if isinstance(result, dict) and result.get("ok") is False:
                raise RuntimeError(result.get("error") or "行业景气高频数据更新失败")
            _append("行业景气高频数据更新完成")
        rate_tasks = (
            ("rate_spread", "超长端利率利差", "interest_bond.spread.updater", "run_update"),
            ("rate_bond_switch", "新老券利差", "interest_bond.bond_switch.updater", "run_update"),
            ("rate_issuance", "国债、地方债发行", "interest_bond.issuance.updater", "run_update"),
        )
        for module, label, module_path, function_name in rate_tasks:
            if module not in modules:
                continue
            _append(f"开始更新{label}")
            imported = __import__(module_path, fromlist=[function_name])
            result = getattr(imported, function_name)("incremental")
            if isinstance(result, dict) and result.get("ok") is False:
                raise RuntimeError(result.get("error") or f"{label}更新失败")
            _append(f"{label}更新完成")
        with _lock:
            status = _load_status()
            status["ok"] = True
            status["step"] = "更新完成"
            status["percent"] = 100
            status["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status["running"] = False
            status["log"].append(f"{datetime.now().strftime('%H:%M:%S')} 更新完成 [100%]")
            _save_status(status)
    except Exception as exc:
        tb = traceback.format_exc(limit=8)
        with _lock:
            status = _load_status()
            status["ok"] = False
            status["error"] = f"{type(exc).__name__}: {exc}"
            status["step"] = "更新失败"
            status["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status["running"] = False
            status["log"].append(tb)
            _save_status(status)
