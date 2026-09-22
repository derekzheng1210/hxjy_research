from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path


from . import config


def holding_ratio_fields(amount, outstanding):
    """信评持仓金额（亿元）向下取千万整数后与债券余额（亿元）相除。

    返回 (单券持仓亿, 债券余额亿, 占比%)；无持仓/余额缺失时对应值为 None。
    二级择券与债券详查共用此口径，勿在调用方各自重写取整规则。
    """
    # 向下取千万（0.1亿）整数：2.06亿 -> 2.0亿；epsilon 抵消浮点误差
    holding = math.floor(round(amount, 6) * 10 + 1e-9) / 10 if amount is not None and amount > 0 else None
    ratio = round(holding / outstanding * 100, 2) if holding is not None and outstanding and outstanding > 0 else None
    return (
        round(holding, 1) if holding is not None else None,
        outstanding,
        ratio,
    )


RATING_RANK = {
    "C": 1,
    "CC": 2,
    "CCC": 3,
    "B-": 4,
    "B": 5,
    "B+": 6,
    "BB-": 7,
    "BB": 8,
    "BB+": 9,
    "BBB-": 10,
    "BBB": 11,
    "BBB+": 12,
    "A-": 13,
    "A": 14,
    "A+": 15,
    "AA-": 16,
    "AA": 17,
    "AA(2)": 17,
    "AA+": 18,
    "AAA-": 19,
    "AAA": 20,
    "AAA+": 21,
}

def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def normalize_bond_code(code) -> str:
    text = normalize_text(code).replace(" ", "").upper()
    if not text:
        return ""
    if text.endswith((".IB", ".SH", ".SZ", ".BJ")):
        return text
    if not text.isdigit():
        return text
    if len(text) >= 9:
        return text + ".IB"
    if len(text) == 6:
        if text[:2] in ("52",) or text[:3] in ("148", "149", "111", "112"):
            return text + ".SZ"
        return text + ".SH"
    return text + ".IB"


def normalize_rating(value) -> str:
    text = normalize_text(value).upper().replace(" ", "")
    if text in {"#N/A", "NA", "N/A", "-", "--"}:
        return ""
    return text


def rating_at_least(value, floor: str = "BBB-") -> bool:
    rank = RATING_RANK.get(normalize_rating(value))
    return rank is not None and rank >= RATING_RANK[floor]


def rating_below(value, floor: str) -> bool:
    """已知评级且严格低于floor；空/未知评级不算低（去留由调用方另行判断）。"""
    rank = RATING_RANK.get(normalize_rating(value))
    return rank is not None and rank < RATING_RANK[floor]


def is_blank(value) -> bool:
    return normalize_text(value) == ""


_json_cache: dict[Path, tuple[float, float, object]] = {}


def _load_json_cached(path: Path, default):
    """Read and parse *path* as JSON, memoised by (mtime, size).

    Repeated calls within the same process skip disk I/O and JSON parsing
    when the file has not changed. Writes via ``write_json`` / ``atomic_write_text``
    update the file's mtime, so the cache invalidates automatically.
    """
    try:
        stat = path.stat()
    except OSError:
        _json_cache.pop(path, None)
        return default
    cached = _json_cache.get(path)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _json_cache.pop(path, None)
        return default
    _json_cache[path] = (stat.st_mtime, stat.st_size, payload)
    return payload


def load_json(path: Path, default):
    return _load_json_cached(path, default)


def write_json(path: Path, payload) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    _json_cache.pop(path, None)


def load_bond_static() -> dict:
    return load_json(config.BOND_STATIC_JSON, {"generated_at": "", "source_file": "", "bonds": []})


def save_bond_static(bonds: list[dict], source_file: str) -> dict:
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": source_file,
        "total_bonds": len(bonds),
        "bonds": bonds,
    }
    write_json(config.BOND_STATIC_JSON, payload)
    return payload


def load_counterparty_limits() -> dict:
    return load_json(
        config.COUNTERPARTY_LIMITS_JSON,
        {"generated_at": "", "source_file": "", "total_issuers": 0, "limits": {}},
    )


def save_counterparty_limits(limits: dict[str, float], source_file: str) -> dict:
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": source_file,
        "total_issuers": len(limits),
        "limits": limits,
    }
    write_json(config.COUNTERPARTY_LIMITS_JSON, payload)
    return payload


def apply_portal_metadata(bonds: list[dict]) -> int:
    """Overlay non-Excel internal ratings, limits and holdings from the portal cache."""
    from .neiping_portal_fetch import load_portal_data

    payload = load_portal_data()
    ratings = payload.get("ratings") or {}
    holdings = load_portal_holdings()
    updated = 0
    for bond in bonds:
        issuer = str(bond.get("issuer") or "").strip()
        rating = ratings.get(issuer)
        if rating:
            bond["internal_rating"] = normalize_rating(rating)
            updated += 1
        info = holdings.get(bond.get("code"))
        if info is not None:
            amount = float(info.get("amount") or 0)
            bond["is_holding"] = amount != 0
            bond["holding_amount"] = round(amount, 6)
            bond["holding_date"] = info.get("holding_date") or ""
        elif holdings:
            bond["is_holding"] = False
    return updated


def get_bond_picker_bonds() -> list[dict]:
    import copy

    bonds = copy.deepcopy(load_bond_static().get("bonds", []))
    apply_portal_metadata(bonds)
    return [
        bond for bond in bonds
        if rating_at_least(bond.get("internal_rating")) and is_blank(bond.get("guarantor"))
    ]


# 利差监控清单下限：隐含评级低于CCC（违约/深度高风险，利差动辄上万bp）不进监控。
# 只在这里过滤，不改 bond_static 池本身——二级择券的期限修复与全量池依赖完整池。
SPREAD_MONITOR_MIN_RATING = "CCC"


def get_spread_monitor_bonds() -> list[dict]:
    import copy

    bonds = copy.deepcopy(load_bond_static().get("bonds", []))
    apply_portal_metadata(bonds)
    return [
        bond for bond in bonds
        if not rating_below(bond.get("implied_rating"), SPREAD_MONITOR_MIN_RATING)
    ]


def load_portal_holdings() -> dict:
    """门户「债项评级-有效1」最新持仓，键为规范化债券代码。

    值为 {"amount": 持仓金额, "is_holding": 是否持仓, "holding_date": 持仓日期, ...}。
    """
    from .neiping_portal_fetch import load_portal_data

    payload = load_portal_data()
    holdings: dict[str, dict] = {}
    for raw_code, info in (payload.get("holdings") or {}).items():
        code = normalize_bond_code(raw_code)
        if not code or code in holdings:
            continue
        holdings[code] = info
    return holdings


def apply_portal_holdings(bonds: list[dict]) -> int:
    """用信评系统「债项查询-最新持仓金额」覆盖 is_holding（非 0 即有持仓）。

    门户清单之外的债券一律视为无持仓；门户缓存为空时保留 Excel 原值。
    """
    holdings = load_portal_holdings()
    if not holdings:
        return 0
    overridden = 0
    for bond in bonds:
        info = holdings.get(bond.get("code"))
        if info is not None:
            amount = float(info.get("amount") or 0)
            bond["is_holding"] = amount != 0
            bond["holding_amount"] = round(amount, 6)
            bond["holding_date"] = info.get("holding_date") or ""
            overridden += 1
        else:
            bond["is_holding"] = False
    return overridden


def load_bond_picker_yields_cache() -> dict:
    return load_json(config.BOND_PICKER_YIELDS_CACHE, {"trade_date": "", "generated_at": "", "yields": {}})


def save_bond_picker_yields_cache(trade_date: str, yields: dict[str, float]) -> dict:
    normalized_yields = {}
    for key, value in yields.items():
        if value is None:
            continue
        raw = str(key or "").strip().upper()
        if not raw:
            continue
        normalized_yields[raw] = float(value)
        bare = raw.split(".", 1)[0]
        normalized_yields.setdefault(bare, float(value))
    payload = {
        "trade_date": trade_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "yields": normalized_yields,
    }
    write_json(config.BOND_PICKER_YIELDS_CACHE, payload)
    return payload


def load_spread_history_cache() -> dict:
    return load_json(config.SPREAD_HISTORY_CACHE, {"generated_at": "", "dates": {}})


def save_spread_history_cache(cache: dict) -> None:
    cache["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_json(config.SPREAD_HISTORY_CACHE, cache)


def _header_index(headers: list[str], candidates: list[str], default=None):
    for candidate in candidates:
        for idx, header in enumerate(headers):
            if candidate == header or candidate in header:
                return idx
    return default


def parse_neiping_sheet(ws) -> dict[str, float]:
    rows = list(ws.iter_rows(values_only=True))
    header_row = None
    issuer_index = None
    limit_index = None
    for row_index, row in enumerate(rows[:20]):
        headers = [normalize_text(value) for value in row]
        issuer_index = _header_index(headers, ["融资主体", "主体名称"], None)
        limit_index = _header_index(headers, ["最新可用对手限额"], None)
        if issuer_index is not None and limit_index is not None:
            header_row = row_index
            break
    if header_row is None:
        raise RuntimeError("统一 Excel 的 neiping 缺少融资主体或最新可用对手限额列")

    limits: dict[str, float] = {}
    for row in rows[header_row + 1:]:
        issuer = normalize_text(row[issuer_index] if issuer_index < len(row) else None)
        if not issuer:
            continue
        try:
            value = float(row[limit_index])
        except (TypeError, ValueError, IndexError):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        limits[issuer] = round(value, 6)
    return limits
