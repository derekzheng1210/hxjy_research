"""新老券模块的SQLite缓存。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS bond_snapshots (
    as_of_date        TEXT NOT NULL,
    code              TEXT NOT NULL,
    secode            TEXT NOT NULL,
    short_name        TEXT NOT NULL,
    full_name         TEXT NOT NULL,
    issue_date        TEXT,
    list_date         TEXT,
    maturity_date     TEXT,
    remaining_years   REAL,
    valuation_yield   REAL,
    deal_count        INTEGER NOT NULL DEFAULT 0,
    volume            REAL NOT NULL DEFAULT 0,
    outstanding_amount REAL,
    reissue_count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(as_of_date, code)
);
CREATE TABLE IF NOT EXISTS role_assignments (
    as_of_date TEXT NOT NULL,
    role       TEXT NOT NULL,
    ordinal    INTEGER NOT NULL DEFAULT 1,
    code       TEXT NOT NULL,
    PRIMARY KEY(as_of_date, role, ordinal)
);
CREATE TABLE IF NOT EXISTS valuation_points (
    trade_date      TEXT NOT NULL,
    code            TEXT NOT NULL,
    yield           REAL NOT NULL,
    remaining_years REAL,
    PRIMARY KEY(trade_date, code)
);
CREATE TABLE IF NOT EXISTS bond_daily_quotes (
    trade_date      TEXT NOT NULL,
    code            TEXT NOT NULL,
    deal_count      INTEGER NOT NULL DEFAULT 0,
    volume          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(trade_date, code)
);
CREATE INDEX IF NOT EXISTS idx_bond_daily_quotes_code_date
ON bond_daily_quotes(code, trade_date);
CREATE INDEX IF NOT EXISTS idx_valuation_points_code_date
ON valuation_points(code, trade_date);
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


def initialize(db_path: Path | str | None = None) -> None:
    with transaction(db_path) as conn:
        conn.executescript(SCHEMA)


def replace_snapshot(as_of_date: str, bonds: list[dict], roles: dict[str, list[str]], db_path=None) -> None:
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM bond_snapshots WHERE as_of_date=?", (as_of_date,))
        conn.execute("DELETE FROM role_assignments WHERE as_of_date=?", (as_of_date,))
        conn.executemany(
            """INSERT INTO bond_snapshots(
                as_of_date,code,secode,short_name,full_name,issue_date,list_date,maturity_date,
                remaining_years,valuation_yield,deal_count,volume,outstanding_amount,reissue_count
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    as_of_date, b["code"], b["secode"], b["short_name"], b["full_name"],
                    b.get("issue_date"), b.get("list_date"), b.get("maturity_date"),
                    b.get("remaining_years"), b.get("valuation_yield"), int(b.get("deal_count") or 0),
                    float(b.get("volume") or 0), b.get("outstanding_amount"), int(b.get("reissue_count") or 0),
                )
                for b in bonds
            ],
        )
        conn.executemany(
            "INSERT INTO role_assignments(as_of_date,role,ordinal,code) VALUES(?,?,?,?)",
            [(as_of_date, role, i + 1, code) for role, codes in roles.items() for i, code in enumerate(codes)],
        )


def upsert_valuations(rows: list[tuple[str, str, float, float | None]], db_path=None) -> int:
    if not rows:
        return 0
    with transaction(db_path) as conn:
        conn.executemany(
            """INSERT INTO valuation_points(trade_date,code,yield,remaining_years) VALUES(?,?,?,?)
               ON CONFLICT(trade_date,code) DO UPDATE SET
                 yield=excluded.yield,remaining_years=excluded.remaining_years""",
            rows,
        )
    return len(rows)


def latest_snapshot_date(db_path=None) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT MAX(as_of_date) FROM bond_snapshots").fetchone()
    return row[0] if row else None


def max_valuation_date(code: str, db_path=None) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM valuation_points WHERE code=?", (code,)).fetchone()
    return row[0] if row else None


def load_latest_dashboard(db_path=None) -> tuple[str | None, list[dict], dict[str, list[str]]]:
    as_of = latest_snapshot_date(db_path)
    if not as_of:
        return None, [], {}
    with connect(db_path) as conn:
        bonds = [dict(r) for r in conn.execute(
            "SELECT * FROM bond_snapshots WHERE as_of_date=? ORDER BY deal_count DESC,volume DESC", (as_of,)
        )]
        role_rows = conn.execute(
            "SELECT role,ordinal,code FROM role_assignments WHERE as_of_date=? ORDER BY role,ordinal", (as_of,)
        ).fetchall()
    roles: dict[str, list[str]] = {}
    for r in role_rows:
        roles.setdefault(r["role"], []).append(r["code"])
    return as_of, bonds, roles


def role_first_date(role: str, code: str, db_path=None) -> str | None:
    """角色首次被指派的快照日期，例如券成为活跃券的起始日期。"""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT MIN(as_of_date) FROM role_assignments WHERE role=? AND code=?", (role, code)
        ).fetchone()
    return row[0] if row else None


def upsert_daily_quotes(rows: list[tuple[str, str, int, float]], db_path=None) -> int:
    if not rows:
        return 0
    with transaction(db_path) as conn:
        conn.executemany(
            """INSERT INTO bond_daily_quotes(trade_date,code,deal_count,volume) VALUES(?,?,?,?)
               ON CONFLICT(trade_date,code) DO UPDATE SET
                 deal_count=excluded.deal_count,volume=excluded.volume""",
            rows,
        )
    return len(rows)


def load_daily_quotes(start: str | None = None, end: str | None = None, db_path=None) -> dict[str, dict[str, tuple[int, float]]]:
    """返回 {trade_date: {code: (成交笔数, 成交量)}}。"""
    sql = "SELECT trade_date,code,deal_count,volume FROM bond_daily_quotes WHERE 1=1"
    binds: list[object] = []
    if start:
        sql += " AND trade_date>=?"
        binds.append(start.replace("-", ""))
    if end:
        sql += " AND trade_date<=?"
        binds.append(end.replace("-", ""))
    out: dict[str, dict[str, tuple[int, float]]] = {}
    with connect(db_path) as conn:
        for trade_date, code, deals, vol in conn.execute(sql + " ORDER BY trade_date", binds):
            out.setdefault(trade_date, {})[code] = (int(deals or 0), float(vol or 0))
    return out


def distinct_valuation_codes(db_path=None) -> list[str]:
    with connect(db_path) as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT code FROM valuation_points")]


def all_snapshot_names(db_path=None) -> dict[str, str]:
    """历史快照中出现过的 code -> 简称（含已退出当前候选池的券）。"""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT code, short_name FROM bond_snapshots").fetchall()
    return {r["code"]: r["short_name"] for r in rows}


def snapshot_bond_attrs(db_path=None) -> dict[str, dict[str, str]]:
    """code -> 最近快照中的静态属性（简称、发行日），用于逐日角色判定与名称回溯。"""
    sql = """
    SELECT s.code, s.short_name, s.issue_date
    FROM bond_snapshots s
    JOIN (
      SELECT code, MAX(as_of_date) AS latest FROM bond_snapshots GROUP BY code
    ) m ON m.code = s.code AND m.latest = s.as_of_date
    """
    out: dict[str, dict[str, str]] = {}
    with connect(db_path) as conn:
        for row in conn.execute(sql):
            out[row["code"]] = {"short_name": row["short_name"] or row["code"], "issue_date": row["issue_date"] or ""}
    return out


def load_point_matrix(start: str | None = None, end: str | None = None, db_path=None) -> dict[str, dict[str, tuple[float, float | None]]]:
    """全部券的估值矩阵 {trade_date: {code: (yield, remaining_years)}}。"""
    sql = "SELECT trade_date,code,yield,remaining_years FROM valuation_points WHERE 1=1"
    binds: list[object] = []
    if start:
        sql += " AND trade_date>=?"
        binds.append(start.replace("-", ""))
    if end:
        sql += " AND trade_date<=?"
        binds.append(end.replace("-", ""))
    out: dict[str, dict[str, tuple[float, float | None]]] = {}
    with connect(db_path) as conn:
        for trade_date, code, value, remaining in conn.execute(sql + " ORDER BY trade_date", binds):
            out.setdefault(trade_date, {})[code] = (float(value), float(remaining) if remaining is not None else None)
    return out


def load_valuations(codes: list[str], start: str | None = None, end: str | None = None, db_path=None) -> dict[str, dict[str, float]]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    sql = f"SELECT trade_date,code,yield FROM valuation_points WHERE code IN ({placeholders})"
    binds: list[object] = list(codes)
    if start:
        sql += " AND trade_date>=?"
        binds.append(start.replace("-", ""))
    if end:
        sql += " AND trade_date<=?"
        binds.append(end.replace("-", ""))
    sql += " ORDER BY trade_date"
    out: dict[str, dict[str, float]] = {c: {} for c in codes}
    with connect(db_path) as conn:
        for trade_date, code, value in conn.execute(sql, binds):
            out.setdefault(code, {})[trade_date] = float(value)
    return out


def start_update_run(mode: str, db_path=None) -> int:
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO update_runs(mode,status,started_at) VALUES(?,?,?)",
            (mode, "running", datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def finish_update_run(run_id: int, status: str, message: str, as_of: str | None, rows: int, db_path=None) -> None:
    with transaction(db_path) as conn:
        conn.execute(
            """UPDATE update_runs SET status=?,finished_at=?,as_of_date=?,message=?,rows_upserted=?
               WHERE id=?""",
            (status, datetime.now().isoformat(timespec="seconds"), as_of, message, rows, run_id),
        )


def last_successful_run(db_path=None) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM update_runs WHERE status='success' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def set_meta(key: str, value: object, db_path=None) -> None:
    with transaction(db_path) as conn:
        conn.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def get_meta(key: str, default: str | None = None, db_path=None) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else default
