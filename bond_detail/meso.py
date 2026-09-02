# -*- coding: utf-8 -*-
"""债券详查——中观研究数据补充。

把项目内两个中观研究数据源接到 AI 信用研究的"行业关键数据"卡上方：
1. 行业景气度跟踪页面（industry_prosperity/行业景气度跟踪.html）内嵌的
   发行人→申万一/二级行业映射，用于确定主体行业（纯名称匹配，不依赖 AI）；
2. 行业景气高频跟踪（ipm_tracker）的 Wind 指标缓存（Excel 指标表 + 
   cache/indicators_data.json），用于取行业的价格/产量等高频最新值。

任何数据源缺失或解析失败都静默降级（返回 None / 空列表），不影响主流程。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from paths import DATA_DIR

PROSPERITY_HTML = DATA_DIR / "industry_prosperity" / "行业景气度跟踪.html"
IPM_EXCEL = Path(__file__).resolve().parent.parent / "ipm_tracker" / "指标对应表.xlsx"
IPM_CACHE = DATA_DIR / "ipm_tracker" / "cache" / "indicators_data.json"

MAX_INDICATORS = 12
TREND_DAYS = 30

# 指标级相关性规则：指标名包含任一关键词 → 适用 (申万一级, 申万二级集合)。
# 只有主体二级行业命中才展示该指标；没有可匹配指标的行业（白酒、软饮料、银行、
# 综合等）不强行展示。规则按顺序匹配，首个命中生效。
INDICATOR_RULES: list[tuple[tuple[str, ...], str, frozenset[str] | None]] = [
    # 农林牧渔：生猪链 → 养殖/饲料
    (("生猪",), "农林牧渔", frozenset({"畜牧业"})),
    # 食品饮料：大豆→调味品（酱油成本端）；乳制品/奶粉→乳品；白酒/软饮料/其他食品无对应指标不展示
    (("大豆",), "食品饮料", frozenset({"调味品"})),
    (("乳制品", "奶粉"), "食品饮料", frozenset({"乳品及冷冻食品"})),
    # 家用电器：白电产销
    (("空调", "冰箱", "洗衣机"), "家用电器", frozenset({"白色家电"})),
    # 社会服务
    (("免税",), "社会服务", frozenset({"旅游及景区"})),
    (("航班", "餐饮"), "社会服务", frozenset({"酒店", "旅游及景区"})),
    # 传媒：观影/票房 → 影视院线
    (("观影", "票房"), "传媒", frozenset({"影视院线"})),
    # 房地产：开发全链条
    (("土地溢价率", "房屋销售价格", "商品房", "房屋施工", "房屋新开工", "房屋竣工",
      "开发投资", "开发资金", "住房贷款", "个人按揭"), "房地产", frozenset({"房地产开发"})),
    # 非银金融：交易量直接驱动券商营收（保险/多元金融不匹配）
    (("换手率", "万得全A"), "非银金融", frozenset({"证券"})),
    # 电子
    (("半导体", "费城半导体"), "电子", frozenset({"半导体"})),
    (("PCB",), "电子", frozenset({"半导体", "元件"})),
    (("光电子器件", "二极管"), "电子", frozenset({"元件"})),
    # 通信
    (("基站", "集成电路"), "通信", frozenset({"通信设备"})),
    # 计算机
    (("信骅", "软件业务"), "计算机", None),
    # 汽车
    (("乘用车",), "汽车", frozenset({"乘用车", "汽车零部件"})),
    # 电力设备：动力电池（光伏/风电价格指数无对应设备类发行人，不展示）
    (("动力电池",), "电力设备", frozenset({"电池"})),
    # 机械设备
    (("挖掘机",), "机械设备", frozenset({"专用设备"})),
    (("工业机器人",), "机械设备", frozenset({"通用设备"})),
    # 国防军工：造船
    (("造船价格", "手持订单"), "国防军工", frozenset({"船舶"})),
    # 轻工制造：家居景气
    (("建材家居", "BHI"), "轻工制造", frozenset({"家居用品"})),
    # 周期
    (("动力煤", "原煤产量"), "煤炭", None),
    (("螺纹钢",), "钢铁", None),
    (("LME铜", ":铝"), "有色金属", frozenset({"工业金属"})),
    (("水泥",), "建筑材料", frozenset({"水泥"})),
    (("布伦特",), "石油石化", None),
    # 交通运输
    (("波罗的海", "集装箱运价", "BDI", "TDI", "SCFI"), "交通运输", frozenset({"航运"})),
    (("快递",), "交通运输", frozenset({"物流"})),
    (("铁路客运",), "交通运输", frozenset({"铁路"})),
    (("民航客运",), "交通运输", frozenset({"航空"})),
    (("公路客运",), "交通运输", frozenset({"公路", "公交"})),
]

_cache_lock = threading.Lock()
_industry_cache: dict[str, tuple[str, str]] | None = None
_meso_table_cache: list[dict[str, Any]] | None = None
_series_cache: dict[str, Any] = {"mtime": -1.0, "data": {}, "updated": ""}


def _load_issuer_industries() -> dict[str, tuple[str, str]]:
    """从行业景气度跟踪.html 提取发行人→(申万一级, 申万二级)映射（进程内缓存）。"""
    global _industry_cache
    with _cache_lock:
        if _industry_cache is not None:
            return _industry_cache
    mapping: dict[str, tuple[str, str]] = {}
    try:
        raw = PROSPERITY_HTML.read_text(encoding="utf-8", errors="replace")
        pattern = re.compile(r'\{"name":"([^"]+)","sw_l1":"([^"]*)","sw_l2":"([^"]*)"')
        for name, l1, l2 in pattern.findall(raw):
            mapping.setdefault(name.strip(), (l1.strip(), l2.strip()))
    except OSError:
        pass
    with _cache_lock:
        _industry_cache = mapping
    return mapping


def _load_meso_table() -> list[dict[str, Any]]:
    """读取指标对应表（一级/二级/名称/ID），返回 [{l1,l2,name,sid}]（进程内缓存）。"""
    global _meso_table_cache
    with _cache_lock:
        if _meso_table_cache is not None:
            return _meso_table_cache
    rows: list[dict[str, Any]] = []
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(IPM_EXCEL, read_only=True)
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                if not row or len(row) < 4 or not row[0] or str(row[0]).strip() in ("一级", ""):
                    continue
                sid = str(row[3] or "").strip()
                if not sid:
                    continue
                rows.append({
                    "l1": str(row[0]).strip(), "l2": str(row[1]).strip(),
                    "name": str(row[2]).strip(), "sid": sid,
                })
        workbook.close()
    except Exception:  # noqa: BLE001 - Excel 缺失时静默降级
        rows = []
    with _cache_lock:
        _meso_table_cache = rows
    return rows


def _unit_for(sid: str) -> str:
    try:
        from ipm_tracker.routes import UNIT_MAP
        return str(UNIT_MAP.get(sid, "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _load_cache_payload() -> dict[str, Any]:
    """解析指标数据缓存（按文件 mtime 缓存解析结果，避免每次 GET 重复解析 674KB）。"""
    try:
        mtime = IPM_CACHE.stat().st_mtime
    except OSError:
        return {}
    with _cache_lock:
        if _series_cache["mtime"] == mtime:
            return _series_cache["data"]
    try:
        payload = json.loads(IPM_CACHE.read_text(encoding="utf-8"))
        indicators = payload.get("indicators") or {}
        data = {}
        for sid, entry in indicators.items():
            times, values = entry.get("times"), entry.get("values")
            if isinstance(times, str):
                times = json.loads(times.replace("'", '"'))
            if isinstance(values, str):
                values = json.loads(values.replace("'", '"'))
            if isinstance(times, list) and isinstance(values, list):
                clean = []
                for value in values:
                    try:
                        clean.append(float(value))
                    except (TypeError, ValueError):
                        clean.append(float("nan"))
                data[sid] = ([str(t) for t in times], clean)
        with _cache_lock:
            _series_cache.update(mtime=mtime, data=data, updated=str(payload.get("updated") or ""))
        return data
    except Exception:  # noqa: BLE001
        return {}


def _load_series(sid: str) -> tuple[list[str], list[float]]:
    series = _load_cache_payload().get(sid)
    return series if series else ([], [])


def issuer_sw_industry(issuer: str) -> tuple[str, str] | None:
    """发行人名称 → 申万行业；先精确匹配，再按包含关系匹配（长名/短名兼容）。"""
    issuer = str(issuer or "").strip()
    if not issuer:
        return None
    mapping = _load_issuer_industries()
    if not mapping:
        return None
    if issuer in mapping:
        return mapping[issuer]
    for name, (l1, l2) in mapping.items():
        if issuer in name or name in issuer:
            return l1, l2
    return None


def _rule_for(indicator_name: str) -> tuple[str, frozenset[str] | None] | None:
    """指标名 → (适用申万一级, 申万二级集合或None=整组)。"""
    for keywords, sw_l1, sw_l2 in INDICATOR_RULES:
        if any(keyword in indicator_name for keyword in keywords):
            return sw_l1, sw_l2
    return None


def meso_supplement(issuer: str) -> dict[str, Any] | None:
    """组装主体的中观高频跟踪数据。

    只有存在与主体申万二级行业真正相关的高频指标时才返回；
    白酒、软饮料、银行、综合等无对应指标的行业返回 None（不强行匹配）。
    """
    sw = issuer_sw_industry(issuer)
    if not sw:
        return None
    sw_l1, sw_l2 = sw
    table = _load_meso_table()
    if not table:
        return None
    # Excel 内存在重复行，按 一级/二级/名称 去重
    seen: set[tuple[str, str, str]] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in table:
        key = (row["l1"], row["l2"], row["name"])
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)

    matched_rows: list[dict[str, Any]] = []
    for row in unique_rows:
        rule = _rule_for(row["name"])
        if not rule:
            continue
        rule_l1, rule_l2 = rule
        if sw_l1 != rule_l1:
            continue
        if rule_l2 is not None and sw_l2 not in rule_l2:
            continue
        matched_rows.append(row)
    if not matched_rows:
        return None
    matched_rows = matched_rows[:MAX_INDICATORS]

    indicators: list[dict[str, Any]] = []
    for row in matched_rows:
        times, values = _load_series(row["sid"])
        points = [(t, v) for t, v in zip(times, values) if v == v]  # 剔除 NaN
        if not points:
            continue
        latest_t, latest = points[-1]
        previous_t, previous = points[-2] if len(points) >= 2 else (latest_t, latest)
        # 近30天变化率：与30天前最近的观测点比较
        base = None
        for t, v in points:
            if latest_t >= t and _days_between(t, latest_t) >= TREND_DAYS:
                base = v
            elif _days_between(t, latest_t) < TREND_DAYS:
                break
        change_pct = None
        # 同比/环比/指数等“率”类指标不做30天变化率（变化率的变化率无意义）
        is_ratio = (row["name"].find("同比") >= 0 or row["name"].find("环比") >= 0
                    or row["name"].find("指数") >= 0 or row["name"].find("率") >= 0
                    or _unit_for(row["sid"]).strip() in ("%", "%，", "百分比"))
        if base not in (None, 0) and not is_ratio:
            change_pct = round((latest - base) / abs(base) * 100, 2)
        indicators.append({
            "name": row["name"],
            "category": f"{row['l1']}/{row['l2']}",
            "unit": _unit_for(row["sid"]),
            "latest": round(latest, 4),
            "latest_date": latest_t,
            "previous": round(previous, 4),
            "previous_date": previous_t,
            "change": round(latest - previous, 4),
            "change_pct_30d": change_pct,
        })

    if not indicators:
        return None
    updated = ""
    try:
        updated = str(_series_cache.get("updated") or "")
    except Exception:  # noqa: BLE001
        pass
    return {
        "issuer": issuer,
        "sw_l1": sw_l1,
        "sw_l2": sw_l2,
        "updated": updated,
        "indicators": indicators,
    }


def _days_between(start: str, end: str) -> int:
    from datetime import datetime

    try:
        a = datetime.strptime(str(start)[:10], "%Y-%m-%d")
        b = datetime.strptime(str(end)[:10], "%Y-%m-%d")
        return (b - a).days
    except ValueError:
        return 0
