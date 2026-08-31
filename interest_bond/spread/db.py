"""SQLite本地存储:曲线点位、更新日志、元信息。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS curve_points (
    trade_date TEXT NOT NULL,
    curve_id   TEXT NOT NULL,
    tenor      REAL NOT NULL,
    yield      REAL NOT NULL,
    PRIMARY KEY(trade_date, curve_id, tenor)
);
CREATE TABLE IF NOT EXISTS update_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    as_of_date TEXT,
    message TEXT NOT NULL DEFAULT '',
    rows_upserted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def transaction(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(db_path=None) -> None:
    with transaction(db_path) as conn:
        conn.executescript(SCHEMA)


def get_meta(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(key: str, value: object) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def upsert_points(rows: list[tuple[str, str, float, float]]) -> int:
    if not rows:
        return 0
    with transaction() as conn:
        conn.executemany(
            "INSERT INTO curve_points(trade_date,curve_id,tenor,yield) VALUES(?,?,?,?) "
            "ON CONFLICT(trade_date,curve_id,tenor) DO UPDATE SET yield=excluded.yield",
            rows,
        )
    return len(rows)


def max_trade_date(curve_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM curve_points WHERE curve_id=?", (curve_id,)
        ).fetchone()
    return row[0] if row else None


def start_update_run(mode: str) -> int:
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO update_runs(mode,status,started_at) VALUES(?,?,?)",
            (mode, "running", datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def finish_update_run(run_id: int, status: str, message: str, as_of: str | None, rows: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE update_runs SET status=?, finished_at=?, as_of_date=?, message=?, rows_upserted=? "
            "WHERE id=?",
            (
                status,
                datetime.now().isoformat(timespec="seconds"),
                as_of,
                message,
                rows,
                run_id,
            ),
        )


def last_successful_run(db_path: Path | str | None = None) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM update_runs WHERE status='success' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def load_points(start: str | None = None, end: str | None = None, db_path: Path | str | None = None) -> dict[str, dict[float, dict[str, float]]]:
    """返回 {curve_id: {tenor: {trade_date(YYYYMMDD): yield}}}"""
    sql = "SELECT trade_date, curve_id, tenor, yield FROM curve_points"
    conds, binds = [], []
    if start:
        conds.append("trade_date >= ?")
        binds.append(start.replace("-", ""))
    if end:
        conds.append("trade_date <= ?")
        binds.append(end.replace("-", ""))
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY trade_date"
    result: dict[str, dict[float, dict[str, float]]] = {}
    with connect(db_path) as conn:
        for trade_date, curve_id, tenor, value in conn.execute(sql, binds):
            result.setdefault(curve_id, {}).setdefault(float(tenor), {})[trade_date] = float(value)
    return result


def coverage(db_path: Path | str | None = None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT curve_id, tenor, COUNT(*) n, MIN(trade_date) mn, MAX(trade_date) mx "
            "FROM curve_points GROUP BY curve_id, tenor ORDER BY curve_id, tenor"
        ).fetchall()
    return [dict(r) for r in rows]
