"""Incremental government and policy-bank yield curves for institution-flow."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .db import connect, latest_curve_date


CACHE = config.DATA_DIR / "institution_flow" / "rate_curves_cache.json"
CURVES = {"国债": "203", "国开债": "269", "地方债": "479"}
TENORS = [1, 2, 3, 5, 7, 10, 15, 20, 30]
FULL_START = "20220104"
CHUNK_DAYS = 370
WORKERS = 3


def _ymd(value) -> str:
    text = str(value).replace("-", "")
    return text[:8]


def _dash(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _next_date(dash_date: str) -> str:
    return (datetime.strptime(dash_date, "%Y-%m-%d").date() + timedelta(days=1)).strftime("%Y%m%d")


def _chunks(start_ymd: str, end_ymd: str):
    start = datetime.strptime(start_ymd, "%Y%m%d").date()
    end = datetime.strptime(end_ymd, "%Y%m%d").date()
    while start <= end:
        chunk_end = min(start + timedelta(days=CHUNK_DAYS - 1), end)
        yield start.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")
        start = chunk_end + timedelta(days=1)


def _fetch_chunk(code: str, start_ymd: str, end_ymd: str) -> dict:
    binds = {"s": start_ymd, "e": end_ymd, "cc": code}
    binds.update({f"t{i}": tenor for i, tenor in enumerate(TENORS)})
    placeholders = ",".join(f":t{i}" for i in range(len(TENORS)))
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT TRADEDATE, MATURITY, YIELD FROM TQ_QT_YIELDCURVE
            WHERE TRADEDATE >= :s AND TRADEDATE <= :e
              AND YCURVECODE = :cc AND YCURVETYPE = '1'
              AND MATURITY IN ({placeholders}) AND ISVALID = 1
            """,
            binds,
        )
        output = {}
        for trade_date, maturity, value in cursor.fetchall():
            if maturity is None or value is None:
                continue
            output.setdefault(float(maturity), {})[_ymd(trade_date)] = float(value)
        return output


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"curves": {}}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temp = CACHE.with_suffix(".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8", newline="\n")
    os.replace(temp, CACHE)


def _max_date(cache: dict, name: str) -> str | None:
    dates = [
        date
        for tenor in (cache.get("curves", {}).get(name) or {}).values()
        for date in (tenor.get("dates") or [])
    ]
    return max(dates) if dates else None


def _merge(cache: dict, name: str, values: dict) -> int:
    target = cache.setdefault("curves", {}).setdefault(name, {})
    added = 0
    for tenor, points in values.items():
        key = str(int(float(tenor)))
        entry = target.setdefault(key, {"dates": [], "values": []})
        merged = dict(zip(entry.get("dates") or [], entry.get("values") or []))
        before = len(merged)
        for ymd, value in points.items():
            if value is not None:
                merged[_dash(_ymd(ymd))] = round(float(value), 6)
        added += max(0, len(merged) - before)
        dates = sorted(merged)
        entry["dates"] = dates
        entry["values"] = [merged[date] for date in dates]
    return added


def update_rate_curves(progress=None, full: bool = False) -> dict:
    cache = _load_cache()
    with connect() as conn:
        end_ymd = _ymd(latest_curve_date(conn))

    tasks = []
    for name, code in CURVES.items():
        latest = _max_date(cache, name)
        start = FULL_START if full or not latest else _next_date(latest)
        if start <= end_ymd:
            tasks.extend((name, code, start, end) for start, end in _chunks(start, end_ymd))

    if progress:
        progress(f"机构行为曲线：准备更新 {len(tasks)} 个分段", 5)
    results = {name: {} for name in CURVES}
    completed = 0
    if tasks:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(_fetch_chunk, code, start, end): (name, start, end)
                for name, code, start, end in tasks
            }
            for future in as_completed(futures):
                name, start, end = futures[future]
                for tenor, points in (future.result() or {}).items():
                    results[name].setdefault(tenor, {}).update(points)
                completed += 1
                if progress:
                    progress(
                        f"机构行为曲线：{name} {_dash(start)}~{_dash(end)} 完成",
                        5 + int(completed / len(tasks) * 85),
                    )

    added = {name: _merge(cache, name, values) for name, values in results.items()}
    dates = sorted(
        {
            date
            for curve in (cache.get("curves") or {}).values()
            for tenor in curve.values()
            for date in tenor.get("dates") or []
        }
    )
    cache["meta"] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "tenors": TENORS,
    }
    _save_cache(cache)
    if progress:
        progress(f"机构行为曲线更新完成：新增 {sum(added.values())} 个数据点", 100)
    return {"added": added, "end_date": cache["meta"]["end_date"], "path": str(CACHE)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="更新机构行为国债/国开债/地方债收益率曲线")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    print(update_rate_curves(full=args.full))
