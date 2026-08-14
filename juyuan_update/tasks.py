from __future__ import annotations

from datetime import datetime
import threading
import traceback

from .generators import run_all


DEFAULT_MODULES = ["bond_picker", "spread_monitor", "strategy_dashboard", "credit_std_dev", "institution_flow_rates", "primary_market_pricing"]
VALID_MODULES = set(DEFAULT_MODULES)


_lock = threading.Lock()
_status = {
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


def get_status() -> dict:
    with _lock:
        return dict(_status, log=list(_status["log"][-200:]))


def _append(message: str, percent: int | None = None) -> None:
    with _lock:
        if percent is not None:
            _status["percent"] = max(0, min(100, int(percent)))
        _status["step"] = message
        pct = f" [{_status['percent']}%]" if _status.get("percent") else ""
        _status["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {message}{pct}")


def start_update(modules: list[str] | None = None) -> tuple[bool, str]:
    selected = [m for m in (modules or DEFAULT_MODULES) if m in VALID_MODULES]
    if not selected:
        return False, "\u672a\u9009\u62e9\u4efb\u4f55\u66f4\u65b0\u6a21\u5757"
    with _lock:
        if _status["running"]:
            return False, "\u5df2\u6709\u6570\u636e\u5e93\u66f4\u65b0\u4efb\u52a1\u6b63\u5728\u8fd0\u884c"
        _status.update({
            "running": True,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "step": "\u51c6\u5907\u5f00\u59cb",
            "percent": 0,
            "ok": None,
            "error": None,
            "log": [],
            "modules": selected,
        })
    thread = threading.Thread(target=_worker, args=(selected,), name="juyuan-update", daemon=True)
    thread.start()
    return True, "\u6570\u636e\u5e93\u66f4\u65b0\u4efb\u52a1\u5df2\u542f\u52a8"


def _worker(modules: list[str]) -> None:
    try:
        _append("开始数据库更新", 1)
        standard_modules = [module for module in modules if module != "primary_market_pricing"]
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
        with _lock:
            _status["ok"] = True
            _status["step"] = "更新完成"
            _status["percent"] = 100
            _status["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _status["running"] = False
            _status["log"].append(f"{datetime.now().strftime('%H:%M:%S')} 更新完成 [100%]")
    except Exception as exc:
        tb = traceback.format_exc(limit=8)
        with _lock:
            _status["ok"] = False
            _status["error"] = f"{type(exc).__name__}: {exc}"
            _status["step"] = "更新失败"
            _status["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _status["running"] = False
            _status["log"].append(tb)
