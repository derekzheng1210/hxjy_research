# -*- coding: utf-8 -*-
"""叠加曲线数据层

数据源（全部只读，mtime 变化自动重读）：
1. 利率曲线（国债/国开债，含 15/20/30Y）：本项目 data/rate_curves_cache.json
2. 信用收益率（中短票/二级资本债）：portal strategy_curves_cache.json（2023+ 历史段）
   + std_dev oracle_latest_curves_cache.json（增量段，含中短票AAA+/AA 独有曲线）
3. 信用利差（5 品种，2022+）：portal spread_data.js
"""
import json
import re
import threading

import institution_flow_config as config

_lock = threading.Lock()
_file_cache = {}  # path_str -> {"mtime": float|None, "value": object}


# ---------- 通用文件加载（mtime 缓存） ----------

def _cached(path, loader):
    key = str(path)
    mtime = path.stat().st_mtime if path.exists() else None
    with _lock:
        ent = _file_cache.get(key)
        if ent and ent["mtime"] == mtime:
            return ent["value"]
    value = loader(path) if mtime is not None else None
    with _lock:
        _file_cache[key] = {"mtime": mtime, "value": value}
    return value


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_spread_js(path):
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    m = re.search(r"var\s+SPREAD_DATA\s*=\s*(\{.*\})\s*;?\s*$", text, flags=re.S)
    return json.loads(m.group(1)) if m else None


# ---------- 利率曲线（本项目缓存） ----------

def load_rate_curves() -> dict:
    """-> {curve: {tenor_label: {'dates': [], 'values': []}}}"""
    raw = _cached(config.RATE_CURVES_CACHE, _load_json)
    out = {}
    if not raw:
        return out
    for curve, by_tenor in (raw.get("curves") or {}).items():
        out[curve] = {}
        for tenor_key, tv in (by_tenor or {}).items():
            label = f"{tenor_key}Y"
            if tv.get("dates"):
                out[curve][label] = {"dates": tv["dates"], "values": tv["values"]}
    return out


# ---------- 信用收益率（portal 两缓存合并） ----------

def _merge_date_series(base: dict, extra: dict) -> dict:
    """合并两个 {date: value}，extra 覆盖同日；返回按日期排序的 {dates, values}"""
    merged = dict(base)
    merged.update(extra)
    keys = sorted(merged)
    return {"dates": keys, "values": [merged[k] for k in keys]}


def load_credit_curves() -> dict:
    """-> {curve: {tenor_label: {'dates': [], 'values': []}}}，合并 strategy 历史段 + oracle 增量段"""
    strategy = _cached(config.STRATEGY_CURVES_CACHE, _load_json) or {}
    oracle = _cached(config.STD_DEV_CURVES_CACHE, _load_json) or {}

    # 统一成 {curve: {tenor_label: {date: value}}}
    acc = {}

    def put(curve: str, tenor_label: str, date_str: str, value):
        acc.setdefault(curve, {}).setdefault(tenor_label, {})[date_str] = value

    # strategy 历史段（tenor '0' 为 0Y 隔夜，跳过；'1'-'10' -> 1Y-10Y）
    for date_str, curves in (strategy.get("curves_by_date") or {}).items():
        for sname, by_tenor in (curves or {}).items():
            uname = config.STRATEGY_NAME_MAP.get(sname)
            if not uname or uname not in config.CREDIT_CURVES:
                continue
            for tenor_key, value in (by_tenor or {}).items():
                try:
                    t = float(tenor_key)
                except (TypeError, ValueError):
                    continue
                if t < 1 or t > 10:
                    continue
                put(uname, config.tenor_label(t), date_str, value)

    # oracle 增量段（同名覆盖；中短票AAA+/AA 仅在此，但不在对外品种列表则跳过）
    for date_str, curves in (oracle.get("curves_by_date") or {}).items():
        for oname, by_tenor in (curves or {}).items():
            if oname not in config.CREDIT_CURVES:
                continue
            for tenor_key, value in (by_tenor or {}).items():
                try:
                    t = float(tenor_key)
                except (TypeError, ValueError):
                    continue
                put(oname, config.tenor_label(t), date_str, value)

    return {
        curve: {label: _merge_date_series(dv, {}) for label, dv in tenors.items()}
        for curve, tenors in acc.items()
    }


# ---------- 信用利差（portal spread_data.js） ----------

def load_spreads() -> dict:
    return _cached(config.STD_DEV_SPREAD_JS, _load_spread_js) or {}


# ---------- 对外接口 ----------

def get_yield_series(curve: str, tenor: str) -> dict:
    """curve 为利率或信用品种名，tenor 为标签（如 10Y / 1M）"""
    if curve in config.RATE_CURVES:
        series = load_rate_curves().get(curve, {}).get(tenor)
    else:
        series = load_credit_curves().get(curve, {}).get(tenor)
    if not series:
        return {"dates": [], "values": [], "unit": "%", "curve": curve, "tenor": tenor}
    return {
        "dates": series["dates"],
        "values": series["values"],
        "unit": "%",
        "curve": curve,
        "tenor": tenor,
    }


def get_spread_series(category: str, tenor: str) -> dict:
    data = load_spreads()
    series = (data.get("data") or {}).get(f"{category}_{tenor}")
    if not series:
        return {"dates": [], "values": [], "unit": "%", "category": category, "tenor": tenor}
    return {
        "dates": series.get("dates") or [],
        "values": series.get("values") or [],
        "unit": "%",
        "category": category,
        "tenor": tenor,
    }


def _range_of(series_map: dict) -> tuple:
    starts, ends = [], []
    for s in series_map.values():
        if s.get("dates"):
            starts.append(s["dates"][0])
            ends.append(s["dates"][-1])
    return (min(starts) if starts else None, max(ends) if ends else None)


def get_meta() -> dict:
    rate = load_rate_curves()
    credit = load_credit_curves()
    spreads = load_spreads()

    rate_info = {}
    for curve in config.RATE_CURVES:
        tenors = rate.get(curve, {})
        start, end = _range_of(tenors)
        rate_info[curve] = {
            "tenors": [t for t in config.RATE_TENOR_LABELS if t in tenors],
            "start": start, "end": end,
        }

    credit_info = {}
    for curve in config.CREDIT_CURVES:
        tenors = credit.get(curve, {})
        start, end = _range_of(tenors)
        credit_info[curve] = {
            "tenors": [t for t in config.CREDIT_TENOR_LABELS if t in tenors],
            "start": start, "end": end,
            "spread": config.CURVE_TO_SPREAD.get(curve),
        }

    spread_info = {}
    for category in (spreads.get("categories") or []):
        tenors = (spreads.get("tenors_by_category") or {}).get(category) or []
        s0, s1 = None, None
        for t in tenors:
            series = (spreads.get("data") or {}).get(f"{category}_{t}") or {}
            dates = series.get("dates") or []
            if dates:
                s0 = dates[0] if s0 is None else min(s0, dates[0])
                s1 = dates[-1] if s1 is None else max(s1, dates[-1])
        spread_info[category] = {"tenors": tenors, "start": s0, "end": s1}

    return {
        "rate_curves": rate_info,
        "credit_curves": credit_info,
        "spreads": spread_info,
        "spread_update_time": spreads.get("update_time"),
        "available": {
            "rate": bool(rate),
            "credit": bool(credit),
            "spread": bool(spreads.get("data")),
        },
    }
