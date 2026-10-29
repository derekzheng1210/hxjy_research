"""DM量化API：单券经纪商成交数据（历史日度统计 + 盘中分钟时点）。

数据口径（仅经纪商，price_source/data_source=1）：
- 日度统计：/bond/market-data/date —— 成交笔数、GVN/TKN/TRD 笔数、收益率区间、
  中债估值与偏离；行情类次日更新，历史日数据不可变。
- 盘中时点：/bond/market-data/bars（1分钟K线）—— 仅在有成交的分钟出bar，含该分钟
  成交收益率/净价与 GVN/TKN/TRD 笔数；当日实时更新，历史日不可变。
- 当日：date 序列次日才有，直接拉分钟线并在本地汇总日度统计（估值偏离由前端用
  报价历史的当日估值补算）。

量化API为首选；不可用（订阅失效/网络异常，熔断期内不再重试）时降级：
- 当日：使用经纪商抓取时经 ``write_web_today_stats`` 增量写入的网页端今日成交
  汇总（stats 带 stats_source="web" 标记，无分钟时点）；
- 历史日：仅返回已缓存日期，不报错；量化恢复后 web 标记日随一次区间重取被
  官方数据覆盖修复（此后永久缓存，不重复抓取）。

缓存：PORTAL_DATA_ROOT/data/dm_deals/{bare_code}/{YYYYMMDD}.json，
内容 {"stats": 日度统计|null, "minutes": 分钟列表|null, "stats_source"?: "web"}。
历史日永久有效；当日文件按 TTL 过期重取（web 来源的 stats 读取不受 TTL 限制，
由经纪商抓取节奏刷新）。凭据 INNO_APP_KEY/INNO_APP_SECRET 走
.env / 环境变量，缺失时同样降级到网页端备源。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from broker_market.storage import bare_code, finite_number
from paths import DATA_DIR

_logger = logging.getLogger(__name__)

# 当日数据盘中持续变化：分钟线按该TTL重取；日度统计序列不含当日，无需TTL。
TODAY_TTL_SECONDS = max(60, int(os.environ.get("DM_DEALS_TODAY_TTL_SECONDS", "300")))
DM_DEALS_TIMEOUT_SECONDS = max(10, int(os.environ.get("DM_DEALS_TIMEOUT_SECONDS", "30")))
DM_DEALS_MAX_WORKERS = max(1, int(os.environ.get("DM_DEALS_MAX_WORKERS", "4")))
# date序列单次区间上限（自然日，含首尾），超出自动分段。
DATE_SERIES_CHUNK_DAYS = 90
# 量化API失败后的熔断时长：期间直接走网页端备源，到期自动回归量化首选。
QUANT_COOLDOWN_SECONDS = max(30, int(os.environ.get("DM_DEALS_QUANT_COOLDOWN_SECONDS", "300")))

_DATE_PATH = "/dm-quant-func-service/api/v1/bond/market-data/date"
_BARS_PATH = "/dm-quant-func-service/api/v1/bond/market-data/bars"

_deals_dir: Path = DATA_DIR / "dm_deals"

# 同参数响应缓存（避免区间内全部命中磁盘缓存时反复重组/并发重复拉取）。
_payload_lock = threading.RLock()
_payload_cache: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
_PAYLOAD_CACHE_MAX_ENTRIES = 48

# 日缓存读改写串行化：调度器网页端写入与页面视图的量化写入可能并发触碰同一文件。
_cache_write_lock = threading.Lock()

# 量化API熔断：失败后 _quant_down_until 内不再尝试，备源顶上；到期重试自动回归。
_breaker_lock = threading.Lock()
_quant_down_until = 0.0


def _quant_usable() -> bool:
    return time.time() >= _quant_down_until


def _mark_quant_down() -> None:
    global _quant_down_until
    with _breaker_lock:
        _quant_down_until = time.time() + QUANT_COOLDOWN_SECONDS


# SDK client 非线程安全（共享 requests.Session）：每线程独立实例。
_thread_local = threading.local()


class DealsUnavailable(RuntimeError):
    """成交数据整体不可用（未安装SDK/未配置凭据），调用方应以可用性提示呈现。"""


class DealsFetchError(RuntimeError):
    """DM量化API调用失败（网络/权限/限流等），可稍后重试。"""


def _api_client():
    try:
        from dm_quant_api_client import DMQuantApiClient
    except ImportError as exc:  # pragma: no cover - 依赖守卫
        raise DealsUnavailable(
            "DM量化API客户端未安装（dm_quant_api_client），成交数据不可用"
        ) from exc
    app_key = os.environ.get("INNO_APP_KEY", "").strip()
    app_secret = os.environ.get("INNO_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        raise DealsUnavailable("DM量化API凭据未配置（INNO_APP_KEY/INNO_APP_SECRET），成交数据不可用")
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = DMQuantApiClient(
            app_key=app_key, app_secret=app_secret, pythonic=True,
            timeout=DM_DEALS_TIMEOUT_SECONDS,
        )
        _thread_local.client = client
    return client


def _post(payload: dict[str, Any], api_path: str) -> list[dict[str, Any]]:
    import pandas as pd

    try:
        result = _api_client().post_data(payload, api_path)
    except DealsUnavailable:
        raise
    except Exception as exc:  # 网络/HTTP/解密/参数错误统一转中文提示
        raise DealsFetchError(f"DM量化API调用失败：{str(exc)[:200]}") from exc
    if result is None:
        return []
    if isinstance(result, pd.DataFrame):
        return result.to_dict(orient="records")
    if isinstance(result, dict):
        return [result]
    try:
        return list(result)
    except TypeError:  # pragma: no cover - 兜底
        return []


# ---------------------------------------------------------------- 缓存读写

def _day_cache_path(code: str, day: str) -> Path:
    return _deals_dir / bare_code(code) / f"{day}.json"


def _read_day_cache(code: str, day: str, *, ignore_ttl: bool = False) -> dict[str, Any] | None:
    path = _day_cache_path(code, day)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if day == date.today().strftime("%Y%m%d") and not ignore_ttl:
        fetched_at = data.get("fetched_at") or 0
        if not isinstance(fetched_at, (int, float)) or time.time() - fetched_at > TODAY_TTL_SECONDS:
            return None  # 当日数据过期，重取
    return data


def _write_day_cache(code: str, day: str, data: dict[str, Any]) -> None:
    path = _day_cache_path(code, day)
    if day == date.today().strftime("%Y%m%d"):
        data = {**data, "fetched_at": time.time()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        _logger.warning("DM成交缓存写入失败 %s", path, exc_info=True)


def _merge_day_cache(code: str, day: str, stats=None, minutes=None, *, stats_set=False, minutes_set=False, stats_source=False) -> dict[str, Any]:
    with _cache_write_lock:
        # ignore_ttl：当日文件即使过期也保留既有字段，只覆盖本次写入的部分。
        cached = _read_day_cache(code, day, ignore_ttl=True) or {}
        if stats_set:
            cached["stats"] = stats
            if stats_source:
                cached["stats_source"] = "web"
            else:
                cached.pop("stats_source", None)
        if minutes_set:
            cached["minutes"] = minutes
        _write_day_cache(code, day, cached)
        return cached


def write_web_today_stats(deals: dict[str, dict[str, Any]]) -> int:
    """把网页端今日成交按券增量写入日缓存 stats（量化API的备源）。

    ``deals`` 为 ``dm_trades`` 落盘的当日成交映射（债券代码 -> 汇总）。写入带
    stats_source="web" 标记：量化恢复后这些日期随一次区间重取被官方数据覆盖；
    分钟时点始终以量化API为准，此处不写 minutes。
    """
    day = date.today().strftime("%Y%m%d")
    written = 0
    for code, deal in deals.items():
        if not isinstance(deal, dict):
            continue
        stats = {
            "num": int(deal.get("num") or 0),
            "gvn": int(deal.get("gvn") or 0),
            "tkn": int(deal.get("tkn") or 0),
            "trd": int(deal.get("trd") or 0),
            "close_yield": deal.get("yield"),
            "high_yield": deal.get("high"),
            "low_yield": deal.get("low"),
            "cb_yield": None,
            "dev_bp": deal.get("sub_cb"),
        }
        if stats["num"] <= 0:
            continue
        _merge_day_cache(code, day, stats=stats, stats_set=True, stats_source=True)
        written += 1
    return written


# ---------------------------------------------------------------- API 字段清洗

def _int_field(row: dict[str, Any], key: str) -> int:
    value = finite_number(row.get(key))
    return int(value) if value is not None else 0


def _float_field(row: dict[str, Any], key: str) -> float | None:
    value = finite_number(row.get(key))
    return round(float(value), 4) if value is not None else None


def _clean_stats_row(row: dict[str, Any]) -> dict[str, Any]:
    """date序列行 -> 精简日度统计（历史日不可变）。"""
    dev = finite_number(row.get("yield_sub_cb"))
    return {
        "num": _int_field(row, "trading_num"),
        "gvn": _int_field(row, "gvn_trade_num"),
        "tkn": _int_field(row, "tkn_trade_num"),
        "trd": _int_field(row, "trd_trade_num"),
        "close_yield": _float_field(row, "yield"),
        "high_yield": _float_field(row, "high_yield"),
        "low_yield": _float_field(row, "low_yield"),
        "cb_yield": _float_field(row, "cb_ytm"),
        "dev_bp": round(dev * 100, 1) if dev is not None else None,
    }


def _clean_bars_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """bars行 -> 分钟成交时点；无成交或价格缺失的行丢弃。"""
    if _int_field(row, "trade_num") <= 0:
        return None
    yield_v = finite_number(row.get("close_yield"))
    price_v = finite_number(row.get("close_net_price"))
    if yield_v is None and price_v is None:
        return None
    return {
        "time": str(row.get("issue_time") or "")[:5],
        "yield": round(float(yield_v), 4) if yield_v is not None else None,
        "net_price": round(float(price_v), 4) if price_v is not None else None,
        "gvn": _int_field(row, "gvn_trade_num"),
        "tkn": _int_field(row, "tkn_trade_num"),
        "trd": _int_field(row, "trd_trade_num"),
        "num": _int_field(row, "trade_num"),
    }


# ---------------------------------------------------------------- API 拉取

def _iter_chunks(start_day: str, end_day: str) -> list[tuple[str, str]]:
    """按date序列90天上限切段（含首尾自然日）。"""
    start = datetime.strptime(start_day, "%Y%m%d").date()
    end = datetime.strptime(end_day, "%Y%m%d").date()
    chunks = []
    while start <= end:
        stop = min(end, start + timedelta(days=DATE_SERIES_CHUNK_DAYS - 1))
        chunks.append((start.strftime("%Y%m%d"), stop.strftime("%Y%m%d")))
        start = stop + timedelta(days=1)
    return chunks


def _fetch_stats_range(code: str, start_day: str, end_day: str) -> dict[str, dict[str, Any]]:
    """拉取区间日度统计，返回 {YYYYMMDD: 精简统计}；无行=非交易日/无数据。"""
    stats: dict[str, dict[str, Any]] = {}
    for chunk_start, chunk_end in _iter_chunks(start_day, end_day):
        rows = _post(
            {
                "security_id_list": [code],
                "data_source_list": [1],
                "start_date": _fmt_day(chunk_start),
                "end_date": _fmt_day(chunk_end),
            },
            _DATE_PATH,
        )
        for row in rows:
            day = str(row.get("issue_date") or "").replace("-", "")
            if len(day) == 8 and day.isdigit():
                stats[day] = _clean_stats_row(row)
    return stats


def _fetch_minutes(code: str, day: str) -> list[dict[str, Any]]:
    """拉取单日分钟成交时点（升序）。非交易日/无成交返回空表。"""
    rows = _post(
        {
            "security_id_list": [code],
            "data_source_list": [1],
            "kline_type": 1,
            "start_datetime": _fmt_day(day),
            "end_datetime": _fmt_day(day),
        },
        _BARS_PATH,
    )
    minutes = [cleaned for row in rows if (cleaned := _clean_bars_row(row)) is not None]
    minutes.sort(key=lambda item: item.get("time") or "")
    return minutes


def _fmt_day(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day


def _summary_from_minutes(minutes: list[dict[str, Any]]) -> dict[str, Any]:
    """当日（date序列尚无）用分钟线本地汇总。"""
    yields_ = [m["yield"] for m in minutes if m.get("yield") is not None]
    return {
        "num": sum(m.get("num") or 0 for m in minutes),
        "gvn": sum(m.get("gvn") or 0 for m in minutes),
        "tkn": sum(m.get("tkn") or 0 for m in minutes),
        "trd": sum(m.get("trd") or 0 for m in minutes),
        "close_yield": yields_[-1] if yields_ else None,
        "high_yield": max(yields_) if yields_ else None,
        "low_yield": min(yields_) if yields_ else None,
        "cb_yield": None,
        "dev_bp": None,
    }


# ---------------------------------------------------------------- 响应组装

def build_deal_history(
    code: str, *, name: str = "", range_start: str, range_end: str, trading_days: int = 0,
) -> dict[str, Any]:
    """组装区间经纪商成交：日度统计 + 分钟时点。

    ``range_start``/``range_end`` 为 YYYYMMDD；区间与债券解析由调用方
    （bond_detail.service.resolve_quote_history_selection）完成，保证与报价历史同区间。

    量化API为首选：可用时行为与历史版本一致（当日分钟线、历史日统计、
    web 备源日被官方数据覆盖修复）。量化不可用（订阅失效/网络异常，熔断期内
    不重试）时降级：当日用网页端备源汇总（无分钟时点），历史日仅返回已缓存
    日期，payload 以 today_source / quant_available 标注数据来源。
    """
    today = date.today().strftime("%Y%m%d")
    if range_start > range_end:
        raise ValueError("开始日期不能晚于结束日期")
    cache_key = (code, range_start, range_end)
    with _payload_lock:
        cached = _payload_cache.get(cache_key)
        if cached and cached[0] == _version_token(code, range_start, range_end, today):
            return cached[1]

    stats_span_start = range_start
    stats_span_end = min(range_end, _prev_day(today))  # date序列不含当日
    day_cache: dict[str, dict[str, Any]] = {}
    if stats_span_start <= stats_span_end:
        span_days = _days_between(stats_span_start, stats_span_end)
        # web 备源标记日视为缺失：量化恢复后随一次区间重取修复为官方口径。
        missing = []
        for day in span_days:
            cached_day = _read_day_cache(code, day)
            day_cache[day] = cached_day or {}
            if cached_day is None or "stats" not in cached_day or cached_day.get("stats_source") == "web":
                missing.append(day)
        if missing and _quant_usable():
            try:
                fetched = _fetch_stats_range(code, stats_span_start, stats_span_end)
            except (DealsUnavailable, DealsFetchError):
                # 量化不可用：缺失日留空，仅返回已缓存日期，不阻断整个响应。
                _mark_quant_down()
            else:
                for day in span_days:
                    if day in fetched:
                        _merge_day_cache(code, day, stats=fetched[day], stats_set=True)
                    else:
                        _merge_day_cache(code, day, stats=None, stats_set=True)
                for day in span_days:
                    day_cache[day] = _read_day_cache(code, day) or {}

    # 需要分钟时点的日：历史有成交日 + 当日（当日序列无统计，直接拉分钟线）
    deal_days = [day for day in sorted(day_cache) if (day_cache[day].get("stats") or {}).get("num", 0) > 0]
    include_today = range_start <= today <= range_end
    minute_days = list(deal_days) + ([today] if include_today else [])

    def ensure_minutes(day: str) -> None:
        cached_day = _read_day_cache(code, day)
        if cached_day is not None and cached_day.get("minutes") is not None:
            return
        if not _quant_usable():
            return  # 量化不可用：该日无分钟时点，日度汇总照常展示
        try:
            minutes = _fetch_minutes(code, day)
        except (DealsUnavailable, DealsFetchError):
            _mark_quant_down()
            return
        _merge_day_cache(code, day, minutes=minutes, minutes_set=True)

    if minute_days:
        if len(minute_days) == 1:
            ensure_minutes(minute_days[0])
        else:
            with ThreadPoolExecutor(max_workers=DM_DEALS_MAX_WORKERS) as pool:
                for _ in pool.map(ensure_minutes, minute_days):
                    pass

    days_payload: list[dict[str, Any]] = []
    totals = {"num": 0, "gvn": 0, "tkn": 0, "trd": 0}
    for day in sorted(day_cache):
        stats = day_cache[day].get("stats") or {}
        if stats.get("num", 0) <= 0:
            continue
        minutes = (_read_day_cache(code, day) or {}).get("minutes") or []
        for key in totals:
            totals[key] += int(stats.get(key) or 0)
        days_payload.append({"date": _fmt_day(day), **stats, "minutes": minutes})

    today_source = ""
    today_entry: dict[str, Any] | None = None
    if include_today:
        # 当日首选量化分钟线；量化不可用时回退经纪商抓取写入的网页端当日汇总。
        fresh_today = _read_day_cache(code, today)
        today_minutes = fresh_today.get("minutes") if fresh_today else None
        if today_minutes is None and _quant_usable():
            try:
                today_minutes = _fetch_minutes(code, today)
                _merge_day_cache(code, today, minutes=today_minutes, minutes_set=True)
            except (DealsUnavailable, DealsFetchError):
                _mark_quant_down()
                today_minutes = None
        if today_minutes:
            stats = _summary_from_minutes(today_minutes)
            today_entry = {"date": _fmt_day(today), **stats, "minutes": today_minutes, "is_today": True}
            today_source = "quant"
        else:
            cached_today = _read_day_cache(code, today, ignore_ttl=True) or {}
            web_stats = cached_today.get("stats") if cached_today.get("stats_source") == "web" else None
            if web_stats and web_stats.get("num", 0) > 0:
                today_entry = {"date": _fmt_day(today), **web_stats, "minutes": [], "is_today": True}
                today_source = "web"
        if today_entry:
            for key in totals:
                totals[key] += int(today_entry.get(key) or 0)
            days_payload.append(today_entry)

    payload = {
        "code": code,
        "name": name,
        "range": {"start": _fmt_day(range_start), "end": _fmt_day(range_end), "trading_days": trading_days},
        "summary": {**totals, "days": len(days_payload)},
        "days": days_payload,
        "today_source": today_source,
        "quant_available": _quant_usable(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    version = _version_token(code, range_start, range_end, today)
    payload["version"] = version
    with _payload_lock:
        _payload_cache[cache_key] = (version, payload)
        if len(_payload_cache) > _PAYLOAD_CACHE_MAX_ENTRIES:
            stale = list(_payload_cache)[: len(_payload_cache) - _PAYLOAD_CACHE_MAX_ENTRIES]
            for key in stale:
                _payload_cache.pop(key, None)
    return payload


def _days_between(start_day: str, end_day: str) -> list[str]:
    start = datetime.strptime(start_day, "%Y%m%d").date()
    end = datetime.strptime(end_day, "%Y%m%d").date()
    days: list[str] = []
    while start <= end:
        days.append(start.strftime("%Y%m%d"))
        start += timedelta(days=1)
    return days


def _prev_day(day: str) -> str:
    return (datetime.strptime(day, "%Y%m%d").date() - timedelta(days=1)).strftime("%Y%m%d")


def _version_token(code: str, range_start: str, range_end: str, today: str) -> str:
    """响应版本：所涉缓存文件签名 + 当日TTL时间桶，历史日命中缓存时稳定不变。"""
    import hashlib

    parts = [code, range_start, range_end]
    cache_dir = _deals_dir / bare_code(code)
    if cache_dir.is_dir():
        for path in sorted(cache_dir.glob("*.json")):
            day = path.stem
            if range_start <= day <= range_end:
                try:
                    stat = path.stat()
                    parts.append(f"{day}:{stat.st_mtime_ns}")
                except OSError:
                    continue
    if range_start <= today <= range_end:
        parts.append(f"bucket:{int(time.time() // TODAY_TTL_SECONDS)}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def deals_configured() -> bool:
    """凭据与SDK是否就绪（供诊断/前端可用性提示）。"""
    try:
        _api_client()
        return True
    except DealsUnavailable:
        return False


# 联通性探测：用一只高流动性债券查最近一个自然日的日度统计，验证
# SDK/凭据/网络/加解密全链路；返回0行也算连通（非交易日或无成交）。
_PROBE_CODE = os.environ.get("DM_DEALS_PROBE_CODE", "240215.IB")


def deals_overview() -> dict[str, Any]:
    """配置概览（供后台展示；密钥脱敏）。"""
    try:
        import dm_quant_api_client  # noqa: F401
        sdk_installed = True
    except ImportError:
        sdk_installed = False
    app_key = os.environ.get("INNO_APP_KEY", "").strip()
    app_secret = os.environ.get("INNO_APP_SECRET", "").strip()
    return {
        "configured": bool(sdk_installed and app_key and app_secret),
        "sdk_installed": sdk_installed,
        "key_hint": "INNO_APP_KEY / INNO_APP_SECRET",
        "app_key_masked": f"{app_key[:4]}***{app_key[-4:]}" if app_key else "",
        "secret_configured": bool(app_secret),
    }


def test_deals_connectivity() -> dict[str, Any]:
    """联通性检测：一次最小 date 序列请求，只读、不写缓存。"""
    started = time.time()
    day = _prev_day(date.today().strftime("%Y%m%d"))
    try:
        rows = _post(
            {
                "security_id_list": [_PROBE_CODE],
                "data_source_list": [1],
                "start_date": _fmt_day(day),
                "end_date": _fmt_day(day),
            },
            _DATE_PATH,
        )
    except DealsUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    except DealsFetchError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "latency_ms": int((time.time() - started) * 1000),
            "probe_code": _PROBE_CODE,
        }
    return {
        "ok": True,
        "latency_ms": int((time.time() - started) * 1000),
        "probe_code": _PROBE_CODE,
        "rows": len(rows),
    }
