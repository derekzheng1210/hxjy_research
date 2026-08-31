"""超长端利率利差跟踪 - Blueprint,挂载于 /spread。"""
from __future__ import annotations

import logging
import statistics
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request

from . import config, db, scheduler, updater

log = logging.getLogger("spread")

bp = Blueprint("spread", __name__, url_prefix="/spread")


def _db_path():
    return current_app.config.get("SPREAD_DB")


RANGE_DAYS = {"3m": 92, "6m": 184, "1y": 366, "2y": 731, "3y": 1096, "5y": 1827}


def _fmt_date(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _quantile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位数,q∈[0,1];入参须已升序。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _series_stats(values: list[float], current: float) -> dict:
    mean = statistics.fmean(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    lo, hi = min(values), max(values)
    below = sum(1 for v in values if v <= current)
    sv = sorted(values)
    return {
        "mean": round(mean, 1),
        "std": round(std, 1),
        "min": round(lo, 1),
        "max": round(hi, 1),
        "pct": round(100.0 * below / len(values), 1) if values else None,
        "median": round(_quantile(sv, 0.5), 1),
        "p25": round(_quantile(sv, 0.25), 1),
        "p75": round(_quantile(sv, 0.75), 1),
    }


@bp.get("/")
def index():
    return render_template(
        "interest_bond_spread.html",
        spreads=config.SPREADS,
        curves={k: {"name": v["name"], "tenors": list(v["tenors"])} for k, v in config.CURVES.items()},
    )


@bp.get("/api/data")
def api_data():
    start = request.args.get("start")
    end = request.args.get("end")
    rng = request.args.get("range", "3y")
    if not start:
        if rng != "all":
            days = RANGE_DAYS.get(rng, 1096)
            start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    points = db.load_points(start=start, end=end, db_path=_db_path())
    # 全部交易日(并集),升序
    dates = sorted({d for by_tenor in points.values() for m in by_tenor.values() for d in m})

    def yield_at(curve_id: str, tenor: float, d: str) -> float | None:
        return points.get(curve_id, {}).get(tenor, {}).get(d)

    spread_series: dict[str, dict] = {s["key"]: {} for s in config.SPREADS}
    yield_series: dict[str, dict] = {}
    for d in dates:
        for s in config.SPREADS:
            (lc, lt), (sc, st) = s["long"], s["short"]
            lv, sv = yield_at(lc, lt, d), yield_at(sc, st, d)
            if lv is not None and sv is not None:
                spread_series[s["key"]][d] = (lv - sv) * 100.0
        for cid, meta in config.CURVES.items():
            for tenor in meta["tenors"]:
                v = yield_at(cid, tenor, d)
                if v is not None:
                    key = f"{cid}{int(tenor) if tenor == int(tenor) else tenor}"
                    yield_series.setdefault(key, {})[d] = v

    dates_fmt = [_fmt_date(d) for d in dates]

    result_spreads = []
    for s in config.SPREADS:
        series = spread_series[s["key"]]
        vals = [v for v in series.values()]
        latest_d = max(series) if series else None
        prev_vals = [series[d] for d in sorted(series)[:-1]] if len(series) > 1 else []
        prev = prev_vals[-1] if prev_vals else None
        week = prev_vals[-6] if len(prev_vals) >= 6 else None
        current = series.get(latest_d)
        result_spreads.append(
            {
                "key": s["key"],
                "name": s["name"],
                "desc": s["desc"],
                "color": s["color"],
                "dates": [_fmt_date(d) for d in series],
                "values": [series[d] for d in sorted(series)],
                "latest_date": _fmt_date(latest_d) if latest_d else None,
                "latest": round(current, 2) if current is not None else None,
                "chg_1d": round(current - prev, 2) if current is not None and prev is not None else None,
                "chg_5d": round(current - week, 2) if current is not None and week is not None else None,
                "stats": _series_stats(vals, current) if vals and current is not None else None,
            }
        )

    yield_out = []
    yield_defs = [
        ("treasury10", "国债10Y", "#4361ee"),
        ("treasury30", "国债30Y", "#7c3aed"),
        ("treasury50", "国债50Y", "#f59e0b"),
        ("local10", "地方债10Y", "#0ea5e9"),
        ("local30", "地方债30Y", "#10b981"),
    ]
    for key, name, color in yield_defs:
        series = yield_series.get(key, {})
        if not series:
            continue
        yield_out.append(
            {
                "key": key,
                "name": name,
                "color": color,
                "dates": [_fmt_date(d) for d in sorted(series)],
                "values": [series[d] for d in sorted(series)],
            }
        )

    last_run = db.last_successful_run(db_path=_db_path())
    cov = db.coverage(db_path=_db_path())
    return jsonify(
        {
            "dates": dates_fmt,
            "spreads": result_spreads,
            "yields": yield_out,
            "last_run": dict(last_run) if last_run else None,
            "coverage": cov,
        }
    )


def init_app() -> None:
    """初始化本地库、启动定时调度,并在无数据/数据过期时自动补更。"""
    db.initialize()
    scheduler.start()
    status = updater.status()
    if not db.max_trade_date("treasury") and not status["running"]:
        log.info("本地无数据,自动触发首次全量回补")
        updater.run_async("full")
    elif updater.is_stale() and not status["running"]:
        log.info("数据已过期,启动时自动增量更新")
        updater.run_async("incremental")
