"""每日定时更新新老券模块。"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from . import config, updater

log = logging.getLogger("bond_switch.scheduler")
_started = False
_guard = threading.Lock()


def _parse_hhmm(text: str) -> dt.time:
    try:
        hh, mm = text.strip().split(":", 1)
        return dt.time(int(hh), int(mm))
    except Exception:
        return dt.time(8, 0)


def _seconds_until(target: dt.time) -> float:
    now = dt.datetime.now()
    run_at = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += dt.timedelta(days=1)
    return (run_at - now).total_seconds()


def _loop() -> None:
    target = _parse_hhmm(config.AUTO_UPDATE_TIME)
    log.info("新老券自动更新已启动，每日 %s", target)
    while True:
        wait = _seconds_until(target)
        time.sleep(min(wait, 3600))
        if wait > 3600:
            continue
        log.info("新老券定时更新结果: %s", updater._run("incremental"))
        time.sleep(60)


def start() -> None:
    global _started
    if not config.AUTO_UPDATE_ENABLED:
        return
    with _guard:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="bond-switch-scheduler").start()
