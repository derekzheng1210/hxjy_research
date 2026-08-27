from __future__ import annotations

from datetime import datetime

from .dm_client_local import (
    DMClient,
    DM_PASSWORD,
    DM_USERNAME,
    choose_value,
    enrich_market_rows,
)
from .storage import save_snapshot


def fetch_and_save_latest(timeout: int = 45) -> dict:
    """Fetch one complete DM best-quote snapshot and atomically replace the cache."""
    client = DMClient(DM_USERNAME, DM_PASSWORD, timeout=timeout)
    try:
        client.login()
        quotes, _audit = client.fetch_partitioned_best_quotes(73_000, 450)
        if not quotes:
            raise RuntimeError("DM 未返回有效经纪商行情")
        bond_ids = [row.get("bondUniCode") for row in quotes if row.get("bondUniCode")]
        bond_info = client.fetch_bond_info(bond_ids)
        bonds_by_code = {}
        for item in bond_info:
            value = choose_value(item, ("bondUniCode", "bond_uni_code", "uniCode"))
            try:
                bonds_by_code[int(value)] = item
            except (TypeError, ValueError):
                continue
        enrich_market_rows(quotes, bonds_by_code)
        return save_snapshot(quotes, datetime.now())
    finally:
        client.close()

