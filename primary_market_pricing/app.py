"""
一级发行非市场化评估系统 - Flask Web应用

API:
    GET  /                     → 前端页面
    GET  /api/search?q=xxx     → 搜索发行人或债券简称（模糊匹配）
    GET  /api/issuer/<name>    → 获取发行人偏离数据
         ?start_date=20240101  → 起始日期（默认20240101）
         ?end_date=20260703    → 截止日期（默认今天）
         ?exclude_short=0      → 是否剔除1Y以内（0/1）

性能策略:
    1. 优先读SQLite缓存（毫秒级）
    2. 缓存无数据时走实时计算（秒级，方案A优化后）
    3. 实时计算结果写回缓存

运行：python app.py
"""

import os
import sys
import sqlite3
import threading
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from statistics import median

from flask import Blueprint, jsonify, render_template, request

from . import config
from .calculator import calculate_issuer_deviations
from .data_fetcher import fetch_new_issues
from .db_utils import get_connection
from .ratings import attach_internal_ratings, rating_for_issuer

# 项目根加入 sys.path，以便复用 paths.py 统一数据路径（PORTAL_DATA_ROOT 定位）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
from paths import PRIMARY_PRICING_CACHE as CACHE_DB_PATH

pricing_bp = Blueprint("primary_market_pricing", __name__, template_folder="templates")

# ──────────────────────────────────────────────────────
# SQLite 缓存读取层
# ──────────────────────────────────────────────────────
OVERPRICED_THRESHOLD_BP = config.DEVIATION_THRESHOLD_BP
ISSUE_DATE_RULE_VERSION = "issue_date_v7_broker_subordinated"  # 与 cache_builder.py 保持一致
HISTORY_START_DATE = "20240101"
_BACKGROUND_JOBS: set[str] = set()
_BACKGROUND_COMPLETED: set[str] = set()
_BACKGROUND_LOCK = threading.Lock()


def _get_cache_conn():
    """获取SQLite缓存连接（只读）"""
    if not os.path.exists(CACHE_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")
        return conn
    except Exception:
        return None


def _cache_coverage_meta() -> dict:
    """Return cache date coverage metadata used by API defaults."""
    fallback_end = datetime.now().strftime("%Y%m%d")
    meta = {
        "start_date": HISTORY_START_DATE,
        "default_end_date": fallback_end,
        "max_end_date": fallback_end,
        "issuer_count": 0,
    }
    cache_conn = _get_cache_conn()
    if not cache_conn:
        return meta

    try:
        row = cache_conn.execute(
            """
            SELECT end_date, COUNT(*) AS n
            FROM issuer_summary
            WHERE issue_date_rule = ? AND end_date IS NOT NULL
            GROUP BY end_date
            ORDER BY n DESC, end_date DESC
            LIMIT 1
            """,
            (ISSUE_DATE_RULE_VERSION,),
        ).fetchone()
        range_row = cache_conn.execute(
            """
            SELECT MIN(start_date) AS min_start,
                   MAX(end_date) AS max_end,
                   COUNT(*) AS issuer_count
            FROM issuer_summary
            WHERE issue_date_rule = ?
            """,
            (ISSUE_DATE_RULE_VERSION,),
        ).fetchone()
        bond_range_row = cache_conn.execute(
            """
            SELECT MIN(issue_date) AS min_issue_date,
                   MAX(issue_date) AS max_issue_date,
                   MAX(CASE
                       WHEN deviation_bp IS NOT NULL AND COALESCE(is_no_judgement, 0) = 0
                       THEN issue_date
                   END) AS latest_calculable_date
            FROM bond_deviations
            WHERE issue_date_rule = ?
            """,
            (ISSUE_DATE_RULE_VERSION,),
        ).fetchone()
        if row and row["end_date"]:
            meta["default_end_date"] = row["end_date"]
        if range_row:
            meta["start_date"] = range_row["min_start"] or meta["start_date"]
            meta["max_end_date"] = range_row["max_end"] or meta["max_end_date"]
            meta["issuer_count"] = range_row["issuer_count"] or 0
        if bond_range_row:
            meta["start_date"] = bond_range_row["min_issue_date"] or meta["start_date"]
            meta["max_end_date"] = bond_range_row["max_issue_date"] or meta["max_end_date"]
            meta["default_end_date"] = bond_range_row["latest_calculable_date"] or meta["default_end_date"]
        return meta
    except Exception:
        return meta
    finally:
        cache_conn.close()


def _default_end_date() -> str:
    return _cache_coverage_meta()["default_end_date"]


def _date_offset(date_str: str, days: int) -> str:
    return (datetime.strptime(date_str, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def _bond_from_cache_row(row: sqlite3.Row, use_cached_overpriced: bool = False) -> dict:
    """Normalize a cached bond row to the API shape used by the frontend."""
    columns = set(row.keys())
    deviation_bp = row["deviation_bp"]
    is_no_judgement = bool(row["is_no_judgement"]) if "is_no_judgement" in columns else False
    no_deviation = deviation_bp is None
    is_overpriced = bool(row["is_overpriced"]) if "is_overpriced" in columns else deviation_bp > OVERPRICED_THRESHOLD_BP
    return {
        "bond_symbol": row["symbol"],
        "bond_name": row["bond_name"],
        "issuer": row["issuer"],
        "coupon_rate": row["coupon_rate"],
        "issue_amount_wan": row["issue_amount_wan"] if "issue_amount_wan" in columns else None,
        "issue_date": row["issue_date"],
        "maturity_year": row["effective_term"],
        "raise_mode": row["raise_mode"] or "",
        "bond_type": (row["bond_type"] or "") if "bond_type" in columns else "",
        "cvtbd_expire": (row["cvtbd_expire"] or "") if "cvtbd_expire" in columns else "",
        "ref_bond_name": row["ref_bond_name"],
        "ref_bond_symbol": row["ref_bond_symbol"],
        "ref_start_date": row["ref_start_date"] if "ref_start_date" in columns else "",
        "ref_date_gap_years": row["ref_date_gap_years"] if "ref_date_gap_years" in columns else None,
        "ref_yield": row["ref_yield"],
        "ref_term": row["ref_term"],
        "curve_code": row["curve_code"],
        "curve_at_ref": row["curve_at_ref"],
        "curve_at_target": row["curve_at_target"],
        "spread": row["spread"],
        "fair_price": row["fair_price"],
        "deviation": row["deviation"],
        "deviation_bp": deviation_bp,
        "is_non_market": False if is_no_judgement or no_deviation else bool(row["is_non_market"]),
        "is_overpriced": False if is_no_judgement or no_deviation else (is_overpriced if use_cached_overpriced else deviation_bp > OVERPRICED_THRESHOLD_BP),
        "is_no_judgement": is_no_judgement or no_deviation,
    }


def _summarize_bonds(bonds: list[dict], issuer: str | None = None) -> dict:
    """Build common count/ratio metrics for issuer, date and market views."""
    total_bonds = len(bonds)
    valid_bonds = [
        b for b in bonds
        if not b.get("is_no_judgement") and b.get("deviation_bp") is not None
    ]
    calculated_bonds = len(valid_bonds)
    non_market_count = sum(1 for b in valid_bonds if b["is_non_market"])
    overpriced_count = sum(1 for b in valid_bonds if b["is_overpriced"])
    avg_deviation = (
        sum(b["deviation_bp"] for b in valid_bonds) / calculated_bonds
        if calculated_bonds else 0.0
    )
    median_deviation = median([b["deviation_bp"] for b in valid_bonds]) if valid_bonds else 0.0
    known_amounts = [
        float(b["issue_amount_wan"])
        for b in bonds
        if b.get("issue_amount_wan") is not None
    ]
    result = {
        "total_bonds": total_bonds,
        "issuer_count": len({b.get("issuer") for b in bonds if b.get("issuer")}),
        "issue_amount_wan": round(sum(known_amounts), 4) if known_amounts else None,
        "issue_amount_yi": round(sum(known_amounts) / 10000, 4) if known_amounts else None,
        "issue_amount_bond_count": len(known_amounts),
        "calculated_bonds": calculated_bonds,
        "calculable_ratio": round(calculated_bonds / total_bonds, 4) if total_bonds else 0.0,
        "non_market_count": non_market_count,
        "non_market_ratio": round(non_market_count / calculated_bonds, 4) if calculated_bonds else 0.0,
        "overpriced_count": overpriced_count,
        "overpriced_ratio": round(overpriced_count / calculated_bonds, 4) if calculated_bonds else 0.0,
        "bonds": bonds,
        "avg_deviation_bp": round(avg_deviation, 2),
        "median_deviation_bp": round(median_deviation, 2),
    }
    if issuer is not None:
        result["issuer"] = issuer
    return result


def _filter_public_bonds(bonds: list[dict], only_public: bool) -> list[dict]:
    """Filter API bond rows to public offerings only when requested."""
    def excluded_bond_name(name: str) -> bool:
        if any(keyword in name for keyword in config.EXCLUDED_BOND_NAME_KEYWORDS):
            return True
        for pattern in getattr(config, "EXCLUDED_BOND_NAME_LIKE_PATTERNS", ()):
            if pattern.startswith("__") and pattern.endswith("%"):
                prefix = pattern[2:-1]
                if len(name) >= 2 and name[2:].startswith(prefix):
                    return True
        return False

    bonds = [
        b for b in bonds
        if not any(keyword in str(b.get("issuer") or "") for keyword in config.EXCLUDED_ISSUER_KEYWORDS)
        and not excluded_bond_name(str(b.get("bond_name") or ""))
    ]
    if not only_public:
        return bonds
    return [b for b in bonds if str(b.get("raise_mode") or "").strip() == "1"]


def _is_perpetual_bond(bond: dict) -> bool:
    memo = str(bond.get("cvtbd_expire") or "").strip().upper()
    name = str(bond.get("bond_name") or "")
    return bond.get("bond_type") == "perpetual" or memo.endswith("+N") or "永续" in name


def _filter_bond_terms(
    bonds: list[dict],
    exclude_perpetual: bool = False,
    term_min: float | None = None,
    term_max: float | None = None,
) -> list[dict]:
    """Apply the shared perpetual and effective-term filters."""
    filtered = []
    for bond in bonds:
        if exclude_perpetual and _is_perpetual_bond(bond):
            continue
        term = bond.get("maturity_year")
        if term_min is not None and (term is None or float(term) < term_min):
            continue
        if term_max is not None and (term is None or float(term) > term_max):
            continue
        filtered.append(bond)
    return filtered


def _optional_float_arg(name: str) -> float | None:
    value = request.args.get(name, "").strip()
    if not value:
        return None
    return float(value)


def _workpaper_round(value: float, digits: int) -> float:
    """Match SQLite ROUND used by the issuer Excel workpaper."""
    quantum = Decimal("1").scaleb(-digits)
    return float(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def _read_from_cache(
    issuer: str,
    start_date: str,
    end_date: str,
    exclude_short: bool,
    only_public: bool = False,
) -> dict | None:
    """
    从SQLite缓存读取发行人数据
    Returns: 与calculate_issuer_deviations相同格式的dict，或None（缓存无数据）
    """
    cache_conn = _get_cache_conn()
    if not cache_conn:
        return None

    try:
        # 检查issuer_summary是否有该发行人
        summary = cache_conn.execute(
            "SELECT * FROM issuer_summary WHERE issuer = ? AND issue_date_rule = ?",
            (issuer, ISSUE_DATE_RULE_VERSION)
        ).fetchone()

        if not summary:
            return None

        # 检查缓存的日期范围是否覆盖请求范围
        cached_start = summary["start_date"]
        cached_end = summary["end_date"]
        if cached_start > start_date or cached_end < end_date:
            # 缓存范围不完全覆盖请求范围，需要实时计算
            return None

        # 读取债券详情
        query = """
            SELECT * FROM bond_deviations 
            WHERE issuer = ? AND issue_date >= ? AND issue_date <= ?
              AND issue_date_rule = ?
        """
        params = [issuer, start_date, end_date, ISSUE_DATE_RULE_VERSION]

        if exclude_short:
            query += " AND effective_term >= 1.0"

        query += " ORDER BY issue_date"

        rows = cache_conn.execute(query, params).fetchall()

        result = _summarize_bonds(
            _filter_public_bonds([_bond_from_cache_row(row) for row in rows], only_public),
            issuer=issuer,
        )
        result["_source"] = "cache"
        return result
    except Exception:
        return None
    finally:
        cache_conn.close()


def _read_issuer_partial_from_cache(
    issuer: str,
    start_date: str,
    end_date: str,
    exclude_short: bool,
    only_public: bool = False,
) -> tuple[dict | None, list[tuple[str, str]]]:
    """Read the cached overlap and return missing date ranges."""
    cache_conn = _get_cache_conn()
    if not cache_conn:
        return None, [(start_date, end_date)]

    try:
        summary = cache_conn.execute(
            "SELECT * FROM issuer_summary WHERE issuer = ? AND issue_date_rule = ?",
            (issuer, ISSUE_DATE_RULE_VERSION),
        ).fetchone()
        if not summary:
            return None, [(start_date, end_date)]

        cached_start = summary["start_date"]
        cached_end = summary["end_date"]
        if not cached_start or not cached_end:
            return None, [(start_date, end_date)]

        coverage_start = min(cached_start, HISTORY_START_DATE)
        missing_ranges = []
        if start_date < coverage_start:
            missing_ranges.append((start_date, min(end_date, _date_offset(coverage_start, -1))))
        if end_date > cached_end:
            missing_ranges.append((max(start_date, _date_offset(cached_end, 1)), end_date))

        overlap_start = max(start_date, coverage_start)
        overlap_end = min(end_date, cached_end)
        bonds = []
        if overlap_start <= overlap_end:
            query = """
                SELECT * FROM bond_deviations
                WHERE issuer = ? AND issue_date >= ? AND issue_date <= ?
                  AND issue_date_rule = ?
            """
            params = [issuer, overlap_start, overlap_end, ISSUE_DATE_RULE_VERSION]
            if exclude_short:
                query += " AND effective_term >= 1.0"
            query += " ORDER BY issue_date"
            rows = cache_conn.execute(query, params).fetchall()
            bonds = [_bond_from_cache_row(row) for row in rows]

        result = _summarize_bonds(_filter_public_bonds(bonds, only_public), issuer=issuer)
        result["_source"] = "cache_partial" if missing_ranges else "cache"
        result["_cache_start_date"] = cached_start
        result["_cache_end_date"] = cached_end
        return result, [(s, e) for s, e in missing_ranges if s <= e]
    except Exception:
        return None, [(start_date, end_date)]
    finally:
        cache_conn.close()


def _merge_issuer_results(
    issuer: str,
    results: list[dict],
    only_public: bool,
    source: str,
) -> dict:
    bonds_by_symbol = {}
    for result in results:
        for bond in result.get("bonds", []):
            key = bond.get("bond_symbol") or f"{bond.get('bond_name')}:{bond.get('issue_date')}"
            bonds_by_symbol[key] = bond

    bonds = list(bonds_by_symbol.values())
    bonds.sort(key=lambda b: (b.get("issue_date") or "", b.get("bond_symbol") or ""))
    merged = _summarize_bonds(_filter_public_bonds(bonds, only_public), issuer=issuer)
    merged["_source"] = source
    return merged


def _write_to_cache(result: dict, start_date: str, end_date: str):
    """将实时计算结果写入缓存"""
    if not result or not result.get("issuer"):
        return

    try:
        from .cache_builder import init_cache_db, save_issuer_result
        cache_conn = init_cache_db(CACHE_DB_PATH)
        save_issuer_result(cache_conn, result, start_date, end_date)
        cache_conn.close()
    except Exception:
        pass  # 缓存写入失败不影响返回结果


def _run_background_job(key: str, target, *args):
    succeeded = False
    try:
        target(*args)
        succeeded = True
    except Exception:
        app.logger.exception("Background cache job failed: %s", key)
    finally:
        with _BACKGROUND_LOCK:
            _BACKGROUND_JOBS.discard(key)
            if succeeded:
                _BACKGROUND_COMPLETED.add(key)


def _schedule_background_job(key: str, target, *args) -> bool:
    """Start one daemon job per key and return without waiting for Oracle."""
    with _BACKGROUND_LOCK:
        if key in _BACKGROUND_JOBS or key in _BACKGROUND_COMPLETED:
            return False
        _BACKGROUND_JOBS.add(key)
    thread = threading.Thread(
        target=_run_background_job,
        args=(key, target, *args),
        daemon=True,
        name=f"primary-market-{key[:30]}",
    )
    thread.start()
    return True


def _backfill_issue_amounts(start_date: str, end_date: str):
    """Backfill issue amounts for existing pricing-cache rows only."""
    with get_connection() as oracle_conn:
        issues = fetch_new_issues(oracle_conn, start_date, end_date)
    if issues.empty or "ISSUE_AMOUNT_WAN" not in issues.columns:
        return

    from .cache_builder import init_cache_db

    def clean(value):
        return None if value is None or str(value).lower() == "nan" else value

    updates = []
    for _, row in issues.iterrows():
        symbol = str(row["SYMBOL"])
        amount = clean(row.get("ISSUE_AMOUNT_WAN"))
        if amount is not None:
            updates.append((float(amount), symbol, ISSUE_DATE_RULE_VERSION))

    if not updates:
        return

    cache_conn = init_cache_db(CACHE_DB_PATH)
    try:
        cache_conn.executemany(
            """
            UPDATE bond_deviations
            SET issue_amount_wan = ?
            WHERE symbol = ? AND issue_date_rule = ?
            """,
            updates,
        )
        cache_conn.commit()
    finally:
        cache_conn.close()


def _schedule_amount_backfill(bonds: list[dict], start_date: str, end_date: str) -> bool:
    if not bonds or all(bond.get("issue_amount_wan") is not None for bond in bonds):
        return False
    return _schedule_background_job(
        f"amount:{start_date}:{end_date}",
        _backfill_issue_amounts,
        start_date,
        end_date,
    )


def _refresh_issuer_cache(issuer: str, ranges: list[tuple[str, str]], exclude_short: bool):
    with get_connection() as oracle_conn:
        for start_date, end_date in ranges:
            result = calculate_issuer_deviations(
                oracle_conn,
                issuer=issuer,
                start_date=start_date,
                end_date=end_date,
                exclude_short_term=exclude_short,
            )
            _write_to_cache(result, start_date, end_date)


def _refresh_date_cache(issue_date: str, exclude_short: bool, only_public: bool):
    _calculate_issue_date_from_db(issue_date, exclude_short, only_public)


def _search_from_cache(query: str) -> list[dict] | None:
    """从缓存中按发行人名称或债券简称搜索对应发行人。"""
    cache_conn = _get_cache_conn()
    if not cache_conn:
        return None

    try:
        rows = cache_conn.execute(
            """
            SELECT issuer, label, match_type
            FROM (
                SELECT issuer, issuer AS label, 'issuer' AS match_type
                FROM issuer_summary
                WHERE issuer LIKE ? AND issue_date_rule = ?

                UNION

                SELECT issuer, bond_name AS label, 'bond' AS match_type
                FROM bond_deviations
                WHERE bond_name LIKE ? AND issue_date_rule = ?
            )
            ORDER BY
                CASE WHEN label = ? THEN 0
                     WHEN label LIKE ? THEN 1
                     ELSE 2 END,
                match_type,
                label
            LIMIT 20
            """,
            (
                f"%{query}%", ISSUE_DATE_RULE_VERSION,
                f"%{query}%", ISSUE_DATE_RULE_VERSION,
                query, f"{query}%",
            ),
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]
        return None
    except Exception:
        return None
    finally:
        cache_conn.close()


def _read_bonds_from_cache(
    start_date: str,
    end_date: str,
    exclude_short: bool,
    only_public: bool = False,
    exclude_perpetual: bool = False,
    term_min: float | None = None,
    term_max: float | None = None,
    apply_config_exclusions: bool = True,
    use_cached_overpriced: bool = False,
) -> list[dict] | None:
    """Read all cached bond deviations in a date range."""
    cache_conn = _get_cache_conn()
    if not cache_conn:
        return None

    try:
        query = """
            SELECT * FROM bond_deviations
            WHERE issue_date >= ? AND issue_date <= ?
              AND issue_date_rule = ?
        """
        params = [start_date, end_date, ISSUE_DATE_RULE_VERSION]
        if exclude_short:
            query += " AND effective_term >= 1.0"
        query += " ORDER BY issue_date, issuer, symbol"
        rows = cache_conn.execute(query, params).fetchall()
        bonds = [_bond_from_cache_row(row, use_cached_overpriced) for row in rows]
        if apply_config_exclusions:
            bonds = _filter_public_bonds(bonds, only_public)
        elif only_public:
            bonds = [bond for bond in bonds if str(bond.get("raise_mode") or "").strip() == "1"]
        return attach_internal_ratings(
            _filter_bond_terms(bonds, exclude_perpetual, term_min, term_max)
        )
    except Exception:
        return None
    finally:
        cache_conn.close()


def _issuer_rows_from_bonds(bonds: list[dict], history_lookup: dict[str, dict] | None = None) -> list[dict]:
    """Aggregate cached bond rows by issuer for market/date overview tables."""
    grouped: dict[str, list[dict]] = {}
    for bond in bonds:
        grouped.setdefault(bond["issuer"], []).append(bond)

    rows = []
    for issuer, issuer_bonds in grouped.items():
        summary = _summarize_bonds(issuer_bonds, issuer=issuer)
        rows.append({
            "issuer": issuer,
            "internal_rating": rating_for_issuer(issuer),
            "total_bonds": summary["total_bonds"],
            "issue_amount_wan": summary["issue_amount_wan"],
            "issue_amount_yi": summary["issue_amount_yi"],
            "calculated_bonds": summary["calculated_bonds"],
            "non_market_count": summary["non_market_count"],
            "non_market_ratio": summary["non_market_ratio"],
            "overpriced_count": summary["overpriced_count"],
            "overpriced_ratio": summary["overpriced_ratio"],
            "history_non_market_ratio": (history_lookup or {}).get(issuer, {}).get("non_market_ratio"),
            "history_overpriced_ratio": (history_lookup or {}).get(issuer, {}).get("overpriced_ratio"),
            "history_calculated_bonds": (history_lookup or {}).get(issuer, {}).get("calculated_bonds"),
            "history_avg_deviation_bp": (history_lookup or {}).get(issuer, {}).get("avg_deviation_bp"),
            "avg_deviation_bp": summary["avg_deviation_bp"],
            "deviation_overview": [
                round(b["deviation_bp"], 1)
                for b in issuer_bonds
                if not b.get("is_no_judgement") and b.get("deviation_bp") is not None
            ],
        })

    rows.sort(key=lambda r: (r["history_non_market_ratio"] or 0, r["history_calculated_bonds"] or 0), reverse=True)
    return rows


def _build_date_result(bonds: list[dict], issue_date: str, source: str) -> dict:
    """Build the API response for single-date issue queries."""
    result = _summarize_bonds(bonds)
    result["issue_date"] = issue_date
    result["issuers"] = _issuer_rows_from_bonds(bonds)
    result["_source"] = source
    return result


def _read_issuer_history_from_cache(issuer: str, exclude_short: bool, only_public: bool) -> dict | None:
    result = _read_from_cache(
        issuer,
        HISTORY_START_DATE,
        datetime.now().strftime("%Y%m%d"),
        exclude_short,
        only_public,
    )
    if not result:
        return None
    return {
        "non_market_ratio": result["non_market_ratio"],
        "overpriced_ratio": result["overpriced_ratio"],
    }


def _read_history_ratios_from_cache(
    issuers: list[str],
    exclude_short: bool,
    only_public: bool,
    history_end_date: str,
    exclude_perpetual: bool = False,
    term_min: float | None = None,
    term_max: float | None = None,
) -> dict[str, dict]:
    """Read historical issuer ratios in one SQLite query."""
    issuer_list = list(dict.fromkeys(i for i in issuers if i))
    if not issuer_list:
        return {}

    cache_conn = _get_cache_conn()
    if not cache_conn:
        return {}

    try:
        table_info = cache_conn.execute("PRAGMA table_info(bond_deviations)").fetchall()
        has_no_judgement = any(row["name"] == "is_no_judgement" for row in table_info)
        placeholders = ",".join("?" for _ in issuer_list)
        filters = ["issue_date_rule = ?", "issue_date >= ?", "issue_date <= ?", f"issuer IN ({placeholders})"]
        params: list = [ISSUE_DATE_RULE_VERSION, HISTORY_START_DATE, history_end_date, *issuer_list]
        if exclude_short:
            filters.append("effective_term >= 1.0")
        if only_public:
            filters.append("TRIM(COALESCE(raise_mode, '')) = '1'")
        if exclude_perpetual:
            filters.append("COALESCE(bond_type, '') <> 'perpetual'")
            filters.append("UPPER(TRIM(COALESCE(cvtbd_expire, ''))) NOT LIKE '%+N'")
            filters.append("COALESCE(bond_name, '') NOT LIKE '%永续%'")
        if term_min is not None:
            filters.append("effective_term >= ?")
            params.append(term_min)
        if term_max is not None:
            filters.append("effective_term <= ?")
            params.append(term_max)
        if has_no_judgement:
            filters.append("COALESCE(is_no_judgement, 0) = 0")

        rows = cache_conn.execute(f"""
            SELECT issuer,
                   COUNT(*) AS total_bonds,
                   SUM(CASE WHEN is_non_market = 1 THEN 1 ELSE 0 END) AS non_market_count,
                   SUM(CASE WHEN deviation_bp > ? THEN 1 ELSE 0 END) AS overpriced_count,
                   AVG(deviation_bp) AS avg_deviation_bp
            FROM bond_deviations
            WHERE {" AND ".join(filters)}
            GROUP BY issuer
        """, [OVERPRICED_THRESHOLD_BP, *params]).fetchall()

        ratios = {}
        for row in rows:
            total = row["total_bonds"] or 0
            if total:
                ratios[row["issuer"]] = {
                    "non_market_ratio": round((row["non_market_count"] or 0) / total, 4),
                    "overpriced_ratio": round((row["overpriced_count"] or 0) / total, 4),
                    "calculated_bonds": total,
                    "avg_deviation_bp": round(row["avg_deviation_bp"] or 0, 2),
                }
        return ratios
    except Exception:
        return {}
    finally:
        cache_conn.close()


def _attach_history_ratios(
    result: dict,
    exclude_short: bool,
    only_public: bool,
    issue_date: str,
    exclude_perpetual: bool = False,
    term_min: float | None = None,
    term_max: float | None = None,
) -> dict:
    """Attach issuer-level historical non-market and overpriced ratios for date view."""
    issuer_names = [row["issuer"] for row in result.get("issuers", [])]
    history_lookup = _read_history_ratios_from_cache(
        issuer_names,
        exclude_short,
        only_public,
        _date_offset(issue_date, -1),
        exclude_perpetual,
        term_min,
        term_max,
    )

    result["issuers"] = _issuer_rows_from_bonds(result["bonds"], history_lookup)
    return result


def _build_market_result(bonds: list[dict], start_date: str, end_date: str) -> dict:
    """Build overall and issue-date series statistics from cached bonds."""
    grouped: dict[str, list[dict]] = {}
    for bond in bonds:
        grouped.setdefault(bond["issue_date"], []).append(bond)

    daily = []
    for issue_date, day_bonds in sorted(grouped.items()):
        row = _summarize_bonds(day_bonds)
        row.pop("bonds", None)
        row["issue_date"] = issue_date
        row["normal_count"] = max(
            0,
            row["calculated_bonds"] - row["non_market_count"] - row["overpriced_count"],
        )
        daily.append(row)

    for index, row in enumerate(daily):
        window = daily[max(0, index - 19):index + 1]
        calculated = sum(item["calculated_bonds"] for item in window)
        row["rolling_non_market_ratio"] = round(
            sum(item["non_market_count"] for item in window) / calculated, 4
        ) if calculated else 0.0
        row["rolling_overpriced_ratio"] = round(
            sum(item["overpriced_count"] for item in window) / calculated, 4
        ) if calculated else 0.0

    summary = _summarize_bonds(bonds)
    summary.pop("bonds", None)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "summary": summary,
        "daily": daily,
        "_source": "cache",
    }


def _build_issuer_summary_result(bonds: list[dict], start_date: str, end_date: str) -> dict:
    """Aggregate all issuers with the Excel workpaper's calculation basis."""
    grouped: dict[str, list[dict]] = {}
    for bond in bonds:
        issuer = str(bond.get("issuer") or "").strip()
        if issuer:
            grouped.setdefault(issuer, []).append(bond)

    issuers = []
    for issuer, issuer_bonds in grouped.items():
        summary = _summarize_bonds(issuer_bonds, issuer=issuer)
        valid_bonds = [
            bond for bond in issuer_bonds
            if not bond.get("is_no_judgement") and bond.get("deviation_bp") is not None
        ]
        deviations = [float(bond["deviation_bp"]) for bond in valid_bonds]
        calculated_bonds = len(deviations)
        non_market_count = sum(1 for bond in valid_bonds if bond.get("is_non_market"))
        overpriced_count = sum(1 for bond in valid_bonds if bond.get("is_overpriced"))
        issuers.append({
            "issuer": issuer,
            "internal_rating": rating_for_issuer(issuer),
            "total_bonds": summary["total_bonds"],
            "issue_amount_yi": summary["issue_amount_yi"],
            "calculated_bonds": calculated_bonds,
            "calculable_ratio": _workpaper_round(calculated_bonds / summary["total_bonds"], 4)
            if summary["total_bonds"] else 0.0,
            "non_market_count": non_market_count,
            "non_market_ratio": _workpaper_round(non_market_count / calculated_bonds, 4)
            if calculated_bonds else None,
            "overpriced_count": overpriced_count,
            "overpriced_ratio": _workpaper_round(overpriced_count / calculated_bonds, 4)
            if calculated_bonds else None,
            "avg_deviation_bp": _workpaper_round(sum(deviations) / calculated_bonds, 2)
            if calculated_bonds else None,
            "avg_abs_deviation_bp": _workpaper_round(sum(abs(value) for value in deviations) / calculated_bonds, 2)
            if calculated_bonds else None,
            "min_deviation_bp": _workpaper_round(min(deviations), 2) if deviations else None,
            "max_deviation_bp": _workpaper_round(max(deviations), 2) if deviations else None,
            "first_issue_date": min(bond["issue_date"] for bond in issuer_bonds),
            "last_issue_date": max(bond["issue_date"] for bond in issuer_bonds),
        })

    issuers.sort(
        key=lambda row: (
            row["avg_abs_deviation_bp"] is None,
            -(row["avg_abs_deviation_bp"] or 0),
            -row["calculated_bonds"],
            row["issuer"],
        )
    )
    overall = _summarize_bonds(bonds)
    overall.pop("bonds", None)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "summary": overall,
        "issuers": issuers,
        "_source": "cache",
    }


def _filtered_issuer_result(
    result: dict,
    issuer: str,
    exclude_perpetual: bool,
    term_min: float | None,
    term_max: float | None,
) -> dict:
    filtered = _summarize_bonds(
        _filter_bond_terms(result.get("bonds", []), exclude_perpetual, term_min, term_max),
        issuer=issuer,
    )
    attach_internal_ratings(filtered.get("bonds", []))
    filtered["_source"] = result.get("_source", "cache")
    return filtered


def _calculate_issue_date_from_db(
    issue_date: str,
    exclude_short: bool,
    only_public: bool,
    exclude_perpetual: bool = False,
    term_min: float | None = None,
    term_max: float | None = None,
) -> dict:
    """实时计算指定发行日期的全市场可计算债券偏离数据。"""
    with get_connection() as conn:
        issues = fetch_new_issues(conn, issue_date, issue_date)
        if exclude_short and not issues.empty:
            issues = issues[issues["EFFECTIVE_TERM"] >= 1.0]
        if only_public and not issues.empty:
            issues = issues[issues["RAISEMODE"].astype(str).str.strip() == "1"]

        if issues.empty:
            return _build_date_result([], issue_date, "database")

        bonds = []
        for issuer in sorted(issues["ISSUER"].dropna().unique()):
            issuer_result = calculate_issuer_deviations(
                conn,
                issuer=issuer,
                start_date=issue_date,
            end_date=issue_date,
            exclude_short_term=exclude_short,
        )
            if issuer_result.get("bonds"):
                bonds.extend(_filter_public_bonds(issuer_result["bonds"], only_public))
                _write_to_cache(issuer_result, issue_date, issue_date)

        bonds = _filter_bond_terms(bonds, exclude_perpetual, term_min, term_max)
        bonds.sort(key=lambda b: (b.get("issuer") or "", b.get("bond_symbol") or ""))
        result = _build_date_result(bonds, issue_date, "database")
        return _attach_history_ratios(
            result,
            exclude_short,
            only_public,
            issue_date,
            exclude_perpetual,
            term_min,
            term_max,
        )


# ──────────────────────────────────────────────────────
# Flask 路由
# ──────────────────────────────────────────────────────

@pricing_bp.route("/")
def index():
    return render_template("index.html")


@pricing_bp.route("/api/cache/meta")
def api_cache_meta():
    """Expose cache coverage dates for frontend defaults."""
    return jsonify(_cache_coverage_meta())


@pricing_bp.route("/api/market")
def api_market():
    """Return cached all-market summary and issue-date time series."""
    start_date = request.args.get("start_date", HISTORY_START_DATE)
    end_date = request.args.get("end_date", _default_end_date())
    exclude_short = request.args.get("exclude_short", "0") == "1"
    only_public = request.args.get("only_public", "0") == "1"
    exclude_perpetual = request.args.get("exclude_perpetual", "0") == "1"
    try:
        datetime.strptime(start_date, "%Y%m%d")
        datetime.strptime(end_date, "%Y%m%d")
        term_min = _optional_float_arg("term_min")
        term_max = _optional_float_arg("term_max")
        if start_date > end_date or (term_min is not None and term_max is not None and term_min > term_max):
            raise ValueError("筛选区间无效")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    bonds = _read_bonds_from_cache(
        start_date,
        end_date,
        exclude_short,
        only_public,
        exclude_perpetual,
        term_min,
        term_max,
    )
    cache_missing = bonds is None
    if cache_missing:
        bonds = []
        _schedule_background_job(
            f"amount:{start_date}:{end_date}",
            _backfill_issue_amounts,
            start_date,
            end_date,
        )
    amount_pending = _schedule_amount_backfill(bonds, start_date, end_date)
    result = _build_market_result(bonds, start_date, end_date)
    result["_background_refresh"] = cache_missing
    result["_amount_pending"] = amount_pending
    return jsonify(result)


@pricing_bp.route("/api/issuer-summary")
def api_issuer_summary():
    """Return all-issuer aggregation from the cached issuance deviations."""
    start_date = request.args.get("start_date", HISTORY_START_DATE)
    end_date = request.args.get("end_date", _default_end_date())
    exclude_short = request.args.get("exclude_short", "0") == "1"
    only_public = request.args.get("only_public", "0") == "1"
    exclude_perpetual = request.args.get("exclude_perpetual", "0") == "1"
    try:
        datetime.strptime(start_date, "%Y%m%d")
        datetime.strptime(end_date, "%Y%m%d")
        term_min = _optional_float_arg("term_min")
        term_max = _optional_float_arg("term_max")
        if start_date > end_date or (term_min is not None and term_max is not None and term_min > term_max):
            raise ValueError("invalid filter range")
    except ValueError:
        return jsonify({"error": "日期或期限筛选无效"}), 400

    bonds = _read_bonds_from_cache(
        start_date,
        end_date,
        exclude_short,
        only_public,
        exclude_perpetual,
        term_min,
        term_max,
        apply_config_exclusions=False,
        use_cached_overpriced=True,
    )
    cache_missing = bonds is None
    result = _build_issuer_summary_result(bonds or [], start_date, end_date)
    result["_background_refresh"] = cache_missing
    return jsonify(result)


@pricing_bp.route("/api/search")
def api_search():
    """按发行人名称或债券简称模糊搜索发行人。"""
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"results": []})

    # 优先从缓存搜
    cached = _search_from_cache(query)
    if cached:
        return jsonify({"results": cached})

    # 缓存无结果，查 Oracle 的发行人和债券简称。
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            included_types = ",".join(f"'{t}'" for t in config.INCLUDED_BONDTYPE1)
            excluded_types = ",".join(f"'{t}'" for t in config.EXCLUDED_BONDTYPE1)
            excluded_issuer_filters = " ".join(
                f"AND n.COMPNAME NOT LIKE '%{keyword}%'"
                for keyword in config.EXCLUDED_ISSUER_KEYWORDS
            )
            cur.execute(f"""
                SELECT issuer, label, match_type
                FROM (
                    SELECT DISTINCT n.COMPNAME AS issuer,
                           n.COMPNAME AS label,
                           'issuer' AS match_type
                    FROM TQ_BD_NEWESTBASICINFO n
                    WHERE n.COMPNAME LIKE :q
                      AND n.ISVALID = 1
                      AND NVL(n.BONDTYPE1, '0') NOT IN ({excluded_types})
                      AND n.BONDTYPE1 IN ({included_types})
                      {excluded_issuer_filters}

                    UNION

                    SELECT DISTINCT n.COMPNAME AS issuer,
                           b.BONDSNAME AS label,
                           'bond' AS match_type
                    FROM TQ_BD_BASICINFO b
                    JOIN TQ_BD_NEWESTBASICINFO n ON n.SECODE = b.SECODE
                    WHERE b.BONDSNAME LIKE :q
                      AND b.ISVALID = 1
                      AND n.ISVALID = 1
                      AND NVL(b.BONDTYPE1, '0') NOT IN ({excluded_types})
                      AND b.BONDTYPE1 IN ({included_types})
                      {excluded_issuer_filters}
                )
                WHERE issuer IS NOT NULL AND label IS NOT NULL
                ORDER BY CASE WHEN label = :exact_query THEN 0
                              WHEN label LIKE :prefix_query THEN 1
                              ELSE 2 END,
                         match_type,
                         label
                FETCH FIRST 20 ROWS ONLY
            """, {
                "q": f"%{query}%",
                "exact_query": query,
                "prefix_query": f"{query}%",
            })
            results = [
                {"issuer": row[0], "label": row[1], "match_type": row[2]}
                for row in cur.fetchall()
                if row[0] and row[1]
            ]
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@pricing_bp.route("/api/issuer/<path:issuer_name>")
def api_issuer(issuer_name: str):
    """获取指定发行人的偏离数据"""
    start_date = request.args.get("start_date", "20240101")
    end_date = request.args.get("end_date", _default_end_date())
    exclude_short = request.args.get("exclude_short", "0") == "1"
    only_public = request.args.get("only_public", "0") == "1"
    exclude_perpetual = request.args.get("exclude_perpetual", "0") == "1"
    try:
        datetime.strptime(start_date, "%Y%m%d")
        datetime.strptime(end_date, "%Y%m%d")
        term_min = _optional_float_arg("term_min")
        term_max = _optional_float_arg("term_max")
        if start_date > end_date or (term_min is not None and term_max is not None and term_min > term_max):
            raise ValueError
    except ValueError:
        return jsonify({"error": "日期或期限筛选无效"}), 400

    # 策略1：优先读缓存
    cached_result, missing_ranges = _read_issuer_partial_from_cache(
        issuer_name,
        start_date,
        end_date,
        exclude_short,
        only_public,
    )
    background_refresh = bool(missing_ranges)
    if background_refresh:
        _schedule_background_job(
            f"issuer:{issuer_name}:{start_date}:{end_date}:{int(exclude_short)}",
            _refresh_issuer_cache,
            issuer_name,
            missing_ranges,
            exclude_short,
        )

    base_result = cached_result or _summarize_bonds([], issuer=issuer_name)
    result = _filtered_issuer_result(
        base_result, issuer_name, exclude_perpetual, term_min, term_max
    )
    result["_background_refresh"] = background_refresh
    result["_amount_pending"] = _schedule_amount_backfill(
        result.get("bonds", []), start_date, end_date
    )
    return jsonify(result)

@pricing_bp.route("/api/date/<issue_date>")
def api_issue_date(issue_date: str):
    """获取指定发行日期的发行人汇总和逐券偏离数据"""
    exclude_short = request.args.get("exclude_short", "0") == "1"
    only_public = request.args.get("only_public", "0") == "1"
    exclude_perpetual = request.args.get("exclude_perpetual", "0") == "1"
    try:
        datetime.strptime(issue_date, "%Y%m%d")
        term_min = _optional_float_arg("term_min")
        term_max = _optional_float_arg("term_max")
        if term_min is not None and term_max is not None and term_min > term_max:
            raise ValueError
    except ValueError:
        return jsonify({"error": "日期或期限筛选无效"}), 400
    bonds = _read_bonds_from_cache(
        issue_date,
        issue_date,
        exclude_short,
        only_public,
        exclude_perpetual,
        term_min,
        term_max,
    )
    if bonds:
        result = _build_date_result(bonds, issue_date, "cache")
        result = _attach_history_ratios(
            result,
            exclude_short,
            only_public,
            issue_date,
            exclude_perpetual,
            term_min,
            term_max,
        )
        result["_amount_pending"] = _schedule_amount_backfill(
            bonds, issue_date, issue_date
        )
        return jsonify(result)

    _schedule_background_job(
        f"date:{issue_date}:{int(exclude_short)}:{int(only_public)}",
        _refresh_date_cache,
        issue_date,
        exclude_short,
        only_public,
    )
    result = _build_date_result([], issue_date, "cache_miss")
    result["_background_refresh"] = True
    return jsonify(result)


if __name__ == "__main__":
    print(f"启动一级发行非市场化评估系统...")
    print(f"  缓存文件: {CACHE_DB_PATH}")
    if os.path.exists(CACHE_DB_PATH):
        try:
            c = sqlite3.connect(CACHE_DB_PATH)
            n = c.execute("SELECT COUNT(*) FROM issuer_summary").fetchone()[0]
            c.close()
            print(f"  缓存发行人: {n} 个")
        except Exception:
            pass
    else:
        print(f"  ⚠️ 缓存为空，首次查询将实时计算（较慢）")
        print(f"     建议运行: python cache_builder.py")
    print(f"  访问地址: http://localhost:{config.FLASK_PORT}")
    app.run(
        host="0.0.0.0",
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )

