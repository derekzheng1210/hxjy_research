from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import openpyxl

from . import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS limits (
    year INTEGER NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(year, category)
);
CREATE TABLE IF NOT EXISTS limit_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    category TEXT NOT NULL,
    old_amount REAL,
    new_amount REAL NOT NULL,
    changed_at TEXT NOT NULL,
    actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS annual_snapshots (
    year INTEGER NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    source TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(year, category)
);
CREATE TABLE IF NOT EXISTS bond_cache (
    unique_key TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('treasury','local')),
    issue_date TEXT NOT NULL,
    year INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    term_years REAL,
    purpose TEXT NOT NULL DEFAULT '',
    codes TEXT NOT NULL DEFAULT '',
    classification TEXT,
    include_special_refi INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_bond_cache_year ON bond_cache(year);
CREATE INDEX IF NOT EXISTS idx_bond_cache_long ON bond_cache(term_years, issue_date);
CREATE INDEX IF NOT EXISTS idx_bond_cache_class ON bond_cache(classification, year);
CREATE TABLE IF NOT EXISTS policy_financial_bond_cache (
    unique_key TEXT PRIMARY KEY,
    issue_date TEXT NOT NULL,
    year INTEGER NOT NULL,
    issuer TEXT NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    source_updated_at TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_policy_financial_year ON policy_financial_bond_cache(year);
CREATE INDEX IF NOT EXISTS idx_policy_financial_date ON policy_financial_bond_cache(issue_date);
CREATE TABLE IF NOT EXISTS treasury_maturities (
    unique_key TEXT PRIMARY KEY,
    maturity_date TEXT NOT NULL,
    year INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_treasury_maturity_year ON treasury_maturities(year);
CREATE TABLE IF NOT EXISTS update_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    as_of_date TEXT,
    message TEXT NOT NULL DEFAULT '',
    rows_changed INTEGER NOT NULL DEFAULT 0
);
"""


class ClosingConnection(sqlite3.Connection):
    """SQLite默认的with只提交事务不关闭句柄；这里保证读连接及时释放。"""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path, timeout=30, check_same_thread=False, factory=ClosingConnection
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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


def _limits_from_workbook() -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    if config.LIMIT_WORKBOOK.exists():
        wb = openpyxl.load_workbook(config.LIMIT_WORKBOOK, data_only=True, read_only=True)
        ws = wb["Sheet1"]
        for row in ws.iter_rows(min_row=3, max_col=6, values_only=True):
            if not row[0]:
                continue
            year_text = str(row[0])
            digits = "".join(ch for ch in year_text if ch.isdigit())
            if not digits:
                continue
            year = int(digits[:4])
            result[year] = {
                category: float(row[idx] or 0)
                for idx, category in enumerate(config.CATEGORIES, start=1)
            }
        wb.close()
    if not result:
        for year, values in config.FALLBACK_LIMITS.items():
            result[year] = dict(zip(config.CATEGORIES, map(float, values)))
    return result


def initialize(db_path: Path | str | None = None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with transaction(db_path) as conn:
        conn.executescript(SCHEMA)
        for year, by_category in config.HISTORICAL_SNAPSHOTS.items():
            for category, amount in by_category.items():
                conn.execute(
                    """
                    INSERT INTO annual_snapshots(year, category, amount, source, locked, updated_at)
                    VALUES(?,?,?,?,1,?)
                    ON CONFLICT(year, category) DO UPDATE SET
                      amount=excluded.amount, source=excluded.source, locked=1, updated_at=excluded.updated_at
                    """,
                    (year, category, amount, "历史口径固化", now),
                )
        for year, by_category in _limits_from_workbook().items():
            for category, amount in by_category.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO limits(year, category, amount, updated_at)
                    VALUES(?,?,?,?)
                    """,
                    (year, category, amount, now),
                )
        conn.execute(
            "INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version','1')"
        )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
