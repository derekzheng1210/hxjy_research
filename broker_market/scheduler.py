from __future__ import annotations

import os
import threading
import time as time_module
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

from paths import BOND_DIR

from .storage import (
    LOCK_PATH,
    STATUS_PATH,
    atomic_write_json,
    ensure_directories,
    load_json,
    load_snapshot,
    record_market_emotion,
)


BROKER_TIMES = (
    time(9, 30), time(10, 0), time(10, 30), time(11, 0),
    time(13, 0), time(13, 30), time(14, 0), time(14, 30),
    time(15, 0), time(15, 30), time(16, 0),
)
DAILY_TIME = time(8, 30)
STALE_GRACE = timedelta(minutes=15)
RETRY_DELAYS = (300, 300)

_scheduler = None


def _empty_item() -> dict:
    return {
        "state": "idle", "last_attempt": "", "last_success": "",
        "last_success_scheduled_for": "", "scheduled_for": "",
        "last_error": "", "attempt": 0,
    }


def default_status() -> dict:
    return {"broker": _empty_item(), "bond_picker": _empty_item(), "updated_at": ""}


def load_status() -> dict:
    payload = load_json(STATUS_PATH, default_status())
    base = default_status()
    if isinstance(payload, dict):
        for kind in ("broker", "bond_picker"):
            if isinstance(payload.get(kind), dict):
                base[kind].update(payload[kind])
        base["updated_at"] = str(payload.get("updated_at") or "")
    return base


def save_status(payload: dict) -> None:
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    atomic_write_json(STATUS_PATH, payload)


def _scheduled_datetime(day, value: time) -> datetime:
    return datetime.combine(day, value)


def latest_due(now: datetime, values: tuple[time, ...]) -> datetime | None:
    if now.weekday() >= 5:
        return None
    due = [_scheduled_datetime(now.date(), value) for value in values if value <= now.time()]
    return max(due) if due else None


def next_due(now: datetime, values: tuple[time, ...]) -> datetime:
    for day_offset in range(0, 8):
        day = now.date() + timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for value in values:
            candidate = _scheduled_datetime(day, value)
            if candidate > now:
                return candidate
    raise RuntimeError("无法计算下次更新时间")


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def effective_success_for(kind: str, item: dict) -> datetime | None:
    success_for = _parse_datetime(item.get("last_success_scheduled_for", ""))
    if kind == "broker" and success_for is None:
        snapshot_time = _parse_datetime(load_snapshot().get("generated_at", ""))
        if snapshot_time:
            return snapshot_time
    if kind == "bond_picker" and success_for is None:
        valuation_cache = load_json(BOND_DIR / "oracle_latest_yields_cache.json", {})
        generated_at = _parse_datetime(valuation_cache.get("generated_at", ""))
        if generated_at:
            return generated_at
    return success_for


def public_status(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    payload = load_status()
    definitions = {
        "broker": BROKER_TIMES,
        "bond_picker": (DAILY_TIME,),
    }
    for kind, values in definitions.items():
        item = payload[kind]
        due = latest_due(now, values)
        success_for = effective_success_for(kind, item)
        if not item.get("last_success") and success_for:
            item["last_success"] = success_for.strftime("%Y-%m-%d %H:%M:%S")
        stale = bool(due and now > due + STALE_GRACE and (not success_for or success_for < due))
        item["stale"] = stale
        item["latest_expected"] = due.strftime("%Y-%m-%d %H:%M:%S") if due else ""
        item["next_run"] = next_due(now, values).strftime("%Y-%m-%d %H:%M:%S")
        if stale and item.get("state") not in {"running", "retrying"}:
            item["state"] = "stale"
        elif success_for and item.get("state") == "idle":
            item["state"] = "success"
    payload["timezone"] = "Asia/Shanghai"
    return payload


class SchedulerLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        self.handle.seek(0)
        if self.handle.read(1) == b"":
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            self.handle.close()
            self.handle = None
            return False


class BondTradingScheduler:
    def __init__(self, now_fn: Callable[[], datetime] = datetime.now):
        self.now_fn = now_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._job_lock = threading.Lock()
        self._process_lock = SchedulerLock(LOCK_PATH)

    def start(self) -> bool:
        ensure_directories()
        if not self._process_lock.acquire():
            return False
        self._thread = threading.Thread(target=self._loop, name="bond-trading-scheduler", daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due_jobs()
            except Exception:
                # Job functions persist their own sanitized errors; keep the scheduler alive.
                pass
            self._stop.wait(20)

    def run_due_jobs(self) -> None:
        now = self.now_fn()
        if now.weekday() >= 5:
            return
        status = load_status()
        due_jobs = (
            ("bond_picker", latest_due(now, (DAILY_TIME,))),
            ("broker", latest_due(now, BROKER_TIMES)),
        )
        for kind, due in due_jobs:
            if due is None:
                continue
            last_for = effective_success_for(kind, status[kind])
            attempted_for = _parse_datetime(status[kind].get("scheduled_for", ""))
            if (last_for and last_for >= due) or (attempted_for and attempted_for >= due and status[kind].get("state") in {"running", "retrying"}):
                continue
            self._run_locked(kind, due)
            status = load_status()

    def trigger(self, kind: str) -> tuple[bool, str]:
        if kind not in {"broker", "bond_picker"}:
            return False, "未知更新类型"
        if self._job_lock.locked():
            return False, "已有更新任务正在运行"
        scheduled_for = self.now_fn().replace(microsecond=0)
        thread = threading.Thread(
            target=self._run_locked,
            args=(kind, scheduled_for),
            name=f"manual-{kind}-update",
            daemon=True,
        )
        thread.start()
        return True, "更新任务已启动"

    def _run_locked(self, kind: str, scheduled_for: datetime) -> None:
        if not self._job_lock.acquire(blocking=False):
            return
        try:
            self._run_with_retries(kind, scheduled_for)
        finally:
            self._job_lock.release()

    def _run_with_retries(self, kind: str, scheduled_for: datetime) -> None:
        if kind == "broker":
            from .fetcher import fetch_and_save_latest
            runner = fetch_and_save_latest
        else:
            from juyuan_update.generators import run_all
            runner = lambda: run_all(modules=["bond_picker"])
        total_attempts = 1 + len(RETRY_DELAYS)
        for index in range(total_attempts):
            attempt = index + 1
            status = load_status()
            item = status[kind]
            item.update({
                "state": "running" if attempt == 1 else "retrying",
                "last_attempt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "scheduled_for": scheduled_for.strftime("%Y-%m-%d %H:%M:%S"),
                "last_error": "",
                "attempt": attempt,
            })
            save_status(status)
            try:
                result = runner()
                if kind == "broker" and isinstance(result, dict):
                    try:
                        record_market_emotion(result, scheduled_for=scheduled_for)
                    except Exception as emotion_exc:
                        # The quote snapshot is still valid.  Do not refetch DM
                        # merely because the derived history file could not be written.
                        result["emotion_error"] = f"{type(emotion_exc).__name__}: {emotion_exc}"[:500]
                status = load_status()
                status[kind].update({
                    "state": "success",
                    "last_success": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_success_scheduled_for": scheduled_for.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_error": "",
                    "attempt": attempt,
                    "result_count": int(result.get("quote_count", 0)) if isinstance(result, dict) else 0,
                })
                save_status(status)
                return
            except Exception as exc:
                status = load_status()
                status[kind].update({
                    "state": "retrying" if index < len(RETRY_DELAYS) else "failed",
                    "last_error": f"{type(exc).__name__}: {exc}"[:500],
                    "attempt": attempt,
                })
                save_status(status)
                if index < len(RETRY_DELAYS):
                    time_module.sleep(RETRY_DELAYS[index])


def start_scheduler() -> bool:
    global _scheduler
    if _scheduler is not None:
        return True
    instance = BondTradingScheduler()
    if not instance.start():
        return False
    _scheduler = instance
    return True


def trigger_update(kind: str) -> tuple[bool, str]:
    if _scheduler is None:
        return False, "调度器未启动，请使用独立项目启动脚本运行网页"
    return _scheduler.trigger(kind)
