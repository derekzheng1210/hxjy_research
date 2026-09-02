from __future__ import annotations

import hashlib
import math
import statistics
import threading
from bisect import bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from broker_market.storage import (
    HISTORY_DIR,
    OUTLIER_THRESHOLD_BP,
    bare_code,
    finite_number,
    list_quote_history,
    load_json as load_market_json,
    load_snapshot,
    normalize_code,
)
from juyuan_update import config
from juyuan_update.db import connect
from juyuan_update.generators import curve_for_bond, read_js_data
from juyuan_update.unified_excel import (
    get_spread_monitor_bonds,
    load_bond_picker_yields_cache,
    load_json,
    load_spread_history_cache,
)
HOLDING_DAYS_BY_MONTHS = {3: 91, 6: 182}

# 主体曲线外推：目标期限落在样本区间外时，样本（不含目标债）不少于该数量才允许外推。
# 偏离边界不超过 EXTRAPOLATION_LINEAR_MAX_GAP 年时用最小二乘线性外推；更远时改用
# “最近样本相对同隐含评级曲线的平均利差 + 评级曲线在目标期限收益率”推算，可达距离
# 不超过 max(EXTRAPOLATION_SPREAD_MIN_REACH, 样本区间跨度×比例)
EXTRAPOLATION_MIN_SAMPLES = 4
EXTRAPOLATION_LINEAR_MAX_GAP = 1.0
EXTRAPOLATION_SPREAD_MIN_REACH = 2.0
EXTRAPOLATION_SPREAD_REACH_RATIO = 1.0

CURVE_TO_STD_CATEGORY = {
    "中短票AAA": "中短票AAA-国开",
    "中短票AA+": "中短票AA+-国开",
    "中短票AA": "中短票AA-国开",
    "大行二级资本债": "大行二级资本债-国开",
    "股份行二级资本债": "股份行二级资本债-国开",
}

_instrument_cache: dict[str, dict[str, Any]] = {}
_instrument_lock = threading.Lock()


def _number(value: Any, digits: int = 4) -> float | None:
    result = finite_number(value)
    return round(result, digits) if result is not None else None


def _date_text(value: Any) -> str:
    text = str(value or "").strip().replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit() or text <= "19010101":
        return ""
    try:
        return datetime.strptime(text, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _parse_date(value: Any) -> date | None:
    text = _date_text(value)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date() if text else None
    except ValueError:
        return None


def _bond_indexes() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    bonds = get_spread_monitor_bonds()
    by_code: dict[str, dict[str, Any]] = {}
    by_issuer: dict[str, list[dict[str, Any]]] = {}
    for bond in bonds:
        code = normalize_code(bond.get("code"))
        if not code:
            continue
        by_code[code] = bond
        by_code.setdefault(bare_code(code), bond)
        issuer = str(bond.get("issuer") or "").strip()
        if issuer:
            by_issuer.setdefault(issuer, []).append(bond)
    return bonds, by_code, by_issuer


def _yield_indexes() -> tuple[dict[str, float], dict[str, Any]]:
    payload = load_bond_picker_yields_cache()
    values: dict[str, float] = {}
    for raw_code, raw_value in (payload.get("yields") or {}).items():
        value = finite_number(raw_value)
        code = normalize_code(raw_code)
        if value is None or not code:
            continue
        values[code] = value
        values.setdefault(bare_code(code), value)
    return values, payload


def search_bonds(query: str, limit: int = 20) -> list[dict[str, Any]]:
    query = str(query or "").strip().lower()
    if not query:
        return []
    bonds, _, _ = _bond_indexes()
    yields, _ = _yield_indexes()
    matches = []
    for bond in bonds:
        code = normalize_code(bond.get("code"))
        haystack = " ".join(
            [code, str(bond.get("name") or ""), str(bond.get("issuer") or "")]
        ).lower()
        if query not in haystack:
            continue
        prefix = 0 if code.lower().startswith(query) else 1
        name_prefix = 0 if str(bond.get("name") or "").lower().startswith(query) else 1
        matches.append((prefix, name_prefix, code, bond, yields.get(code) or yields.get(bare_code(code))))
    matches.sort(key=lambda item: item[:3])
    try:
        safe_limit = max(1, min(int(limit or 20), 50))
    except (TypeError, ValueError):
        safe_limit = 20
    return [
        {
            "code": code,
            "name": bond.get("name") or "",
            "issuer": bond.get("issuer") or "",
            "term": _number(bond.get("term")),
            "implied_rating": bond.get("implied_rating") or "",
            "yield": _number(ytm),
        }
        for _, _, code, bond, ytm in matches[:safe_limit]
    ]


def interpolate_points(points: Iterable[tuple[float, float]], tenor: float, *, extrapolate: bool = False) -> float | None:
    clean = sorted(
        (float(t), float(v))
        for t, v in points
        if finite_number(t) is not None and finite_number(v) is not None
    )
    if not clean:
        return None
    tenor = float(tenor)
    exact = [value for term, value in clean if abs(term - tenor) < 1e-9]
    if exact:
        return statistics.median(exact)
    grouped: dict[float, list[float]] = {}
    for term, value in clean:
        grouped.setdefault(term, []).append(value)
    clean = [(term, statistics.median(values)) for term, values in sorted(grouped.items())]
    if tenor < clean[0][0]:
        return clean[0][1] if extrapolate else None
    if tenor > clean[-1][0]:
        return clean[-1][1] if extrapolate else None
    for (t1, y1), (t2, y2) in zip(clean, clean[1:]):
        if t1 <= tenor <= t2:
            return y1 + (y2 - y1) * (tenor - t1) / (t2 - t1)
    return None


def _curve_cache() -> tuple[str, dict[str, dict[str, float]], dict[str, Any]]:
    payload = load_json(config.STD_DEV_CURVES_CACHE, {})
    curves_by_date = payload.get("curves_by_date") or {}
    if not curves_by_date:
        return "", {}, payload
    curve_date = str(payload.get("oracle_latest") or max(curves_by_date))
    day = curves_by_date.get(curve_date) or curves_by_date.get(curve_date[:10])
    if not day:
        curve_date = max(curves_by_date)
        day = curves_by_date[curve_date]
    return curve_date, day, payload


def _curve_yield(day: dict[str, dict[str, float]], curve_name: str, tenor: float) -> float | None:
    points = [(float(term), value) for term, value in (day.get(curve_name) or {}).items()]
    return interpolate_points(points, tenor, extrapolate=False)


def _rating_curve_overlay(
    day: dict[str, dict[str, float]], curve_name: str, terms: Iterable[float]
) -> list[dict[str, float]]:
    raw = sorted(
        (float(term), float(value))
        for term, value in (day.get(curve_name) or {}).items()
        if finite_number(term) is not None and finite_number(value) is not None
    )
    clean_terms = [float(term) for term in terms if finite_number(term) is not None]
    if not raw or not clean_terms:
        return []
    low, high = min(clean_terms), max(clean_terms)
    if high - low < 0.5:
        center = (low + high) / 2
        low, high = max(raw[0][0], center - 0.5), min(raw[-1][0], center + 0.5)
    points = [(term, value) for term, value in raw if low <= term <= high]
    for boundary in (low, high):
        value = interpolate_points(raw, boundary, extrapolate=False)
        if value is not None:
            points.append((boundary, value))
    # 整年补点：评级曲线在每个整数年都有可悬停的数据点（曲线范围外的年份跳过）
    for year in range(1, math.floor(high) + 1):
        if year < low:
            continue
        value = interpolate_points(raw, float(year), extrapolate=False)
        if value is not None:
            points.append((float(year), value))
    deduped = {round(term, 6): value for term, value in points}
    return [
        {"term": round(term, 4), "yield": round(value, 4)}
        for term, value in sorted(deduped.items())
    ]


def _series_for_exact_tenor(std_data: dict[str, Any], category: str, tenor: float) -> list[tuple[str, float]]:
    by_date: dict[str, list[tuple[float, float]]] = {}
    prefix = category + "_"
    for key, series in (std_data.get("data") or {}).items():
        if not str(key).startswith(prefix):
            continue
        label = str(key)[len(prefix):]
        try:
            term = 0.08 if label == "1M" else float(label.rstrip("Y"))
        except ValueError:
            continue
        for dt, value in zip(series.get("dates") or [], series.get("values") or []):
            if finite_number(value) is not None:
                by_date.setdefault(str(dt)[:10], []).append((term, float(value)))
    result = []
    for dt in sorted(by_date):
        value = interpolate_points(by_date[dt], tenor, extrapolate=False)
        if value is not None:
            result.append((dt, value * 100.0))
    return result


def rating_band_metrics(std_data: dict[str, Any], category: str, tenor: float) -> dict[str, Any]:
    series = _series_for_exact_tenor(std_data, category, tenor)
    if not series:
        return {"available": False, "reason": "该期限暂无两倍标准差历史"}
    values = [value for _, value in series]
    current = values[-1]
    window = values[-30:]
    mean = statistics.fmean(window)
    sigma = statistics.pstdev(window) if len(window) > 1 else 0.0
    upper = mean + 2 * sigma
    lower = mean - 2 * sigma
    zscore = (current - mean) / sigma if sigma > 1e-12 else 0.0
    percentile = sum(value <= current for value in values) / len(values) * 100
    current_date = datetime.strptime(series[-1][0], "%Y-%m-%d").date()
    target = (current_date - timedelta(days=7)).isoformat()
    dates = [dt for dt, _ in series]
    idx = bisect_right(dates, target) - 1
    week_value = series[idx][1] if idx >= 0 else None
    if current > upper:
        status = "上轨以上"
    elif current < lower:
        status = "下轨以下"
    else:
        status = "轨道区间内"
    return {
        "available": True,
        "category": category,
        "tenor": round(float(tenor), 4),
        "date": series[-1][0],
        "credit_spread_bp": round(current, 2),
        "ma30_bp": round(mean, 2),
        "upper_2sigma_bp": round(upper, 2),
        "lower_2sigma_bp": round(lower, 2),
        "zscore": round(zscore, 2),
        "percentile": round(percentile, 1),
        "week_change_bp": round(current - week_value, 2) if week_value is not None else None,
        "status": status,
        "sample_days": len(series),
        "history": [{"date": dt, "value": round(value, 2)} for dt, value in series[-90:]],
    }


def _same_curve_group(target: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if str(candidate.get("guarantor") or "").strip():
        return False
    return str(candidate.get("sub") or "否") == str(target.get("sub") or "否")


def _is_exchange_tech_bond(bond: dict[str, Any]) -> bool:
    code = normalize_code(bond.get("code"))
    name = str(bond.get("name") or "").upper()
    is_exchange = code.endswith((".SH", ".SZ"))
    is_tech = str(bond.get("tech") or "否") == "是" or "科创" in name or "K" in name
    return is_exchange and is_tech


def _mad(values: list[float]) -> float:
    if not values:
        return 0.0
    center = statistics.median(values)
    return statistics.median(abs(value - center) for value in values)


def _extrapolate_issuer_curve(
    samples: list[dict[str, Any]], target_term: float, rating_curve_yield=None,
) -> tuple[float | None, str]:
    """目标期限落在样本区间外时的外推，返回 (公平收益率, 外推说明)。

    偏离边界不超过 EXTRAPOLATION_LINEAR_MAX_GAP 年：边界最近 2-3 只样本
    最小二乘线性外推；偏离更远：最近 2-3 只样本相对同隐含评级曲线的平均
    利差，加到评级曲线在目标期限的收益率上（远端曲线形态由评级曲线承载，
    避免直线外推失真）。不满足条件时返回 (None, 不外推原因)。
    """
    if len(samples) < EXTRAPOLATION_MIN_SAMPLES:
        return None, f"单侧样本不足{EXTRAPOLATION_MIN_SAMPLES}只，不做外推"
    terms = [row["term"] for row in samples]
    lo, hi = min(terms), max(terms)
    if target_term > hi:
        gap = target_term - hi
        nearest = sorted(samples, key=lambda row: row["term"])[-3:]
        direction = "右侧"
    else:
        gap = lo - target_term
        nearest = sorted(samples, key=lambda row: row["term"])[:3]
        direction = "左侧"
    if len(nearest) < 2:
        return None, "可用样本不足，不做外推"
    count = len(nearest)
    reach = max(EXTRAPOLATION_SPREAD_MIN_REACH, (hi - lo) * EXTRAPOLATION_SPREAD_REACH_RATIO)
    if gap > reach:
        return None, "目标期限超出样本区间过远，不做外推"
    if gap > EXTRAPOLATION_LINEAR_MAX_GAP:
        # 偏离较远：按最近样本相对隐含评级曲线的平均利差推算
        if rating_curve_yield is None:
            return None, (
                f"目标期限{direction}偏离边界超过{EXTRAPOLATION_LINEAR_MAX_GAP:g}年，"
                "且隐含评级曲线不可用，无法按利差外推"
            )
        curve_at_target = rating_curve_yield(target_term)
        curve_at_samples = [rating_curve_yield(row["term"]) for row in nearest]
        if curve_at_target is None or any(value is None for value in curve_at_samples):
            return None, "隐含评级曲线在样本或目标期限处无收益率，无法按利差外推"
        spreads = [row["yield"] - value for row, value in zip(nearest, curve_at_samples)]
        fair = curve_at_target + sum(spreads) / len(spreads)
        if fair <= 0:
            return None, "外推收益率非正，结果不可信"
        return fair, (
            f"目标期限{direction}无样本且偏离边界{gap:.2f}年，按最近{count}只样本"
            "相对同隐含评级曲线的平均利差外推"
        )
    # 偏离较近：边界最近样本最小二乘线性外推
    mean_x = sum(row["term"] for row in nearest) / count
    mean_y = sum(row["yield"] for row in nearest) / count
    var_x = sum((row["term"] - mean_x) ** 2 for row in nearest)
    if var_x <= 1e-12:
        return None, "边界样本期限重复，无法估计斜率"
    slope = sum(
        (row["term"] - mean_x) * (row["yield"] - mean_y) for row in nearest
    ) / var_x
    fair = mean_y + slope * (target_term - mean_x)
    if fair is None or fair <= 0:
        return None, "外推收益率非正，结果不可信"
    return fair, f"目标期限{direction}无样本，按最近{count}只样本最小二乘线性外推"


def issuer_curve_analysis(
    target: dict[str, Any], issuer_bonds: list[dict[str, Any]], yields: dict[str, float],
    *, exclude_exchange_tech: bool = True, rating_curve_yield=None,
) -> dict[str, Any]:
    target_code = normalize_code(target.get("code"))
    target_term = finite_number(target.get("term"))
    target_yield = yields.get(target_code) or yields.get(bare_code(target_code))
    peers = []
    excluded_exchange_tech_count = 0
    for bond in issuer_bonds:
        if not _same_curve_group(target, bond):
            continue
        code = normalize_code(bond.get("code"))
        if code != target_code and exclude_exchange_tech and _is_exchange_tech_bond(bond):
            excluded_exchange_tech_count += 1
            continue
        term = finite_number(bond.get("term"))
        ytm = yields.get(code) or yields.get(bare_code(code))
        if not code or term is None or ytm is None:
            continue
        peers.append({
            "code": code,
            "name": bond.get("name") or "",
            "term": round(term, 4),
            "yield": round(ytm, 4),
            "is_target": code == target_code,
            "exchange_tech": _is_exchange_tech_bond(bond),
        })
    peers.sort(key=lambda row: (row["term"], row["code"]))
    others = [row for row in peers if not row["is_target"]]
    base = {
        "sample_count": len(peers),
        "peer_count": len(others),
        "peers": peers,
        "exclude_exchange_tech": bool(exclude_exchange_tech),
        "excluded_exchange_tech_count": excluded_exchange_tech_count,
        "method": "同主体同层级、无担保债券留一法分段线性拟合"
        + ("，剔除交易所科创债" if exclude_exchange_tech else "，保留交易所科创债"),
    }
    if target_term is None or target_yield is None:
        return {**base, "available": False, "confidence": "不足", "reason": "目标债缺少期限或中债估值"}
    if len(peers) < 3 or len(others) < 2:
        return {**base, "available": False, "confidence": "不足", "reason": "同类主体债样本不足3只"}
    left = [row for row in others if row["term"] < target_term]
    right = [row for row in others if row["term"] > target_term]
    same = [row for row in others if abs(row["term"] - target_term) < 1e-9]
    extrapolated = False
    extrapolation_note = ""
    if same:
        fair = statistics.median(row["yield"] for row in same)
    elif left and right:
        fair = interpolate_points([(row["term"], row["yield"]) for row in others], target_term)
    else:
        # 单侧样本：主体样本足够多且目标期限未偏离过远时线性外推，否则降级
        fair, extrapolation_note = _extrapolate_issuer_curve(
            others, target_term, rating_curve_yield=rating_curve_yield,
        )
        if fair is None:
            return {
                **base,
                "available": False,
                "confidence": "低",
                "reason": extrapolation_note or "目标期限单侧无样本，不做外推",
            }
        extrapolated = True
    residuals_bp = []
    for row in others:
        remaining = [item for item in others if item["code"] != row["code"]]
        has_left = any(item["term"] < row["term"] for item in remaining)
        has_right = any(item["term"] > row["term"] for item in remaining)
        if not (has_left and has_right):
            continue
        predicted = interpolate_points([(item["term"], item["yield"]) for item in remaining], row["term"])
        if predicted is not None:
            residuals_bp.append((row["yield"] - predicted) * 100)
    threshold = max(3.0, 1.5 * _mad(residuals_bp))
    residual = (target_yield - fair) * 100
    if residual >= threshold:
        convexity = "明显凸点"
    elif residual > 0:
        convexity = "轻微凸点"
    else:
        convexity = "无凸点"
    if extrapolated:
        # 外推结果的置信度上限为“中”：曲线本身可靠但目标期限在样本区间外
        confidence = "中" if len(peers) >= 5 else "低"
    else:
        confidence = "高" if len(peers) >= 5 and left and right else "中" if len(peers) >= 4 else "低"
    return {
        **base,
        "available": True,
        "confidence": confidence,
        "curve_yield": round(fair, 4),
        "residual_bp": round(residual, 2),
        "threshold_bp": round(threshold, 2),
        "convexity": convexity,
        "has_left_sample": bool(left or same),
        "has_right_sample": bool(right or same),
        "extrapolated": extrapolated,
        "extrapolation_note": extrapolation_note,
    }


def fetch_instrument_details(code: str) -> dict[str, Any]:
    code = normalize_code(code)
    symbol = bare_code(code)
    with _instrument_lock:
        cached = _instrument_cache.get(symbol)
    if cached is not None:
        return dict(cached)
    result: dict[str, Any] = {}
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT b.COUPONRATE, b.STARTDATE, b.MATURITYDATE,
                       b.PAYMENTMODE, b.PAYMENTNUM, b.PERPAYDATE,
                       b.PAYMENTDATE, b.PUTDATE, b.REDEEMDATE,
                       b.CVTBDEXPIREMEMP
                FROM TQ_BD_BASICINFO b
                WHERE b.SYMBOL = :symbol
                  AND b.ISVALID = 1
                  AND ROWNUM = 1
                """,
                {"symbol": symbol},
            )
            row = cur.fetchone()
        if row:
            result = {
                "coupon_rate": _number(row[0]),
                "start_date": _date_text(row[1]),
                "maturity_date": _date_text(row[2]),
                "payment_mode": str(row[3] or ""),
                "payments_per_year": int(row[4] or 0),
                "payment_day_rules": str(row[5] or ""),
                "payment_date": _date_text(row[6]),
                "put_date": _date_text(row[7]),
                "redeem_date": _date_text(row[8]),
                "option_memo": str(row[9] or ""),
            }
    except Exception as exc:
        result = {"error": str(exc)[:160]}
    if result and not result.get("error"):
        with _instrument_lock:
            _instrument_cache[symbol] = dict(result)
    return result


def _payment_dates(details: dict[str, Any], valuation_date: date, effective_maturity: date) -> list[date]:
    dates: set[date] = set()
    rules = [part.strip() for part in str(details.get("payment_day_rules") or "").split(",") if part.strip()]
    for year in range(valuation_date.year - 1, effective_maturity.year + 1):
        for rule in rules:
            try:
                month, day = (int(value) for value in rule.split("-", 1))
                candidate = date(year, month, day)
            except (TypeError, ValueError):
                continue
            if valuation_date < candidate <= effective_maturity:
                dates.add(candidate)
    dates.add(effective_maturity)
    return sorted(dates)


def _cashflows(details: dict[str, Any], valuation_date: date, effective_maturity: date) -> list[tuple[date, float]]:
    coupon = finite_number(details.get("coupon_rate"))
    if coupon is None:
        return []
    frequency = int(details.get("payments_per_year") or 0)
    start_date = _parse_date(details.get("start_date")) or valuation_date
    if frequency <= 0 or str(details.get("payment_mode") or "") == "5":
        years = max((effective_maturity - start_date).days / 365.0, 0)
        return [(effective_maturity, 100.0 + coupon * years)]
    coupon_amount = coupon / frequency
    flows = []
    for payment_date in _payment_dates(details, valuation_date, effective_maturity):
        amount = coupon_amount
        if payment_date == effective_maturity:
            amount += 100.0
        flows.append((payment_date, amount))
    return flows


def _dirty_price(cashflows: list[tuple[date, float]], settlement: date, ytm: float, frequency: int) -> float:
    frequency = max(int(frequency or 1), 1)
    periodic = ytm / 100.0 / frequency
    if periodic <= -1:
        return math.nan
    price = 0.0
    for payment_date, amount in cashflows:
        if payment_date <= settlement:
            continue
        years = (payment_date - settlement).days / 365.0
        price += amount / ((1 + periodic) ** (frequency * years))
    return price


def _holding_return_case(
    cashflows: list[tuple[date, float]], valuation_date: date, horizon_date: date,
    current_yield: float, future_yield: float | None, frequency: int, horizon_days: int,
) -> dict[str, Any]:
    price0 = _dirty_price(cashflows, valuation_date, current_yield, frequency)
    received = sum(amount for payment_date, amount in cashflows if valuation_date < payment_date <= horizon_date)
    if all(payment_date <= horizon_date for payment_date, _ in cashflows):
        price1_same = price1 = 0.0
        end_yield = None
    else:
        end_yield = current_yield if future_yield is None else future_yield
        price1_same = _dirty_price(cashflows, horizon_date, current_yield, frequency)
        price1 = _dirty_price(cashflows, horizon_date, end_yield, frequency)
    if not math.isfinite(price0) or price0 <= 0:
        return {"available": False, "reason": "现金流定价失败"}
    carry = (received + price1_same - price0) / price0 * 100
    total = (received + price1 - price0) / price0 * 100
    return {
        "available": True,
        "current_yield": round(current_yield, 4),
        "horizon_yield": round(end_yield, 4) if end_yield is not None else None,
        "current_full_price": round(price0, 4),
        "horizon_full_price": round(price1, 4),
        "cash_received": round(received, 4),
        "carry_return_pct": round(carry, 4),
        "rolldown_return_pct": round(total - carry, 4),
        "total_return_pct": round(total, 4),
        "simple_annualized_pct": round(total * 365 / horizon_days, 4),
    }


def calculate_holding_returns(
    bond: dict[str, Any], details: dict[str, Any], valuation_date_text: str,
    actual_yield: float | None, curve_yield_now: float | None,
    curve_yield_horizon: float | None, *, horizon_months: int = 3,
) -> dict[str, Any]:
    horizon_months = int(horizon_months)
    if horizon_months not in HOLDING_DAYS_BY_MONTHS:
        raise ValueError("骑乘期限仅支持3个月或6个月")
    horizon_days = HOLDING_DAYS_BY_MONTHS[horizon_months]
    valuation_date = _parse_date(valuation_date_text)
    effective_maturity = _parse_date(bond.get("effective_maturity_date") or details.get("maturity_date"))
    if not valuation_date or not effective_maturity or actual_yield is None:
        return {"available": False, "reason": "缺少估值日期、到期/行权日或中债估值"}
    if curve_yield_now is None or curve_yield_horizon is None:
        return {"available": False, "reason": "评级曲线不覆盖该期限"}
    if effective_maturity <= valuation_date:
        return {"available": False, "reason": "债券已到期或行权"}
    cashflows = _cashflows(details, valuation_date, effective_maturity)
    if not cashflows:
        return {"available": False, "reason": "Oracle票息/现金流字段暂不可用"}
    horizon_date = valuation_date + timedelta(days=horizon_days)
    frequency = max(int(details.get("payments_per_year") or 1), 1)
    residual = actual_yield - curve_yield_now
    actual_future = curve_yield_horizon + residual
    actual = _holding_return_case(
        cashflows, valuation_date, horizon_date, actual_yield, actual_future, frequency, horizon_days
    )
    benchmark = _holding_return_case(
        cashflows, valuation_date, horizon_date, curve_yield_now, curve_yield_horizon, frequency, horizon_days
    )
    excess = None
    if actual.get("available") and benchmark.get("available"):
        excess = round(actual["total_return_pct"] - benchmark["total_return_pct"], 4)
    return {
        "available": bool(actual.get("available") and benchmark.get("available")),
        "valuation_date": valuation_date.isoformat(),
        "horizon_date": horizon_date.isoformat(),
        "horizon_days": horizon_days,
        "horizon_months": horizon_months,
        "assumption": "评级曲线形态与个券相对评级曲线残差保持不变；按Oracle票息结构定价",
        "crosses_effective_maturity": effective_maturity <= horizon_date,
        "actual": actual,
        "rating_benchmark": benchmark,
        "excess_return_pct": excess,
    }


def _clean_quote(row: dict[str, Any] | None, valuation_yield: float | None, observed_at: str) -> dict[str, Any] | None:
    if not row:
        return None
    bid = finite_number(row.get("bid_yield"))
    ofr = finite_number(row.get("ofr_yield"))
    bid_outlier = bool(valuation_yield is not None and bid is not None and abs((bid - valuation_yield) * 100) >= OUTLIER_THRESHOLD_BP)
    ofr_outlier = bool(valuation_yield is not None and ofr is not None and abs((ofr - valuation_yield) * 100) >= OUTLIER_THRESHOLD_BP)
    clean_bid = None if bid_outlier else bid
    clean_ofr = None if ofr_outlier else ofr
    return {
        "observed_at": observed_at,
        "bid": _number(clean_bid),
        "ofr": _number(clean_ofr),
        "bid_volume": row.get("bid_volume_text") or row.get("bid_volume_value") or "",
        "ofr_volume": row.get("ofr_volume_text") or row.get("ofr_volume_value") or "",
        "bid_broker": row.get("bid_broker") or "",
        "ofr_broker": row.get("ofr_broker") or "",
        "bid_time": row.get("bid_time") or row.get("quote_time") or "",
        "ofr_time": row.get("ofr_time") or row.get("quote_time") or "",
        "bid_ofr_width_bp": round((clean_bid - clean_ofr) * 100, 2) if clean_bid is not None and clean_ofr is not None else None,
        "bid_vs_valuation_bp": round((clean_bid - valuation_yield) * 100, 2) if clean_bid is not None and valuation_yield is not None else None,
        "ofr_vs_valuation_bp": round((clean_ofr - valuation_yield) * 100, 2) if clean_ofr is not None and valuation_yield is not None else None,
        "bid_outlier": bid_outlier,
        "ofr_outlier": ofr_outlier,
        "two_sided": clean_bid is not None and clean_ofr is not None,
    }


def _quote_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in snapshot.get("quotes") or []:
        code = normalize_code(row.get("code"))
        if code:
            result[code] = row
            result.setdefault(bare_code(code), row)
    return result


def quote_analysis(
    target: dict[str, Any], issuer_bonds: list[dict[str, Any]], yields: dict[str, float]
) -> dict[str, Any]:
    target_code = normalize_code(target.get("code"))
    current = load_snapshot()
    current_index = _quote_index(current)
    target_yield = yields.get(target_code) or yields.get(bare_code(target_code))
    latest = _clean_quote(current_index.get(target_code) or current_index.get(bare_code(target_code)), target_yield, str(current.get("generated_at") or ""))
    history = []
    files = list_quote_history()
    keep_days = []
    for name in reversed(files):
        day = name[:8]
        if day not in keep_days:
            keep_days.append(day)
        if len(keep_days) > 5:
            break
        snapshot = load_market_json(HISTORY_DIR / name, {})
        index = _quote_index(snapshot)
        quote = _clean_quote(index.get(target_code) or index.get(bare_code(target_code)), target_yield, str(snapshot.get("generated_at") or ""))
        if quote:
            history.append(quote)
    history.sort(key=lambda item: item.get("observed_at") or "")
    issuer_latest = []
    for bond in issuer_bonds:
        code = normalize_code(bond.get("code"))
        quote = current_index.get(code) or current_index.get(bare_code(code))
        if not quote:
            continue
        ytm = yields.get(code) or yields.get(bare_code(code))
        cleaned = _clean_quote(quote, ytm, str(current.get("generated_at") or ""))
        if cleaned and (cleaned.get("bid") is not None or cleaned.get("ofr") is not None):
            issuer_latest.append({
                "code": code,
                "name": bond.get("name") or "",
                "term": _number(bond.get("term")),
                "valuation_yield": _number(ytm),
                **cleaned,
            })
    issuer_latest.sort(key=lambda item: (item.get("term") is None, item.get("term") or 0))
    return {
        "snapshot_at": current.get("generated_at") or "",
        "latest": latest,
        "history": history,
        "history_days": len({str(item.get("observed_at") or "")[:10] for item in history}),
        "issuer_latest": issuer_latest,
    }


def _median(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in values if finite_number(value) is not None]
    return round(statistics.median(clean), 2) if clean else None


def _one_year_reference(cache_dates: dict[str, Any], current: str) -> str:
    try:
        target = (datetime.strptime(current, "%Y%m%d").date() - timedelta(days=365)).strftime("%Y%m%d")
    except ValueError:
        return ""
    candidates = [str(dt) for dt, snapshot in cache_dates.items() if str(dt) <= target and snapshot]
    return max(candidates) if candidates else ""


def spread_analysis(
    target: dict[str, Any], issuer_bonds: list[dict[str, Any]], yields: dict[str, float] | None = None,
) -> dict[str, Any]:
    payload = read_js_data(config.SPREAD_JS) or {}
    rows = payload.get("data") or []
    row_index = {}
    for row in rows:
        code = normalize_code(row.get("code"))
        if code:
            row_index[code] = row
            row_index.setdefault(bare_code(code), row)
    dates = dict(payload.get("dates") or {})
    history_cache = load_spread_history_cache().get("dates") or {}
    current = str(dates.get("当前") or "").replace("-", "")[:8]
    one_year = _one_year_reference(history_cache, current) if current else ""
    if one_year:
        dates["一年前"] = one_year

    def values_for(code: str, row: dict[str, Any] | None) -> dict[str, float | None]:
        bare = bare_code(code)
        result = {
            "当前": finite_number((row or {}).get("spread_current")),
            "昨日": finite_number((row or {}).get("spread_yesterday")),
            "一周前": finite_number((row or {}).get("spread_week")),
            "一月前": finite_number((row or {}).get("spread_month")),
            "年初": finite_number((row or {}).get("spread_year_start")),
            "一年前": finite_number((history_cache.get(one_year, {}).get(bare) or {}).get("spread")) if one_year else None,
        }
        return {key: _number(value, 2) for key, value in result.items()}

    issuer_rows = []
    for bond in issuer_bonds:
        code = normalize_code(bond.get("code"))
        row = row_index.get(code) or row_index.get(bare_code(code))
        values = values_for(code, row)
        if not any(value is not None for value in values.values()):
            continue
        current_value = values.get("当前")
        issuer_rows.append({
            "code": code,
            "name": bond.get("name") or "",
            "term": _number(bond.get("term")),
            "yield": _number(
                (yields or {}).get(code) if (yields or {}).get(code) is not None
                else (yields or {}).get(bare_code(code))
            ),
            "values": values,
            "change_week": round(current_value - values["一周前"], 2) if current_value is not None and values.get("一周前") is not None else None,
            "change_ytd": round(current_value - values["年初"], 2) if current_value is not None and values.get("年初") is not None else None,
            "change_year": round(current_value - values["一年前"], 2) if current_value is not None and values.get("一年前") is not None else None,
        })
    issuer_rows.sort(key=lambda item: (item.get("term") is None, item.get("term") or 0))
    medians = {label: _median(row["values"].get(label) for row in issuer_rows) for label in dates}
    current_median = medians.get("当前")
    changes = {
        label: round(current_median - medians[label], 2)
        if current_median is not None and medians.get(label) is not None else None
        for label in ("昨日", "一周前", "年初", "一年前")
    }
    target_code = normalize_code(target.get("code"))
    target_row = row_index.get(target_code) or row_index.get(bare_code(target_code))
    return {
        "update_time": payload.get("update_time") or "",
        "dates": dates,
        "bond": values_for(target_code, target_row),
        "bond_curve_name": (target_row or {}).get("curve_name") or curve_for_bond(target),
        "issuer_medians": medians,
        "issuer_changes": changes,
        "issuer_bonds": issuer_rows,
    }


def credit_facility_analysis(bond: dict[str, Any]) -> dict[str, Any]:
    """主体最近授信（内评门户「全部每日有效主体授信」表，含可用对手限额与内评级别）。"""
    from juyuan_update.neiping_portal_fetch import load_portal_data

    issuer = str(bond.get("issuer") or "").strip()
    payload = load_portal_data() or {}
    limits = payload.get("limits") or {}
    ratings = payload.get("ratings") or {}
    available = bool(issuer and issuer in limits)
    limit_value = _number(limits.get(issuer), 2) if available else None
    return {
        "available": available,
        "issuer": issuer,
        "internal_rating": ratings.get(issuer) or "",
        "available_limit": limit_value,
        "data_date": str(payload.get("limits_date") or ""),
        "meets_recommend_threshold": bool(limit_value is not None and limit_value > 1),
        "note": "" if available else "内评门户暂无该主体的授信记录",
    }


def rating_compliance_analysis(code: str) -> dict[str, Any]:
    """合规630跟踪评级判定（复用二级择券工具的判定规则与评级事实缓存）。"""
    from juyuan_update.rating_compliance import evaluate_rating_compliance, load_rating_facts_cache

    cache = load_rating_facts_cache() or {}
    fact = (cache.get("facts") or {}).get(code)
    if fact:
        verdict = evaluate_rating_compliance(date.today(), fact)
    else:
        verdict = {"status": "unknown", "reason": "630评级缓存中无该债券记录"}
    issuer_dates = [str(d) for d in (fact or {}).get("issuer_dates") or [] if d]
    credit_dates = [str(d) for d in (fact or {}).get("credit_dates") or [] if d]
    return {
        "status": verdict.get("status") or "unknown",
        "reason": verdict.get("reason") or "",
        "issue_date": str((fact or {}).get("issue_date") or ""),
        "issuer_rating_latest": issuer_dates[-1] if issuer_dates else "",
        "issuer_rating_count": len(issuer_dates),
        "credit_rating_latest": credit_dates[-1] if credit_dates else "",
        "credit_rating_count": len(credit_dates),
        "as_of": date.today().isoformat(),
        "cache_generated_at": str(cache.get("generated_at") or ""),
    }


def deterministic_summary(payload: dict[str, Any]) -> str:
    bond = payload["bond"]
    relative = payload["relative_value"]
    ride = payload["riding_return"]
    quotes = payload["quotes"]
    spreads = payload["spreads"]
    if bond.get("current_yield") is not None:
        sentences = [
            f"{bond['name']}（{bond['code']}）当前中债估值{bond['current_yield']:.4f}%"
            f"，剩余期限{bond['term']:.2f}年，隐含评级{bond['implied_rating'] or '未提供'}。"
        ]
    else:
        sentences = [
            f"{bond['name']}（{bond['code']}）剩余期限{bond['term']:.2f}年，当前暂无中债估值，估值相关诊断不可用。"
        ]
    gap = relative.get("rating_curve_gap_bp")
    if gap is not None:
        sentences.append(f"相对同期限{relative['rating_curve_name']}高出{gap:.1f}BP。" if gap >= 0 else f"相对同期限{relative['rating_curve_name']}低{-gap:.1f}BP。")
    issuer_curve = relative.get("issuer_curve") or {}
    if issuer_curve.get("available"):
        sentences.append(
            f"留一法主体曲线残差为{issuer_curve['residual_bp']:.1f}BP，判断为{issuer_curve['convexity']}"
            f"（样本{issuer_curve['sample_count']}只，置信度{issuer_curve['confidence']}）。"
        )
    else:
        sentences.append(f"主体曲线{issuer_curve.get('reason', '样本不足')}，暂不判断个券凸点。")
    if ride.get("available"):
        actual = ride["actual"]["total_return_pct"]
        excess = ride.get("excess_return_pct")
        months = int(ride.get("horizon_months") or 3)
        sentences.append(f"{months}个月模型持有收益约{actual:.3f}%" + (f"，较评级基准券高{excess:.3f}个百分点。" if excess is not None else "。"))
    latest = quotes.get("latest") or {}
    if latest.get("ofr") is not None:
        sentences.append(f"最新Ofr为{latest['ofr']:.4f}%" + (f"，相对估值{latest['ofr_vs_valuation_bp']:+.1f}BP。" if latest.get("ofr_vs_valuation_bp") is not None else "。"))
    else:
        sentences.append("当前未发现有效卖盘。")
    weekly = (spreads.get("issuer_changes") or {}).get("一周前")
    if weekly is not None:
        sentences.append(f"主体债券利差中位数较一周前{'走阔' if weekly > 0 else '收窄'}{abs(weekly):.1f}BP。")
    compliance = payload.get("rating_compliance") or {}
    if compliance.get("status") == "fail":
        sentences.append(f"注意：该券{compliance.get('reason') or '不满足合规630跟踪评级要求'}，投前请务必确认。")
    return "".join(sentences)


def _detail_version(*paths: Path, extra: str = "") -> str:
    parts = [extra]
    for path in paths:
        try:
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{path}:missing")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def build_bond_detail(
    code: str, *, exclude_exchange_tech: bool = True, horizon_months: int = 3,
) -> dict[str, Any]:
    horizon_months = int(horizon_months)
    if horizon_months not in HOLDING_DAYS_BY_MONTHS:
        raise ValueError("骑乘期限仅支持3个月或6个月")
    horizon_days = HOLDING_DAYS_BY_MONTHS[horizon_months]
    code = normalize_code(code)
    _, by_code, by_issuer = _bond_indexes()
    bond = by_code.get(code) or by_code.get(bare_code(code))
    if not bond:
        raise KeyError("未找到该债券")
    code = normalize_code(bond.get("code"))
    yields, yield_payload = _yield_indexes()
    current_yield = yields.get(code) or yields.get(bare_code(code))
    # 中债估值缺失（如SPB等无估值券种）不再阻断整页：估值相关模块
    # （估值位置/骑乘/主体曲线凸点）按各自"不可用"路径降级，
    # 授信、630合规、经纪商报价与AI信用研究不受影响。
    term = finite_number(bond.get("term"))
    if term is None:
        raise ValueError("该债券缺少剩余期限")
    issuer = str(bond.get("issuer") or "").strip()
    issuer_bonds = by_issuer.get(issuer, [])
    curve_name = curve_for_bond(bond)
    std_category = CURVE_TO_STD_CATEGORY.get(curve_name, "")
    curve_date, curve_day, _ = _curve_cache()
    rating_curve_yield = _curve_yield(curve_day, curve_name, term)
    gov_curve_yield = _curve_yield(curve_day, "国开债", term)
    horizon_term = max(term - horizon_days / 365.0, 0.08)
    rating_curve_horizon = _curve_yield(curve_day, curve_name, horizon_term)
    std_data = read_js_data(config.STD_DEV_JS) or {}
    rating_market = rating_band_metrics(std_data, std_category, term) if std_category else {
        "available": False, "reason": "该券种暂未映射至两倍标准差曲线"
    }
    issuer_curve = issuer_curve_analysis(
        bond, issuer_bonds, yields, exclude_exchange_tech=exclude_exchange_tech,
        rating_curve_yield=lambda tenor: _curve_yield(curve_day, curve_name, tenor),
    )
    overlay_terms = [row.get("term") for row in issuer_curve.get("peers") or []]
    overlay_terms.append(term)
    rating_curve_points = _rating_curve_overlay(curve_day, curve_name, overlay_terms)
    details = fetch_instrument_details(code)
    riding = calculate_holding_returns(
        bond, details, str(yield_payload.get("trade_date") or curve_date),
        current_yield, rating_curve_yield, rating_curve_horizon, horizon_months=horizon_months,
    )
    quotes = quote_analysis(bond, issuer_bonds, yields)
    spreads = spread_analysis(bond, issuer_bonds, yields)
    credit_facility = credit_facility_analysis(bond)
    rating_compliance = rating_compliance_analysis(code)
    relative = {
        "rating_curve_name": curve_name,
        "rating_curve_yield": _number(rating_curve_yield),
        "rating_curve_gap_bp": round((current_yield - rating_curve_yield) * 100, 2) if current_yield is not None and rating_curve_yield is not None else None,
        "government_curve_yield": _number(gov_curve_yield),
        "credit_spread_bp": round((rating_curve_yield - gov_curve_yield) * 100, 2) if rating_curve_yield is not None and gov_curve_yield is not None else None,
        "rating_curve_points": rating_curve_points,
        "issuer_curve": issuer_curve,
    }
    bond_payload = {
        **{key: bond.get(key) for key in (
            "name", "issuer", "implied_rating", "internal_rating", "entity", "ct", "sub", "tech",
            "guarantor", "issue_date", "effective_maturity_date", "term_source", "is_holding",
        )},
        "code": code,
        "term": round(term, 4),
        "current_yield": round(current_yield, 4) if current_yield is not None else None,
        **{key: details.get(key) for key in (
            "coupon_rate", "maturity_date", "payment_mode", "payments_per_year", "payment_day_rules",
            "put_date", "redeem_date", "option_memo",
        )},
    }
    version = _detail_version(
        config.BOND_STATIC_JSON, config.BOND_PICKER_YIELDS_CACHE, config.SPREAD_JS,
        config.STD_DEV_JS, config.STD_DEV_CURVES_CACHE, config.PORTAL_DATA_JSON,
        config.RATING_FACTS_CACHE,
        extra=(f"{quotes.get('snapshot_at') or ''}|exclude_exchange_tech="
               f"{int(exclude_exchange_tech)}|horizon_months={horizon_months}"),
    )
    payload = {
        "version": version,
        "meta": {
            "valuation_date": str(yield_payload.get("trade_date") or ""),
            "curve_date": curve_date,
            "broker_snapshot_at": quotes.get("snapshot_at") or "",
            "spread_update_time": spreads.get("update_time") or "",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "limitations": [
                "主体曲线仅使用当前缓存内同主体、同层级、无担保存续债",
                "骑乘收益为情景测算，不代表未来实际收益",
                "行业基本面模块按方案留待后续版本",
            ],
        },
        "bond": bond_payload,
        "rating_market": rating_market,
        "relative_value": relative,
        "riding_return": riding,
        "quotes": quotes,
        "spreads": spreads,
        "credit_facility": credit_facility,
        "rating_compliance": rating_compliance,
        "data_quality": {
            "issuer_curve_confidence": issuer_curve.get("confidence") or "不足",
            "issuer_curve_samples": issuer_curve.get("sample_count") or 0,
            "excluded_exchange_tech_count": issuer_curve.get("excluded_exchange_tech_count") or 0,
            "quote_history_days": quotes.get("history_days") or 0,
            "cashflow_available": bool(riding.get("available")),
            "instrument_error": details.get("error") or "",
        },
    }
    payload["summary"] = {"text": deterministic_summary(payload)}
    return payload
