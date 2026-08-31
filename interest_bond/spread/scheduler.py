"""每日定时自动更新(后台线程)。时间由 SPREAD_AUTO_UPDATE_TIME 控制,默认07:30。"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from . import config
from . import updater

log = logging.getLogger("scheduler")


def _parse_hhmm(text: str) -> dt.time:
    try:
        hh, mm = text.strip().split(":")
        return dt.time(int(hh), int(mm))
    except Exception:
        return dt.time(7, 30)


def _seconds_until(target: dt.time) -> float:
    now = dt.datetime.now()
    run_at = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += dt.timedelta(days=1)
    return (run_at - now).total_seconds()


def _loop() -> None:
    auto_time = _parse_hhmm(config.AUTO_UPDATE_TIME)
    log.info("自动更新调度已启动,每日 %s 触发", auto_time)
    while True:
        try:
            wait = _seconds_until(auto_time)
            time.sleep(min(wait, 3600))
            if wait > 3600:
                continue  # 每小时醒一次检查,便于长时间驻留
            result = updater._run("incremental")
            log.info("定时更新结果: %s", result)
            time.sleep(60)  # 触发后休眠,避免同分钟重复执行
        except Exception:
            log.exception("调度循环异常")
            time.sleep(600)


def start() -> None:
    if not config.AUTO_UPDATE_ENABLED:
        log.info("自动更新已关闭(SPREAD_AUTO_UPDATE=0)")
        return
    threading.Thread(target=_loop, daemon=True, name="spread-scheduler").start()
