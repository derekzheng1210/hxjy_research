"""更新任务:全量/增量拉取曲线并写入SQLite。供CLI、一键更新、定时调度共用。"""
from __future__ import annotations

import threading
import traceback
from datetime import date, datetime, timedelta

from . import config
from . import db
from . import oracle_source

# 进程内单例:同一时刻只允许一个更新任务
_lock = threading.Lock()
_state = {
    "running": False,
    "mode": "",
    "stage": "",
    "started_at": None,
    "finished_at": None,
    "message": "",
    "rows": 0,
}


def status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "mode": _state["mode"],
            "stage": _state["stage"],
            "started_at": _state["started_at"],
            "finished_at": _state["finished_at"],
            "message": _state["message"],
            "rows": _state["rows"],
        }


def _run(mode: str) -> dict:
    """同步执行一次更新。mode: full=全量回补, incremental=增量。"""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "已有更新任务在进行中"}
    try:
        _state.update(running=True, mode=mode, stage="开始", started_at=datetime.now().isoformat(timespec="seconds"),
                      message="", rows=0)
        db.initialize()
        run_id = db.start_update_run(mode)
        try:
            total = 0
            as_of = None
            latest = oracle_source.latest_available_date()
            if latest:
                as_of = latest
            end_ymd = date.today().strftime("%Y%m%d")
            for curve_id in config.CURVES:
                if mode == "full":
                    start_ymd = config.HISTORY_START
                else:
                    last = db.max_trade_date(curve_id)
                    if not last:
                        start_ymd = config.HISTORY_START
                    else:
                        start = date(int(last[:4]), int(last[4:6]), int(last[6:8])) - timedelta(
                            days=config.INCREMENTAL_OVERLAP_DAYS
                        )
                        start_ymd = max(start.strftime("%Y%m%d"), config.HISTORY_START)
                if start_ymd > end_ymd:
                    continue
                _state["stage"] = f"拉取 {config.CURVES[curve_id]['name']}"
                rows = oracle_source.fetch_curve(curve_id, start_ymd, end_ymd, progress=_progress)
                total += db.upsert_points(rows)
                _state["rows"] = total
            db.finish_update_run(run_id, "success", "ok", as_of, total)
            db.set_meta("last_success_at", datetime.now().isoformat(timespec="seconds"))
            _state.update(running=False, stage="完成", message=f"写入{total}条点位",
                          finished_at=datetime.now().isoformat(timespec="seconds"))
            return {"ok": True, "rows": total, "as_of": as_of}
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            db.finish_update_run(run_id, "failed", detail[:800], None, _state["rows"])
            _state.update(running=False, stage="失败", message=detail[:800],
                          finished_at=datetime.now().isoformat(timespec="seconds"))
            return {"ok": False, "error": detail}
    finally:
        _lock.release()


def run_update(mode: str = "incremental") -> dict:
    """同步执行一次更新，供门户后台统一任务调用。"""
    return _run(mode)


def _progress(text: str) -> None:
    _state["stage"] = text


def run_async(mode: str = "incremental") -> dict:
    """后台线程执行更新,立即返回。若已在运行则报错。"""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "已有更新任务在进行中"}

    def worker():
        # worker内再acquire会失败,先释放,由_run内部重新获取
        _lock.release()
        _run(mode)

    threading.Thread(target=worker, daemon=True, name="spread-update").start()
    return {"ok": True, "started": True, "mode": mode}


def is_stale(days: float = 1.5) -> bool:
    """数据距今超过days天则视为过期(供启动补更判断)。"""
    latest = db.max_trade_date("treasury")
    if not latest:
        return True
    last = date(int(latest[:4]), int(latest[4:6]), int(latest[6:8]))
    return (date.today() - last).days >= days
