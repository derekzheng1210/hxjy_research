from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from paths import BOND_DIR, DATA_DIR


MARKET_DIR = DATA_DIR / "broker_market"
SNAPSHOT_PATH = MARKET_DIR / "latest_snapshot.json"
STATUS_PATH = MARKET_DIR / "scheduler_status.json"
PREFERENCES_PATH = MARKET_DIR / "shared_preferences.json"
EMOTION_HISTORY_PATH = MARKET_DIR / "market_emotion_history.json"
HISTORY_DIR = MARKET_DIR / "history"
COUNTERPARTY_LIMITS_PATH = DATA_DIR / "counterparty_limits.json"
LOCK_PATH = MARKET_DIR / "scheduler.lock"

OUTLIER_THRESHOLD_BP = 30.0
EMOTION_HISTORY_TRADING_DAYS = 60
# 经纪商报价历史快照保留窗口：盘中每个成功抓取时点独立留存，
# 超出最近 QUOTE_HISTORY_TRADING_DAYS 个有数据交易日的快照自动清理。
QUOTE_HISTORY_TRADING_DAYS = 10

BASE_FIELD_COUNT = 11
MARKET_FIELDS = (
    "bid_volume_text",
    "bid_volume_value",
    "bid_yield",
    "ofr_yield",
    "ofr_volume_text",
    "ofr_volume_value",
    "valuation_minus_ofr_bp",
    "bid_minus_ofr_bp",
    "bid_broker",
    "ofr_broker",
    "bid_time",
    "ofr_time",
    "quote_time",
    "has_bid",
    "has_offer",
    "two_sided",
)

ALLOWED_FILTER_KEYS = {
    "ct", "rating", "ir", "entity", "sub", "minY", "maxY", "search",
    "minTerm", "maxTerm", "minOfferVolume", "maxValuationOffer",
    "hasOffer", "twoSided", "favoritesOnly", "offerAtOrAboveValuation",
    "minYield", "maxYield", "maxValOfr", "recommendedOnly", "offerAboveVal",
    "pageSize",
}
ALLOWED_SORT_KEYS = {
    "term", "ytm", "bidVolume", "bid", "ofr", "ofrVolume",
    "valuationOffer", "bidOffer", "issuer", "name",
}

DEFAULT_RECOMMENDATION_SETTINGS = {
    "min_yield": 1.65,
    "max_yield": 3.0,
    "min_offer_volume": 1000.0,
    "bbb_minus_max_term": 3.0,
    "require_better_than_market": True,
}


def ensure_directories() -> None:
    MARKET_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    if not text:
        return ""
    text = re.sub(r"\.(IB|SH|SZ)$", lambda m: "." + m.group(1).upper(), text)
    return text


def bare_code(value: Any) -> str:
    return normalize_code(value).split(".", 1)[0]


def finite_number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_yield(value: Any) -> float | None:
    number = finite_number(value)
    return number if number is not None and number > 0 else None


def _volume_text(row: dict[str, Any], side: str, numeric: float | None) -> str:
    raw = str(row.get(f"{side}VolumeStr") or "").strip()
    if raw:
        return raw
    if numeric is None:
        return ""
    return f"{numeric:g}"


def normalize_market_row(row: dict[str, Any]) -> dict[str, Any] | None:
    code = normalize_code(row.get("bondCode"))
    if not code:
        return None
    bid_yield = valid_yield(row.get("bidYield"))
    ofr_yield = valid_yield(row.get("ofrYield"))
    bid_volume = finite_number(row.get("bidVolumeValue"))
    ofr_volume = finite_number(row.get("ofrVolumeValue"))
    return {
        "code": code,
        "name": str(row.get("bondShortName") or "").strip(),
        "bid_yield": bid_yield,
        "ofr_yield": ofr_yield,
        "bid_volume_value": bid_volume,
        "ofr_volume_value": ofr_volume,
        "bid_volume_text": _volume_text(row, "bid", bid_volume),
        "ofr_volume_text": _volume_text(row, "ofr", ofr_volume),
        "bid_broker": str(row.get("bidBrokerName") or row.get("brokerName") or "").strip(),
        "ofr_broker": str(row.get("ofrBrokerName") or row.get("brokerName") or "").strip(),
        "bid_time": str(row.get("bidIssueTimeText") or "").strip(),
        "ofr_time": str(row.get("ofrIssueTimeText") or "").strip(),
        "quote_time": str(row.get("issueTimeText") or "").strip(),
        "has_bid": bid_yield is not None,
        "has_offer": ofr_yield is not None,
        "two_sided": bid_yield is not None and ofr_yield is not None,
    }


def save_snapshot(rows: Iterable[dict[str, Any]], generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        normalized = normalize_market_row(row)
        if normalized:
            deduped[normalized["code"]] = normalized
    if not deduped:
        raise RuntimeError("DM 经纪商行情结果为空，已保留上次成功快照")
    stamp = generated_at.strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "version": hashlib.sha256(
            f"{stamp}|{len(deduped)}|{generated_at.timestamp()}".encode("utf-8")
        ).hexdigest()[:20],
        "generated_at": stamp,
        "quote_count": len(deduped),
        "quotes": list(deduped.values()),
    }
    atomic_write_json(SNAPSHOT_PATH, payload)
    _save_history_snapshot(payload)
    return payload


def _save_history_snapshot(payload: dict[str, Any]) -> None:
    """按抓取时刻把快照另存到 history/，并清理超出保留窗口的旧文件。

    历史留存属于附属数据：写入失败不影响最新快照与本次任务结果。
    """
    try:
        generated_at = str(payload.get("generated_at") or "")
        try:
            observed = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            observed = datetime.now()
        name = observed.strftime("%Y%m%d_%H%M%S") + ".json"
        atomic_write_json(HISTORY_DIR / name, payload)
        _prune_quote_history()
    except OSError:
        pass


def _prune_quote_history() -> None:
    files = sorted(HISTORY_DIR.glob("????????_??????.json"))
    days: list[str] = []
    for path in reversed(files):
        day = path.name[:8]
        if day not in days:
            days.append(day)
    keep = set(days[:QUOTE_HISTORY_TRADING_DAYS])
    for path in files:
        if path.name[:8] not in keep:
            path.unlink(missing_ok=True)


def list_quote_history() -> list[str]:
    """历史快照文件名（抓取时刻键，升序），供后续历史行情类功能读取。"""
    if not HISTORY_DIR.is_dir():
        return []
    return sorted(path.name for path in HISTORY_DIR.glob("????????_??????.json"))


def load_snapshot() -> dict[str, Any]:
    return load_json(
        SNAPSHOT_PATH,
        {"version": "", "generated_at": "", "quote_count": 0, "quotes": []},
    )


def quote_indexes(snapshot: dict[str, Any] | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    snapshot = snapshot or load_snapshot()
    full: dict[str, dict] = {}
    bare: dict[str, dict] = {}
    for row in snapshot.get("quotes") or []:
        code = normalize_code(row.get("code"))
        if not code:
            continue
        full[code] = row
        bare.setdefault(bare_code(code), row)
    return full, bare


def merge_bond_rows(base_rows: list[list[Any]], snapshot: dict[str, Any] | None = None) -> list[list[Any]]:
    full, bare = quote_indexes(snapshot)
    merged: list[list[Any]] = []
    for source in base_rows:
        base = list(source[:BASE_FIELD_COUNT])
        while len(base) < BASE_FIELD_COUNT:
            base.append("")
        code = normalize_code(base[0])
        quote = full.get(code) or bare.get(bare_code(code)) or {}
        ytm = finite_number(base[5])
        bid = valid_yield(quote.get("bid_yield"))
        ofr = valid_yield(quote.get("ofr_yield"))
        # DM occasionally returns a stale/wrong-side quote.  Clean each side
        # independently so one bad side never hides a still-usable quote.
        bid_outlier = bool(
            ytm is not None and bid is not None
            and abs((bid - ytm) * 100) >= OUTLIER_THRESHOLD_BP
        )
        ofr_outlier = bool(
            ytm is not None and ofr is not None
            and abs((ofr - ytm) * 100) >= OUTLIER_THRESHOLD_BP
        )
        if bid_outlier:
            bid = None
        if ofr_outlier:
            ofr = None
        valuation_offer = round((ytm - ofr) * 100, 2) if ytm is not None and ofr is not None else None
        bid_offer = round((bid - ofr) * 100, 2) if bid is not None and ofr is not None else None
        market = [
            "" if bid_outlier else quote.get("bid_volume_text") or "",
            None if bid_outlier else finite_number(quote.get("bid_volume_value")),
            bid,
            ofr,
            "" if ofr_outlier else quote.get("ofr_volume_text") or "",
            None if ofr_outlier else finite_number(quote.get("ofr_volume_value")),
            valuation_offer,
            bid_offer,
            "" if bid_outlier else quote.get("bid_broker") or "",
            "" if ofr_outlier else quote.get("ofr_broker") or "",
            "" if bid_outlier else quote.get("bid_time") or "",
            "" if ofr_outlier else quote.get("ofr_time") or "",
            quote.get("quote_time") or "",
            bool(bid is not None),
            bool(ofr is not None),
            bool(bid is not None and ofr is not None),
        ]
        merged.append(base + market)
    return merged


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _term_bucket(term: float | None) -> str:
    if term is None:
        return "期限未知"
    if term <= 1:
        return "≤1Y"
    if term <= 3:
        return "1-3Y"
    if term <= 5:
        return "3-5Y"
    if term <= 7:
        return "5-7Y"
    return ">7Y"


def _is_tier2_capital_bond(row: list[Any]) -> bool:
    """Identify bank Tier-2 capital bonds from the standardized short name."""
    return "二级资本债" in str(row[1] or "").replace(" ", "")


def calculate_market_emotion(
    base_rows: list[list[Any]], snapshot: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Equal-weight two-sided mid-yield minus CNBD valuation, in bp.

    ``merge_bond_rows`` performs the 30bp side-level outlier cleaning first.
    A negative reading means quotes are through valuation (stronger sentiment).
    """
    merged = merge_bond_rows(base_rows, snapshot)
    values: list[float] = []
    tier2_capital_values: list[float] = []
    dimensions: dict[str, dict[str, list[float]]] = {
        "implied_rating": {}, "internal_rating": {}, "term": {},
    }
    for row in merged:
        ytm = finite_number(row[5])
        bid = valid_yield(row[13])
        ofr = valid_yield(row[14])
        if ytm is None or bid is None or ofr is None:
            continue
        value = ((bid + ofr) / 2 - ytm) * 100
        values.append(value)
        if _is_tier2_capital_bond(row):
            tier2_capital_values.append(value)
        labels = {
            "implied_rating": str(row[3] or "未评级"),
            "internal_rating": str(row[10] or "未评级"),
            "term": _term_bucket(finite_number(row[2])),
        }
        for dimension, label in labels.items():
            dimensions[dimension].setdefault(label, []).append(value)

    breakdown = {}
    for dimension, groups in dimensions.items():
        breakdown[dimension] = [
            {"label": label, "value": _average(group), "count": len(group)}
            for label, group in groups.items()
        ]
    return {
        "value": _average(values),
        "count": len(values),
        "breakdown": breakdown,
        "tier2_capital": {
            "value": _average(tier2_capital_values),
            "count": len(tier2_capital_values),
        },
    }


def load_current_bond_rows() -> list[list[Any]]:
    """Build the same 11-field CNBD base rows used by the picker without Flask."""
    from juyuan_update.unified_excel import get_bond_picker_bonds, load_bond_picker_yields_cache

    static_bonds = get_bond_picker_bonds()
    yield_cache = load_bond_picker_yields_cache()
    yields = yield_cache.get("yields") or {}
    rows: list[list[Any]] = []
    for bond in static_bonds:
        code = normalize_code(bond.get("code"))
        ytm = yields.get(code)
        if ytm is None:
            ytm = yields.get(bare_code(code))
        if ytm is None:
            continue
        rows.append([
            code, bond.get("name") or "", finite_number(bond.get("term")) or 0,
            bond.get("implied_rating") or "", bond.get("issuer") or "",
            finite_number(ytm), bond.get("entity") or "", bond.get("ct") or "",
            bond.get("sub") or "", bond.get("tech") or "",
            bond.get("internal_rating") or "",
        ])
    return rows


def load_emotion_history() -> dict[str, Any]:
    return load_json(EMOTION_HISTORY_PATH, {"version": "", "points": []})


def record_market_emotion(
    snapshot: dict[str, Any] | None = None,
    scheduled_for: datetime | None = None,
    base_rows: list[list[Any]] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or load_snapshot()
    base_rows = base_rows if base_rows is not None else load_current_bond_rows()
    emotion = calculate_market_emotion(base_rows, snapshot)
    if emotion["value"] is None:
        raise RuntimeError("无有效双边报价，未写入挂盘情绪历史")

    generated_at = str(snapshot.get("generated_at") or "")
    try:
        observed_at = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        observed_at = datetime.now().replace(microsecond=0)
    scheduled_for = (scheduled_for or observed_at).replace(microsecond=0)
    point = {
        "scheduled_for": scheduled_for.strftime("%Y-%m-%d %H:%M:%S"),
        "observed_at": observed_at.strftime("%Y-%m-%d %H:%M:%S"),
        **emotion,
    }

    history = load_emotion_history()
    points = [p for p in history.get("points") or [] if p.get("scheduled_for") != point["scheduled_for"]]
    points.append(point)
    points.sort(key=lambda item: item.get("scheduled_for") or "")

    dates = []
    for item in reversed(points):
        day = str(item.get("scheduled_for") or "")[:10]
        if day and day not in dates:
            dates.append(day)
    keep_dates = set(dates[:EMOTION_HISTORY_TRADING_DAYS])
    points = [item for item in points if str(item.get("scheduled_for") or "")[:10] in keep_dates]
    version = hashlib.sha256(
        "|".join(
            f"{p.get('scheduled_for')}:{p.get('value')}:{(p.get('tier2_capital') or {}).get('value')}"
            for p in points
        ).encode("utf-8")
    ).hexdigest()[:20]
    payload = {"version": version, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "points": points}
    atomic_write_json(EMOTION_HISTORY_PATH, payload)
    return point


def data_version(*paths: Path) -> str:
    pieces: list[str] = []
    for path in paths or (
        SNAPSHOT_PATH,
        STATUS_PATH,
        BOND_DIR / "oracle_latest_yields_cache.json",
        BOND_DIR / "rating_facts_cache.json",
        EMOTION_HISTORY_PATH,
        COUNTERPARTY_LIMITS_PATH,
    ):
        try:
            stat = path.stat()
            pieces.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            pieces.append(f"{path.name}:missing")
    return hashlib.sha256("|".join(pieces).encode("utf-8")).hexdigest()[:20]


def default_preferences() -> dict[str, Any]:
    return {
        "favorites": [],
        "recommendation_settings": dict(DEFAULT_RECOMMENDATION_SETTINGS),
        "updated_at": "",
    }


def load_preferences() -> dict[str, Any]:
    payload = load_json(PREFERENCES_PATH, default_preferences())
    try:
        return validate_preferences(payload)
    except ValueError:
        return default_preferences()


def validate_preferences(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("偏好设置必须是对象")
    favorites: list[str] = []
    seen: set[str] = set()
    for value in payload.get("favorites") or []:
        code = normalize_code(value)
        if code and code not in seen:
            favorites.append(code)
            seen.add(code)
        if len(favorites) >= 1000:
            break

    raw_settings = payload.get("recommendation_settings") if isinstance(payload.get("recommendation_settings"), dict) else {}
    settings = dict(DEFAULT_RECOMMENDATION_SETTINGS)
    for key in ("min_yield", "max_yield", "min_offer_volume", "bbb_minus_max_term"):
        value = finite_number(raw_settings.get(key))
        if value is not None and 0 <= value <= 100000:
            settings[key] = value
    settings["require_better_than_market"] = bool(
        raw_settings.get("require_better_than_market", settings["require_better_than_market"])
    )
    if settings["min_yield"] > settings["max_yield"]:
        raise ValueError("重点关注收益率下限不能高于上限")
    return {
        "favorites": favorites,
        "recommendation_settings": settings,
        "updated_at": str(payload.get("updated_at") or ""),
    }


def save_preferences(payload: Any) -> dict[str, Any]:
    normalized = validate_preferences(payload)
    normalized["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    atomic_write_json(PREFERENCES_PATH, normalized)
    return normalized
