"""Selectively migrate the pricing cache to the v7 broker-subordinated rule.

Run directly from the portal root:

    python primary_market_pricing/migrate_cache_v6_to_v7_broker_subordinated.py

The default performs the migration. ``--dry-run`` only reports the affected
scope. Every completed issuer is committed and recorded in SQLite, so an
interrupted run resumes from the next issuer.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

if __package__:
    from . import config
    from .cache_builder import CACHE_DB_PATH, ISSUE_DATE_RULE_VERSION, save_issuer_result
    from .calculator import (
        _QueryCache,
        _calculate_single_bond_with_cache,
        classify_bond_type,
        is_broker_subordinated_bond,
    )
    from .data_fetcher import fetch_issuer_outstanding
    from .db_utils import get_connection
else:
    # Support direct execution with ``python path\\to\\script.py``.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from primary_market_pricing import config
    from primary_market_pricing.cache_builder import (
        CACHE_DB_PATH,
        ISSUE_DATE_RULE_VERSION,
        save_issuer_result,
    )
    from primary_market_pricing.calculator import (
        _QueryCache,
        _calculate_single_bond_with_cache,
        classify_bond_type,
        is_broker_subordinated_bond,
    )
    from primary_market_pricing.data_fetcher import fetch_issuer_outstanding
    from primary_market_pricing.db_utils import get_connection


OLD_RULE_VERSION = "issue_date_v6"
PROGRESS_TABLE = "broker_subordinated_v7_migration_progress"
MIGRATION_REVISION = "cache_type_comparison_v2"

if hasattr(sys.stdout, "reconfigure"):
    # Make per-issuer progress visible immediately in PowerShell.
    sys.stdout.reconfigure(line_buffering=True, errors="replace")


def _legacy_broker_subordinated(name: str, exchange: str) -> bool:
    return (
        "C" in str(name or "").upper()
        and str(exchange or "").strip() in config.EXCHANGE_BOND_EXCHANGES
    )


def _fetch_changed_bonds(oracle_conn) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return all metadata and symbols whose inferred v6/v7 classes differ."""
    cursor = oracle_conn.cursor()
    cursor.execute("""
        SELECT SYMBOL, BONDSNAME, COMPNAME, RAISEMODE, STARTDATE, MATURITYDATE, EXCHANGE
        FROM TQ_BD_NEWESTBASICINFO
        WHERE ISVALID = 1
          AND BONDTYPE1 IN ('5', '6')
          AND SYMBOL IS NOT NULL
    """)

    by_symbol: dict[str, dict] = {}
    for symbol, name, issuer, raise_mode, start_date, maturity_date, exchange in cursor.fetchall():
        symbol = str(symbol)
        item = by_symbol.setdefault(symbol, {
            "symbol": symbol,
            "name": str(name or ""),
            "issuer": str(issuer or ""),
            "raise_modes": set(),
            "start_date": str(start_date or ""),
            "maturity_date": str(maturity_date or ""),
            "old": False,
            "exchange": str(exchange or ""),
        })
        item["raise_modes"].add(str(raise_mode or "").strip())
        item["old"] = item["old"] or _legacy_broker_subordinated(name, exchange)
        # Use the widest observed outstanding interval when a SYMBOL has duplicate metadata.
        start = str(start_date or "")
        maturity = str(maturity_date or "")
        if start and (not item["start_date"] or start < item["start_date"]):
            item["start_date"] = start
        if maturity and (not item["maturity_date"] or maturity > item["maturity_date"]):
            item["maturity_date"] = maturity

    changed = {}
    for symbol, item in by_symbol.items():
        item["new"] = is_broker_subordinated_bond(item["issuer"], item["name"])
        if item["old"] != item["new"]:
            changed[symbol] = item
    return by_symbol, changed


def _add_cached_type_mismatches(
    cache_conn: sqlite3.Connection,
    source_rule_version: str,
    metadata: dict[str, dict],
    changed: dict[str, dict],
) -> int:
    """Add rows where the stored cache type conflicts with the new classifier.

    A symbol can have both eligible and ineligible EXCHANGE rows.  The former
    v6 classifier then looks correct when evaluated against the source table,
    while the cache may actually have kept an ineligible row and stored
    ``ordinary``.  Cache state is authoritative for deciding what to repair.
    """
    mismatches = 0
    rows = cache_conn.execute(
        "SELECT symbol, bond_name, issuer, bond_type FROM bond_deviations WHERE issue_date_rule = ?",
        (source_rule_version,),
    ).fetchall()
    for symbol, bond_name, issuer, bond_type in rows:
        symbol = str(symbol)
        cached_is_broker = str(bond_type or "") == "broker_subordinated"
        new_is_broker = is_broker_subordinated_bond(str(issuer or ""), str(bond_name or ""))
        if cached_is_broker == new_is_broker:
            continue
        item = metadata.get(symbol)
        if not item:
            continue
        if symbol not in changed:
            mismatches += 1
        changed[symbol] = item
    return mismatches


def _affected_cached_rows(
    cache_conn: sqlite3.Connection,
    changed: dict[str, dict],
    source_rule_version: str,
) -> tuple[list[dict], dict[str, set[str]]]:
    cache_conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in cache_conn.execute("""
        SELECT symbol, bond_name, issuer, coupon_rate, issue_amount_wan, issue_date,
               effective_term, rating, raise_mode, bond_type, cvtbd_expire
        FROM bond_deviations
        WHERE issue_date_rule = ?
    """, (source_rule_version,)).fetchall()]
    by_issuer: dict[str, list[dict]] = defaultdict(list)
    for item in changed.values():
        by_issuer[item["issuer"]].append(item)

    affected = []
    reasons: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        symbol = str(row["symbol"])
        issue_date = str(row["issue_date"] or "")
        if symbol in changed:
            reasons[symbol].add("新发债自身券种分类变化")
        for candidate in by_issuer.get(str(row["issuer"] or ""), []):
            candidate_raises = candidate["raise_modes"]
            same_raise_mode = not candidate_raises or str(row["raise_mode"] or "").strip() in candidate_raises
            is_outstanding = (
                candidate["start_date"]
                and candidate["maturity_date"]
                and candidate["start_date"] < issue_date < candidate["maturity_date"]
            )
            if same_raise_mode and is_outstanding:
                reasons[symbol].add(f"存续候选券 {candidate['symbol']} 的券种分类变化")
        if reasons[symbol]:
            affected.append(row)
    return affected, reasons


def _target_exchange_map(oracle_conn, symbols: set[str]) -> dict[str, str]:
    """Fetch a deterministic exchange only for the target's Kechuang rule."""
    exchanges: dict[str, list[str]] = defaultdict(list)
    symbol_list = sorted(symbols)
    for offset in range(0, len(symbol_list), 900):
        chunk = symbol_list[offset:offset + 900]
        binds = {f"p{i}": value for i, value in enumerate(chunk)}
        placeholders = ", ".join(f":p{i}" for i in range(len(chunk)))
        cursor = oracle_conn.cursor()
        cursor.execute(
            f"SELECT SYMBOL, EXCHANGE FROM TQ_BD_NEWESTBASICINFO "
            f"WHERE ISVALID = 1 AND SYMBOL IN ({placeholders})",
            binds,
        )
        for symbol, exchange in cursor.fetchall():
            exchanges[str(symbol)].append(str(exchange or ""))
    return {
        symbol: next(
            (value for value in values if value in config.EXCHANGE_BOND_EXCHANGES),
            sorted(values)[0] if values else "",
        )
        for symbol, values in exchanges.items()
    }


def _unpriced_result(row: dict) -> dict:
    name = str(row["bond_name"] or "")
    bond_type = classify_bond_type(name, row["cvtbd_expire"])
    if is_broker_subordinated_bond(row["issuer"], name):
        bond_type = "broker_subordinated"
    return {
        "bond_symbol": row["symbol"], "bond_name": name, "issuer": row["issuer"],
        "coupon_rate": row["coupon_rate"], "issue_amount_wan": row["issue_amount_wan"],
        "issue_date": row["issue_date"], "maturity_year": row["effective_term"],
        "rating": row["rating"] or "", "raise_mode": row["raise_mode"] or "",
        "bond_type": bond_type, "cvtbd_expire": row["cvtbd_expire"] or "",
        "ref_bond_name": "", "ref_bond_symbol": "", "ref_start_date": "",
        "ref_date_gap_years": None, "ref_yield": None, "ref_term": None,
        "curve_code": "", "curve_at_ref": None, "curve_at_target": None,
        "spread": None, "fair_price": None, "deviation": None, "deviation_bp": None,
        "is_non_market": False, "is_overpriced": False, "is_no_judgement": True,
    }


def _backup_database(cache_conn: sqlite3.Connection) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{CACHE_DB_PATH}.backup_v7_broker_{timestamp}"
    backup_conn = sqlite3.connect(backup_path)
    try:
        cache_conn.backup(backup_conn)
    finally:
        backup_conn.close()
    return backup_path


def _init_progress_table(cache_conn: sqlite3.Connection) -> None:
    cache_conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {PROGRESS_TABLE} (
            issuer TEXT PRIMARY KEY,
            completed_at TEXT NOT NULL,
            recalculated_rows INTEGER NOT NULL,
            revision TEXT NOT NULL DEFAULT ''
        )
    """)
    columns = {row[1] for row in cache_conn.execute(f"PRAGMA table_info({PROGRESS_TABLE})")}
    if "revision" not in columns:
        cache_conn.execute(
            f"ALTER TABLE {PROGRESS_TABLE} ADD COLUMN revision TEXT NOT NULL DEFAULT ''"
        )
    cache_conn.commit()


def migrate(dry_run: bool = False) -> None:

    cache_conn = sqlite3.connect(CACHE_DB_PATH, timeout=30)
    try:
        old_count = cache_conn.execute(
            "SELECT COUNT(*) FROM bond_deviations WHERE issue_date_rule = ?", (OLD_RULE_VERSION,)
        ).fetchone()[0]
        new_count = cache_conn.execute(
            "SELECT COUNT(*) FROM bond_deviations WHERE issue_date_rule = ?", (ISSUE_DATE_RULE_VERSION,)
        ).fetchone()[0]
        if old_count and new_count:
            raise RuntimeError(
                f"Both {OLD_RULE_VERSION} and {ISSUE_DATE_RULE_VERSION} exist; "
                "refusing to merge two cache versions."
            )
        if not old_count and not new_count:
            raise RuntimeError("Neither v6 nor v7 cache rows were found.")
        source_rule_version = OLD_RULE_VERSION if old_count else ISSUE_DATE_RULE_VERSION
        is_resume = source_rule_version == ISSUE_DATE_RULE_VERSION

        with get_connection() as oracle_conn:
            metadata, changed = _fetch_changed_bonds(oracle_conn)
            cache_mismatches = _add_cached_type_mismatches(
                cache_conn, source_rule_version, metadata, changed
            )
            affected, reasons = _affected_cached_rows(cache_conn, changed, source_rule_version)
            issuers = sorted({row["issuer"] for row in affected})
            additions = sum(1 for item in changed.values() if item["new"])
            removals = len(changed) - additions
            print(f"source cache version: {source_rule_version} ({old_count or new_count} rows)")
            print(f"classification changes: {len(changed)} (add {additions}, remove {removals})")
            print(f"cache type mismatches added to repair scope: {cache_mismatches}")
            print(f"recalculate rows: {len(affected)} across {len(issuers)} issuers")
            print("  add: securities issuer + bond name ending C followed by digits")
            print("  remove: legacy non-securities false positives")
            if is_resume:
                print("Resume mode: v7 cache already exists.")
            if dry_run:
                print("Dry-run only. Run without --dry-run to migrate.")
                return

            _init_progress_table(cache_conn)
            completed = {
                row[0] for row in cache_conn.execute(
                    f"SELECT issuer FROM {PROGRESS_TABLE} WHERE revision = ?",
                    (MIGRATION_REVISION,),
                ).fetchall()
            }
            backup_path = _backup_database(cache_conn)
            print(f"backup created: {backup_path}")
            if not is_resume:
                cache_conn.execute(
                    "UPDATE bond_deviations SET issue_date_rule = ? WHERE issue_date_rule = ?",
                    (ISSUE_DATE_RULE_VERSION, OLD_RULE_VERSION),
                )
                cache_conn.execute(
                    "UPDATE issuer_summary SET issue_date_rule = ? WHERE issue_date_rule = ?",
                    (ISSUE_DATE_RULE_VERSION, OLD_RULE_VERSION),
                )
                cache_conn.commit()

            exchanges = _target_exchange_map(oracle_conn, {str(row["symbol"]) for row in affected})
            pricing_cache = _QueryCache(oracle_conn)
            grouped: dict[str, list[dict]] = defaultdict(list)
            for row in affected:
                grouped[row["issuer"]].append(row)

            ordered = sorted(grouped)
            pending = [issuer for issuer in ordered if issuer not in completed]
            print(f"pending issuers: {len(pending)} (already completed: {len(ordered) - len(pending)})")
            total_start = datetime.now()
            for index, issuer in enumerate(pending, 1):
                issuer_rows = grouped[issuer]
                started = datetime.now()
                try:
                    results = []
                    for issue_date in sorted({str(row["issue_date"]) for row in issuer_rows}):
                        date_rows = [row for row in issuer_rows if str(row["issue_date"]) == issue_date]
                        outstanding = fetch_issuer_outstanding(oracle_conn, issuer, issue_date)
                        valuations = (
                            pricing_cache.get_valuations_batch(outstanding["SYMBOL"].tolist(), issue_date)
                            if not outstanding.empty else {}
                        )
                        for row in date_rows:
                            result = None
                            if not outstanding.empty and valuations:
                                result = _calculate_single_bond_with_cache(
                                    cache=pricing_cache, bond_symbol=row["symbol"], bond_name=row["bond_name"],
                                    issuer=issuer, coupon_rate=row["coupon_rate"], issue_date=issue_date,
                                    maturity_year=float(row["effective_term"]), rating=row["rating"] or "",
                                    raise_mode=row["raise_mode"] or "", outstanding=outstanding,
                                    valuations=valuations, cvtbd_expire=row["cvtbd_expire"],
                                    exchange=exchanges.get(str(row["symbol"]), ""),
                                    issue_amount_wan=row["issue_amount_wan"],
                                )
                            results.append(result or _unpriced_result(row))

                    bounds = cache_conn.execute(
                        "SELECT MIN(issue_date), MAX(issue_date) FROM bond_deviations "
                        "WHERE issuer = ? AND issue_date_rule = ?",
                        (issuer, ISSUE_DATE_RULE_VERSION),
                    ).fetchone()
                    save_issuer_result(
                        cache_conn, {"issuer": issuer, "bonds": results}, bounds[0], bounds[1]
                    )
                    cache_conn.execute(
                        f"INSERT OR REPLACE INTO {PROGRESS_TABLE} "
                        "(issuer, completed_at, recalculated_rows, revision) VALUES (?, ?, ?, ?)",
                        (
                            issuer,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            len(results),
                            MIGRATION_REVISION,
                        ),
                    )
                    cache_conn.commit()
                    elapsed = (datetime.now() - started).total_seconds()
                    average = (datetime.now() - total_start).total_seconds() / index
                    eta_minutes = average * (len(pending) - index) / 60
                    print(
                        f"[{index}/{len(pending)}] {issuer} | {len(results)} rows | "
                        f"{elapsed:.1f}s | ETA {eta_minutes:.0f} min"
                    )
                except Exception as exc:
                    cache_conn.rollback()
                    print(f"[{index}/{len(pending)}] {issuer} | ERROR: {exc}")

            final_completed = cache_conn.execute(
                f"SELECT COUNT(*) FROM {PROGRESS_TABLE} WHERE revision = ?",
                (MIGRATION_REVISION,),
            ).fetchone()[0]
            print("\n" + "=" * 55)
            print(f"Migration complete. Progress: {final_completed}/{len(ordered)} issuers.")
            print("=" * 55)
    finally:
        cache_conn.close()


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
