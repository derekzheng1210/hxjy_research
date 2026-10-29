"""DM 网页端今日经纪商成交：与最优报价同会话拉取，落盘为择券成交列数据源。

产出 ``PORTAL_DATA_ROOT/data/broker_market/latest_deals.json``：
``{trade_date, generated_at, dm_account, deal_count, deals: {债券代码: {...}}}``，
每只券含最新成交（时间/收益率/净价）与当日汇总（笔数、TKN/GVN/TRD、最高/最低收益率）。
仅当日数据对页面有效，``app.bond_picker_market_meta`` 按 trade_date 门控跨日文件。
当日无成交（早盘/节假日）保存空 deals，属正常状态。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .dm_deals import write_web_today_stats
from .storage import DEALS_PATH, atomic_write_json

# DM priceStatus：2=撤销，不进入成交列。
PRICE_STATUS_CANCELLED = 2

# 证券代码形态：6 位以上纯数字，可带交易所后缀。债券信息接口偶发对个别
# bondUniCode 返回内部短码（如 "123"），校验不过的映射整行跳过，避免脏码
# 进入成交列与逐券日缓存（该券本场快照缺成交，下场抓取自愈）。
CODE_PATTERN = re.compile(r"^\d{6,}(\.(IB|SH|SZ|BJ))?$")


def valid_bond_code(code: Any) -> bool:
    return isinstance(code, str) and bool(CODE_PATTERN.match(code))


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits)


def _time_text(value: Any) -> str:
    """epoch 毫秒/秒 -> HH:MM:SS（本地时区，与 DM 页面展示一致）。"""
    if not isinstance(value, (int, float)) or not value:
        return ""
    seconds = value / 1000 if value > 10_000_000_000 else value
    try:
        return datetime.fromtimestamp(seconds).strftime("%H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return ""


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_deals(stat_rows: list[dict[str, Any]], codes_by_unicode: dict[int, str]) -> dict[str, dict[str, Any]]:
    """今日成交概览行 -> {债券代码: 最新成交+当日汇总}，按证券代码键控。

    无法映射出证券代码或 priceStatus=撤销 的行不进入结果（对应债券在页面
    显示为无成交）。理论上一券一行；若上游重复，保留 issueTime 最新的一行。
    """
    deals: dict[str, dict[str, Any]] = {}
    for row in stat_rows:
        if _int_or_zero(row.get("priceStatus")) == PRICE_STATUS_CANCELLED:
            continue
        try:
            unicode_key = int(row.get("bondUniCode") or 0)
        except (TypeError, ValueError):
            continue
        code = codes_by_unicode.get(unicode_key)
        if not code or not valid_bond_code(code):
            continue
        previous = deals.get(code)
        if previous is not None and str(row.get("issueTime") or 0) <= previous.get("_ts", ""):
            continue
        deals[code] = {
            "_ts": str(row.get("issueTime") or 0),
            "time": _time_text(row.get("issueTime")),
            "yield": _round(row.get("yield")),
            "net": _round(row.get("netPrice")),
            "full": _round(row.get("fullPrice")),
            "num": _int_or_zero(row.get("stat_tradeNum")),
            "tkn": _int_or_zero(row.get("stat_tknTradeNum")),
            "gvn": _int_or_zero(row.get("stat_gvnTradeNum")),
            "trd": _int_or_zero(row.get("stat_trdTradeNum")),
            "high": _round(row.get("stat_highYield")),
            "low": _round(row.get("stat_lowYield")),
            "sub_cb": _round(row.get("tradeYieldSubCbBp"), 1),
        }
    for deal in deals.values():
        deal.pop("_ts", None)
    return deals


def fetch_and_save_today_trades(client: Any, *, username: str = "") -> dict[str, Any]:
    """用已登录的 DM 会话拉今日成交概览并原子落盘。

    ``client`` 为 ``dm_client_local.DMClient``（测试传仿件）。债券唯一标识
    到证券代码的映射经 DM 债券基础信息接口批量补全。
    """
    from .dm_client_local import choose_value

    stat_rows, _audit = client.fetch_partitioned_today_trade_stats()
    unicode_keys = sorted({
        int(row["bondUniCode"]) for row in stat_rows
        if isinstance(row.get("bondUniCode"), int)
    })
    codes_by_unicode: dict[int, str] = {}
    if unicode_keys:
        for item in client.fetch_bond_info(unicode_keys):
            value = choose_value(item, ("bondUniCode", "bond_uni_code", "uniCode"))
            try:
                codes_by_unicode[int(value)] = str(
                    choose_value(item, ("bondCode", "securityCode", "code")) or ""
                ).upper()
            except (TypeError, ValueError):
                continue
    deals = build_deals(stat_rows, codes_by_unicode)
    now = datetime.now()
    payload = {
        "trade_date": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "dm_account": username,
        "deal_count": len(deals),
        "deals": deals,
    }
    atomic_write_json(DEALS_PATH, payload)
    # 增量写入单券日缓存：债券详情页的当日行与未来历史日直接复用，不重复抓取；
    # 带 stats_source="web" 标记，量化API恢复后随一次区间重取修复为官方口径。
    payload["web_stats_written"] = write_web_today_stats(deals)
    return payload
