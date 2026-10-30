"""Read-only broker analytics using saved quotes, sentiment and the DM daily report.

成交笔数与 TKN/GVN 方向以 DM 信用债盘面日报为权威来源（经纪商全市场
口径，``dm_report`` 维护历史）；报价与情绪来自本地快照缓存。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import dm_report, storage


def _ratios(row):
    n = row["num"]
    return {**row, "tkn_pct": round(row["tkn"] / n * 100, 2) if n else None,
            "gvn_pct": round(row["gvn"] / n * 100, 2) if n else None,
            "other_pct": round((n - row["tkn"] - row["gvn"]) / n * 100, 2) if n else None,
            "net": row["tkn"] - row["gvn"]}


def _report_deals(history: dict, start: str, end: str, scope: str) -> list[dict]:
    """盘面日报成交序列：日报发布于每交易日盘后，序列始终完整到上一交易日。"""
    days = history.get("days") or {}
    rows = []
    for day in sorted(days):
        if not start <= day <= end:
            continue
        record = days[day]
        if not isinstance(record, dict):
            continue
        if scope == "tier2":
            block = (record.get("sectors") or {}).get(dm_report.TIER2_SECTOR) or {}
            counts = {"num": block.get("num") or 0, "tkn": block.get("tkn") or 0,
                      "gvn": block.get("gvn") or 0, "trd": 0}
        else:
            counts = {"num": record.get("num") or 0, "tkn": record.get("tkn") or 0,
                      "gvn": record.get("gvn") or 0, "trd": record.get("trd") or 0}
        if counts["num"]:
            rows.append(_ratios({"date": day, **counts}))
    return rows


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
    # 报价偏离结构（breakdown）基于最新快照实时计算；情绪逐日序列携带当日
    # 分组，供结构图的区间趋势视图使用（tier2 取细分点位自带的分组）。
    emotion = storage.calculate_market_emotion(base, snapshot)
    daily = {}
    for p in sorted(history, key=lambda x: x.get("observed_at") or x.get("scheduled_for") or ""):
        day = str(p.get("observed_at") or p.get("scheduled_for") or "")[:10]
        if start <= day <= end:
            v = (p.get("tier2_capital") or {}) if scope == "tier2" else p
            daily[day] = {"date": day, "value": v.get("value"), "count": v.get("count", 0),
                          "breakdown": v.get("breakdown") or {}}
    # 成交序列：盘面日报为权威来源（num/tkn/gvn，全市场经纪商口径）。
    report_history = dm_report.load_history()
    report_rows = _report_deals(report_history, start, end, scope)
    history_days = sorted(report_history.get("days") or {})
    return {"range": {"start": start, "end": end}, "scope": scope,
            "snapshot_at": snapshot.get("generated_at"), "snapshot_in_range": start <= anchor <= end,
            "stale": anchor < date.today().isoformat(),
            "breakdown": emotion["breakdown"], "emotion": list(daily.values()),
            "deals": report_rows,
            "deals_source": "dm_report",
            "report_updated": report_history.get("updated_at"),
            "report_range": {"first": history_days[0] if history_days else None,
                             "last": history_days[-1] if history_days else None}}
