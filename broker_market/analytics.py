"""Read-only broker analytics using saved quotes, sentiment and deal caches.

Deal caches are a user-query sample, never a full-market transaction tape.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from paths import DATA_DIR
from . import storage


def _mean(values):
    return round(sum(values) / len(values), 3) if values else None


def _counts(stats):
    if not isinstance(stats, dict):
        return None
    result = {}
    for key in ("num", "tkn", "gvn", "trd"):
        value = storage.finite_number(stats.get(key))
        if value is None or value < 0 or not value.is_integer():
            return None
        result[key] = int(value)
    if result["tkn"] + result["gvn"] + result["trd"] > result["num"]:
        return None
    return result


def _ratios(row):
    n = row["num"]
    return {**row, "tkn_pct": round(row["tkn"] / n * 100, 2) if n else None,
            "gvn_pct": round(row["gvn"] / n * 100, 2) if n else None,
            "other_pct": round((n - row["tkn"] - row["gvn"]) / n * 100, 2) if n else None,
            "net": row["tkn"] - row["gvn"]}


def build_dashboard(*, start=None, end=None, scope="all", base_rows=None):
    if scope not in ("all", "tier2"):
        raise ValueError("未知券池")
    history = storage.load_emotion_history().get("points") or []
    snapshot = storage.load_snapshot()
    # Latest available business date is more useful than a blank current window.
    anchor = str(snapshot.get("generated_at") or date.today().isoformat())[:10]
    end = end or anchor
    start = start or (date.fromisoformat(end) - timedelta(days=90)).isoformat()
    for value in (start, end):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("日期格式应为 YYYY-MM-DD")
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    base = base_rows if base_rows is not None else storage.load_current_bond_rows()
    base = [r for r in base if scope == "all" or storage._is_tier2_capital_bond(r)]
    index = {storage.bare_code(r[0]): r for r in base}
    merged = storage.merge_bond_rows(base, snapshot)
    quote_rows, bid_dev, ofr_dev, mids, widths = [], [], [], [], []
    for r in merged:
        bid, ofr, val = r[13], r[14], storage.finite_number(r[5])
        if bid is None and ofr is None:
            continue
        bd = (bid - val) * 100 if bid is not None and val is not None else None
        od = (ofr - val) * 100 if ofr is not None and val is not None else None
        mid = (bd + od) / 2 if bd is not None and od is not None else None
        width = (bid - ofr) * 100 if bid is not None and ofr is not None else None
        for value, arr in ((bd, bid_dev), (od, ofr_dev), (mid, mids), (width, widths)):
            if value is not None:
                arr.append(value)
        quote_rows.append({"code": r[0], "name": r[1], "term": r[2], "rating": r[3],
                           "valuation": val, "bid": bid, "ofr": ofr,
                           "mid_dev": round(mid, 3) if mid is not None else None,
                           "width": round(width, 3) if width is not None else None})
    emotion = storage.calculate_market_emotion(base, snapshot)
    daily = {}
    for p in sorted(history, key=lambda x: x.get("observed_at") or x.get("scheduled_for") or ""):
        day = str(p.get("observed_at") or p.get("scheduled_for") or "")[:10]
        if start <= day <= end:
            v = (p.get("tier2_capital") or {}) if scope == "tier2" else p
            daily[day] = {"date": day, "value": v.get("value"), "count": v.get("count", 0)}
    days = defaultdict(lambda: {"num": 0, "tkn": 0, "gvn": 0, "trd": 0, "bonds": 0})
    bonds, skipped, latest_fetch = [], 0, 0
    cache_dir = DATA_DIR / "dm_deals"
    # No remote fan-out: consume the existing per-bond caches only.
    for folder in sorted(cache_dir.iterdir()) if cache_dir.is_dir() else []:
        if not folder.is_dir() or (scope == "tier2" and folder.name not in index):
            continue
        row = {"num": 0, "tkn": 0, "gvn": 0, "trd": 0}
        active_days = 0
        for path in sorted(folder.glob("????????.json")):
            day = f"{path.stem[:4]}-{path.stem[4:6]}-{path.stem[6:8]}"
            if not start <= day <= end:
                continue
            payload = storage.load_json(path, {})
            if not isinstance(payload, dict):
                skipped += 1
                continue
            stats = payload.get("stats")
            if stats is None and payload.get("minutes"):
                minutes = payload["minutes"]
                cleaned = [_counts(m) for m in minutes] if isinstance(minutes, list) else [None]
                if any(m is None for m in cleaned):
                    skipped += 1
                    continue
                stats = {k: sum(m[k] for m in cleaned) for k in row}
            if not stats:
                continue
            counts = _counts(stats)
            if counts is None:
                skipped += 1
                continue
            latest_fetch = max(latest_fetch, storage.finite_number(payload.get("fetched_at")) or path.stat().st_mtime)
            if counts["num"]:
                active_days += 1
                days[day]["bonds"] += 1
                for k in row:
                    row[k] += counts[k]
                    days[day][k] += counts[k]
        if active_days:
            meta = index.get(folder.name)
            bonds.append(_ratios({**row, "code": meta[0] if meta else folder.name,
                                  "name": meta[1] if meta else "未匹配当前券池", "days": active_days}))
    totals = {k: sum(b[k] for b in bonds) for k in ("num", "tkn", "gvn", "trd")}
    return {"range": {"start": start, "end": end}, "scope": scope,
            "snapshot_at": snapshot.get("generated_at"), "snapshot_in_range": start <= anchor <= end,
            "stale": anchor < date.today().isoformat(),
            "quote_summary": {"pool": len(base), "quoted": len(quote_rows), "two_sided": len(mids),
                              "coverage_pct": round(len(quote_rows) / len(base) * 100, 2) if base else None,
                              "mid_dev": _mean(mids), "bid_dev": _mean(bid_dev), "ofr_dev": _mean(ofr_dev),
                              "width": _mean(widths)},
            "breakdown": emotion["breakdown"], "emotion": list(daily.values()),
            "deals": [_ratios({"date": day, **v}) for day, v in sorted(days.items())],
            "deal_summary": _ratios({**totals, "bonds": len(bonds), "days": len(days)}),
            "deal_cache_updated": datetime.fromtimestamp(latest_fetch).strftime("%Y-%m-%d %H:%M:%S") if latest_fetch else None,
            "skipped": skipped, "active_bonds": sorted(bonds, key=lambda b: -b["num"]),
            "quotes": sorted(quote_rows, key=lambda r: -(abs(r["mid_dev"]) if r["mid_dev"] is not None else -1))}
