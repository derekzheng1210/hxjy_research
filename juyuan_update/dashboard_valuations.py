"""一级发行看板个券中债估值：聚源(Oracle)回退源。

DM 量化API（看板 dm-refresh 每日 19:00 的估值来源）订阅失效期间行情接口整表
返回 HTTP 429 "无订阅套餐"，valuations.json 的中债/中证估值被写成全 null，
看板「推荐个券 / 中标个券」的中债估值列整列消失。本模块随门户 8:30 每日
任务，按看板自己的个券清单（参与/中标记录 store/bids.json 镜像 + 推荐个券
recommended.json）查询聚源中债估值（TQ_QT_CBESTIMATE，DATASOURCE=1、优先
VALUATIONTYPE=1，与择券工具/债券详查同口径），落盘 oracle_cb_fallback.json。
看板 dm-refresh 在 DM 无值时按 security_id 取用填补中债列；DM 订阅恢复后
其值自动优先（含权债回归 DM 行权口径），本文件照常更新不产生影响。

中证估值无聚源回退（TQ_QT_CBESTIMATE 的 DATASOURCE=2 仅覆盖极少量利率债），
DM 恢复前交易所券的中证列保持为空。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from paths import BOND_DASHBOARD_DIR

from .db import connect, fetch_cnbd_yields_by_symbol, latest_cnbd_valuation_date
from .unified_excel import load_json, write_json

FALLBACK_PATH = BOND_DASHBOARD_DIR / "oracle_cb_fallback.json"
VALUATIONS_PATH = BOND_DASHBOARD_DIR / "valuations.json"


def _clean_code(code: Any) -> str | None:
    """代码清洗为标准后缀格式——与看板 dm-refresh 的 cleanCode / fetch_valuations.py 一致。"""
    if not code:
        return None
    text = str(code).strip().upper()
    if text.endswith((".IB", ".SH", ".SZ")):
        return text
    if re.fullmatch(r"\d+", text):
        return text + (".IB" if len(text) >= 8 else ".SH")
    return text or None


def _dashboard_bond_codes() -> dict[str, str | None]:
    """看板个券清单（债券简称 -> 标准代码，缺代码记 None）。

    与 dm-refresh refreshValuations 的收集规则一致：投标记录（SQLite 权威、
    store/bids.json 为同步镜像）+ 推荐个券 recommended.json，同名首见优先、
    后见补缺代码。
    """
    bonds: dict[str, str | None] = {}
    for rel, name_key, code_key in (
        ("store/bids.json", "bondName", "securityId"),
        ("recommended.json", "name", "code"),
    ):
        rows = load_json(BOND_DASHBOARD_DIR / rel, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get(name_key) or "").strip()
            if not name:
                continue
            code = _clean_code(row.get(code_key))
            if name not in bonds:
                bonds[name] = code
            elif not bonds[name] and code:
                bonds[name] = code
    return bonds


def refresh_dashboard_cb_fallback() -> dict:
    """查询看板个券的聚源中债估值并落盘回退文件，返回执行摘要。

    看板数据目录不存在（未部署）时跳过不报错；其余异常向上抛出由调用方
    （broker_market.scheduler.run_daily_data_update）记录，不阻断每日任务。
    """
    if not BOND_DASHBOARD_DIR.is_dir():
        return {"skipped": "看板数据目录不存在"}
    bonds = _dashboard_bond_codes()
    if not bonds:
        return {"skipped": "看板个券清单为空"}
    codes = [code for code in bonds.values() if code]
    with connect() as conn:
        trade_date = str(latest_cnbd_valuation_date(conn) or "")
        yields = fetch_cnbd_yields_by_symbol(conn, codes, trade_date) if trade_date else {}
    payload = {
        "meta": {
            "date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if len(trade_date) == 8 else trade_date,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "聚源 TQ_QT_CBESTIMATE（DATASOURCE=1，优先 VALUATIONTYPE=1 到期口径）",
            "bond_count": len(bonds),
            "valued_count": len(yields),
        },
        "yields": {code: value for code, value in yields.items()},
    }
    write_json(FALLBACK_PATH, payload)
    return {"bonds": len(bonds), "valued": len(yields), "date": payload["meta"]["date"]}


def apply_cb_fallback_to_valuations() -> dict:
    """把聚源中债估值直接补进 valuations.json 的空 cb 位（部署过渡桥）。

    看板 Node 服务已换新代码（dm-refresh 自带回退）后无需本步骤——文件里
    meta.cb_fallback 存在即视为新代码已接管，跳过。旧代码仍在运行的过渡期，
    其 19:00 刷新会把 DM 失效的估值写成全 null，本步骤在其后重补：
    按 security_id 只填 cb 为 null 的券，其余字段（市场/中证/DM值）一律不动。
    """
    valuations = load_json(VALUATIONS_PATH, {})
    bonds = valuations.get("bonds")
    if not isinstance(bonds, dict) or not bonds:
        return {"skipped": "valuations.json 缺失或为空"}
    if isinstance(valuations.get("meta"), dict) and valuations["meta"].get("cb_fallback"):
        return {"skipped": "新代码已接管（meta.cb_fallback 存在）"}
    fallback = load_json(FALLBACK_PATH, {})
    yields = fallback.get("yields") if isinstance(fallback, dict) else None
    if not isinstance(yields, dict) or not yields:
        return {"skipped": "oracle_cb_fallback.json 缺失或为空"}
    oracle_date = str((fallback.get("meta") or {}).get("date") or "")
    filled = 0
    dm_valued = 0
    for entry in bonds.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("cb_basis") in ("行权", "到期"):
            dm_valued += 1
        if entry.get("cb") is not None:
            continue
        sid = str(entry.get("security_id") or "")
        value = yields.get(sid)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        entry["cb"] = value
        entry["cb_basis"] = "聚源到期"
        filled += 1
    if not filled:
        return {"skipped": "无可填补的空位", "dm_valued": dm_valued}
    meta = valuations.get("meta")
    if not isinstance(meta, dict):
        meta = valuations["meta"] = {}
    if dm_valued == 0 and oracle_date:
        meta["date"] = oracle_date  # DM 全空时估值日期以聚源口径为准
    meta["cb_fallback"] = {
        "source": "聚源中债估值（门户任务 oracle_cb_fallback.json）",
        "date": oracle_date,
        "filled": filled,
        "note": "DM 无值的券以聚源到期口径填补中债列；DM 订阅恢复后自动回归 DM 口径",
    }
    write_json(VALUATIONS_PATH, valuations)
    return {"filled": filled, "dm_valued": dm_valued, "date": meta.get("date") or ""}


def _wait_for_node_refresh(deadline_seconds: int = 1800) -> None:
    """等看板 19:00 的旧代码刷新收尾（最多 deadline_seconds），避免补桥被随后的写入覆盖。

    refresh_state.json 的 lastRun 是今日且 summary.finishedAt 已落盘视为收尾；
    服务未启动/状态文件缺失时不等待（无东西会随后覆盖）。
    """
    import time as time_module

    def today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    deadline = time_module.monotonic() + deadline_seconds
    while time_module.monotonic() < deadline:
        try:
            state = load_json(BOND_DASHBOARD_DIR / "refresh_state.json", {})
        except Exception:  # noqa: BLE001 - 状态读不了就不等
            return
        if not isinstance(state, dict):
            return
        last_run = str(state.get("lastRun") or "")
        summary = state.get("summary")
        if not last_run.startswith(today()) or (
            isinstance(summary, dict) and summary.get("finishedAt")
        ):
            return
        time_module.sleep(60)


if __name__ == "__main__":
    # 计划任务入口（部署过渡桥，每晚 19:35）：
    #   cd /d D:\hxjy_research && python -m juyuan_update.dashboard_valuations
    # 先刷新聚源回退源（尽力而为，失败沿用最近一次文件），再对 valuations.json 补桥。
    # 看板 Node 服务换上新构建（dm-refresh 自带回退）后，补桥检测到
    # meta.cb_fallback 即自动跳过，无需删除本任务。
    summary: dict[str, Any] = {}
    _wait_for_node_refresh()
    try:
        summary["refresh"] = refresh_dashboard_cb_fallback()
    except Exception as exc:  # noqa: BLE001 - 刷新失败沿用旧回退文件继续补桥
        summary["refresh"] = f"failed: {type(exc).__name__}: {exc}"[:300]
    summary["apply"] = apply_cb_fallback_to_valuations()
    print(summary)
