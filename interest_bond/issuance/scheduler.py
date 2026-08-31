from __future__ import annotations

import threading
from datetime import datetime
from time import sleep
from zoneinfo import ZoneInfo

from . import config
from . import database
from .updater import UpdateBusyError, run_update


_STARTED = False
_GUARD = threading.Lock()


def start_scheduler(db_path=None) -> None:
    global _STARTED
    if not config.AUTO_UPDATE_ENABLED:
        return
    with _GUARD:
        if _STARTED:
            return
        _STARTED = True

    def loop():
        hour, minute = (int(x) for x in config.AUTO_UPDATE_TIME.split(":", 1))
        tz = ZoneInfo("Asia/Shanghai")
        while True:
            now = datetime.now(tz)
            today = now.date().isoformat()
            with database.connect(db_path) as conn:
                last_scheduled = database.get_meta(conn, "last_scheduled_date")
            if now.hour == hour and now.minute >= minute and last_scheduled != today:
                try:
                    run_update("full" if now.day == 1 else "incremental", db_path=db_path)
                except UpdateBusyError:
                    pass
                except Exception:
                    pass
                finally:
                    with database.transaction(db_path) as conn:
                        database.set_meta(conn, "last_scheduled_date", today)
            sleep(30)

    threading.Thread(target=loop, name="bond-tracker-scheduler", daemon=True).start()
