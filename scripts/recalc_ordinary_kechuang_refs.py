"""Recalculate only ordinary bonds currently using exchange Kechuang references.

The script preserves unaffected cache rows. It does not modify cached prices by a
fixed amount: every selected bond is passed through the full pricing calculator.
Run it after deploying the ordinary-credit Kechuang reference rule:

    python recalc_ordinary_kechuang_refs.py
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primary_market_pricing.cache_builder import CACHE_DB_PATH, ISSUE_DATE_RULE_VERSION, init_cache_db, save_issuer_result
from primary_market_pricing.calculator import _QueryCache, _calculate_single_bond_with_cache, is_exchange_kechuang_bond
from primary_market_pricing.data_fetcher import fetch_issuer_outstanding
from primary_market_pricing.db_utils import get_connection


def _fetch_exchange_metadata(conn, symbols: set[str]) -> dict[str, tuple[str, str]]:
    """Return symbol -> (bond name, exchange) without loading the issue universe."""
    metadata: dict[str, tuple[str, str]] = {}
    symbol_list = sorted(symbols)
    for start in range(0, len(symbol_list), 900):
        chunk = symbol_list[start:start + 900]
        binds = {f"p{i}": symbol for i, symbol in enumerate(chunk)}
        placeholders = ", ".join(f":p{i}" for i in range(len(chunk)))
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT SYMBOL, BONDSNAME, EXCHANGE
            FROM TQ_BD_NEWESTBASICINFO
            WHERE ISVALID = 1 AND SYMBOL IN ({placeholders})
            """,
            binds,
        )
        for symbol, bond_name, exchange in cursor.fetchall():
            metadata[str(symbol)] = (str(bond_name or ""), str(exchange or ""))
    return metadata


def _unpriced_result(row: dict, target_exchange: str) -> dict:
    """Keep issuance facts while replacing an unreproducible old pricing result."""
    return {
        "bond_symbol": row["symbol"],
        "bond_name": row["bond_name"],
        "issuer": row["issuer"],
        "coupon_rate": row["coupon_rate"],
        "issue_amount_wan": row["issue_amount_wan"],
        "issue_date": row["issue_date"],
        "maturity_year": row["effective_term"],
        "rating": row["rating"] or "",
        "raise_mode": row["raise_mode"] or "",
        "bond_type": row["bond_type"],
        "cvtbd_expire": row["cvtbd_expire"] or "",
        "is_exchange_kechuang_bond": is_exchange_kechuang_bond(row["bond_name"], target_exchange),
        "kechuang_fallback_to_ordinary": False,
        "ordinary_fallback_to_exchange_kechuang": False,
        "ref_bond_name": "",
        "ref_bond_symbol": "",
        "ref_start_date": "",
        "ref_date_gap_years": None,
        "ref_yield": None,
        "ref_term": None,
        "curve_code": "",
        "curve_at_ref": None,
        "curve_at_target": None,
        "spread": None,
        "fair_price": None,
        "deviation": None,
        "deviation_bp": None,
        "is_non_market": False,
        "is_overpriced": False,
        "is_no_judgement": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report selected bonds without writing cache.db")
    args = parser.parse_args()

    sconn = init_cache_db(CACHE_DB_PATH)
    sconn.row_factory = sqlite3.Row
    rows = sconn.execute(
        """
        SELECT symbol, bond_name, issuer, coupon_rate, issue_amount_wan,
               issue_date, effective_term, rating, raise_mode, bond_type,
               cvtbd_expire, ref_bond_symbol
        FROM bond_deviations
        WHERE issue_date_rule = ?
          AND bond_type = 'ordinary'
          AND ref_bond_symbol IS NOT NULL
          AND ref_bond_symbol <> ''
        """,
        (ISSUE_DATE_RULE_VERSION,),
    ).fetchall()
    cached_rows = [dict(row) for row in rows]
    if not cached_rows:
        print("No cached ordinary bonds with a reference bond were found.")
        return

    symbols = {row["symbol"] for row in cached_rows}
    symbols.update(row["ref_bond_symbol"] for row in cached_rows)
    with get_connection() as oracle_conn:
        metadata = _fetch_exchange_metadata(oracle_conn, symbols)
        targets = [
            row for row in cached_rows
            if row["symbol"] in metadata
            and row["ref_bond_symbol"] in metadata
            and not is_exchange_kechuang_bond(row["bond_name"], metadata[row["symbol"]][1])
            and is_exchange_kechuang_bond(*metadata[row["ref_bond_symbol"]])
        ]
        print(f"Selected {len(targets)} ordinary bonds for full recalculation.")
        if args.dry_run or not targets:
            return

        by_issuer: dict[str, list[dict]] = defaultdict(list)
        for row in targets:
            by_issuer[row["issuer"]].append(row)

        pricing_cache = _QueryCache(oracle_conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_recalculated = 0
        for issuer, issuer_rows in by_issuer.items():
            results = []
            for issue_date in sorted({row["issue_date"] for row in issuer_rows}):
                date_rows = [row for row in issuer_rows if row["issue_date"] == issue_date]
                outstanding = fetch_issuer_outstanding(oracle_conn, issuer, issue_date)
                valuations = (
                    pricing_cache.get_valuations_batch(outstanding["SYMBOL"].tolist(), issue_date)
                    if not outstanding.empty else {}
                )
                for row in date_rows:
                    target_exchange = metadata[row["symbol"]][1]
                    result = None
                    if valuations:
                        result = _calculate_single_bond_with_cache(
                            cache=pricing_cache,
                            bond_symbol=row["symbol"],
                            bond_name=row["bond_name"],
                            issuer=issuer,
                            coupon_rate=row["coupon_rate"],
                            issue_date=issue_date,
                            maturity_year=float(row["effective_term"]),
                            rating=row["rating"] or "",
                            raise_mode=row["raise_mode"] or "",
                            outstanding=outstanding,
                            valuations=valuations,
                            cvtbd_expire=row["cvtbd_expire"],
                            exchange=target_exchange,
                            issue_amount_wan=row["issue_amount_wan"],
                        )
                    results.append(result or _unpriced_result(row, target_exchange))

            bounds = sconn.execute(
                """
                SELECT MIN(issue_date), MAX(issue_date)
                FROM bond_deviations WHERE issuer = ? AND issue_date_rule = ?
                """,
                (issuer, ISSUE_DATE_RULE_VERSION),
            ).fetchone()
            save_issuer_result(
                sconn,
                {"issuer": issuer, "bonds": results},
                bounds[0],
                bounds[1],
            )
            sconn.commit()
            total_recalculated += len(results)
            print(f"{issuer}: recalculated {len(results)} bonds")

    sconn.close()
    print(f"Completed full recalculation for {total_recalculated} cached bonds at {now}.")


if __name__ == "__main__":
    main()
