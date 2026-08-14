"""Repair legacy cache rows that have no fair price.

The legacy issue-amount backfill could create placeholder rows before a
pricing calculation ran. This tool recalculates every affected issuer once,
then records the outcome so genuine no-reference cases are not retried.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime

from cache_builder import CACHE_DB_PATH, ISSUE_DATE_RULE_VERSION, init_cache_db, save_issuer_result
from calculator import _QueryCache, calculate_issuer_deviations
from db_utils import get_connection


def _init_repair_log(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS unpriced_cache_repair_log (
            issuer TEXT NOT NULL,
            issue_date_rule TEXT NOT NULL,
            status TEXT NOT NULL,
            first_unpriced_date TEXT,
            end_date TEXT,
            rows_before INTEGER,
            rows_remaining INTEGER,
            attempted_at TEXT NOT NULL,
            detail TEXT,
            PRIMARY KEY (issuer, issue_date_rule)
        )
    """)
    conn.commit()


def _affected_issuers(
    conn: sqlite3.Connection,
    issuer: str | None,
    retry: bool,
    limit: int,
) -> list[sqlite3.Row]:
    filters = ["b.issue_date_rule = ?", "b.fair_price IS NULL"]
    params: list[object] = [ISSUE_DATE_RULE_VERSION]
    if issuer:
        filters.append("b.issuer = ?")
        params.append(issuer)
    if not retry:
        filters.append("NOT EXISTS (SELECT 1 FROM unpriced_cache_repair_log l "
                       "WHERE l.issuer = b.issuer AND l.issue_date_rule = b.issue_date_rule "
                       "AND l.status IN ('done', 'no_issues'))")

    sql = f"""
        SELECT b.issuer,
               MIN(b.issue_date) AS first_unpriced_date,
               MAX(COALESCE(s.end_date, b.issue_date)) AS end_date,
               COUNT(*) AS rows_before
        FROM bond_deviations b
        LEFT JOIN issuer_summary s
          ON s.issuer = b.issuer AND s.issue_date_rule = b.issue_date_rule
        WHERE {' AND '.join(filters)}
        GROUP BY b.issuer
        ORDER BY rows_before DESC, b.issuer
    """
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _write_log(
    conn: sqlite3.Connection,
    issuer: str,
    status: str,
    first_unpriced_date: str,
    end_date: str,
    rows_before: int,
    rows_remaining: int | None,
    detail: str = "",
) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO unpriced_cache_repair_log (
            issuer, issue_date_rule, status, first_unpriced_date, end_date,
            rows_before, rows_remaining, attempted_at, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        issuer,
        ISSUE_DATE_RULE_VERSION,
        status,
        first_unpriced_date,
        end_date,
        rows_before,
        rows_remaining,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        detail[:1000],
    ))
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issuer", help="repair one issuer only")
    parser.add_argument("--limit", type=int, default=0, help="maximum issuers; 0 means all")
    parser.add_argument("--retry", action="store_true", help="include issuers already logged as handled")
    args = parser.parse_args()

    cache_conn = init_cache_db(CACHE_DB_PATH)
    cache_conn.row_factory = sqlite3.Row
    _init_repair_log(cache_conn)
    affected = _affected_issuers(cache_conn, args.issuer, args.retry, args.limit)
    if not affected:
        print("No unpriced cache issuers require repair.")
        cache_conn.close()
        return 0

    print(f"Repairing {len(affected)} issuers with unpriced cache rows.")
    started = time.time()
    succeeded = 0
    failed = 0

    with get_connection() as oracle_conn:
        shared_cache = _QueryCache(oracle_conn)
        for index, row in enumerate(affected, 1):
            issuer = row["issuer"]
            first_date = row["first_unpriced_date"]
            end_date = row["end_date"]
            rows_before = int(row["rows_before"])
            try:
                result = calculate_issuer_deviations(
                    oracle_conn,
                    issuer=issuer,
                    start_date=first_date,
                    end_date=end_date,
                    shared_cache=shared_cache,
                )
                if result["total_bonds"] == 0:
                    _write_log(
                        cache_conn, issuer, "no_issues", first_date, end_date,
                        rows_before, rows_before, "No matching issue facts returned.",
                    )
                    print(f"[{index}/{len(affected)}] {issuer}: no issue facts")
                    continue

                save_issuer_result(cache_conn, result, first_date, end_date)
                rows_remaining = cache_conn.execute("""
                    SELECT COUNT(*)
                    FROM bond_deviations
                    WHERE issuer = ? AND issue_date_rule = ? AND fair_price IS NULL
                """, (issuer, ISSUE_DATE_RULE_VERSION)).fetchone()[0]
                _write_log(
                    cache_conn, issuer, "done", first_date, end_date,
                    rows_before, int(rows_remaining),
                )
                succeeded += 1
                print(
                    f"[{index}/{len(affected)}] {issuer}: "
                    f"priced {result['total_bonds'] - int(rows_remaining)}/{result['total_bonds']}, "
                    f"remaining empty {rows_remaining}"
                )
            except Exception as exc:
                cache_conn.rollback()
                _write_log(
                    cache_conn, issuer, "failed", first_date, end_date,
                    rows_before, None, str(exc),
                )
                failed += 1
                print(f"[{index}/{len(affected)}] {issuer}: failed: {exc}")

    elapsed = (time.time() - started) / 60
    print(f"Finished: {succeeded} repaired, {failed} failed, {elapsed:.1f} minutes.")
    cache_conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
