# -*- coding: utf-8 -*-
"""历史趋势聚合（原一级发行看板-历史趋势迁入，2026-09-28）。

数据源：cache.db 的 bond_deviations 表——由门户每日"数据库更新"任务
（build_cache_once，底层聚源 Oracle TQ_BD_BASICINFO/BESTIMATE）增量刷新，
本模块每次请求实时聚合，不做 Oracle 直连，新数据随每日管道自动出现。

区域分布（province_top_year）：bond_deviations 无省份字段，发行人→省份映射由
每日管道 best-effort 从 Oracle 拉取（列名自适应探测，连接失败静默跳过），
落 issuer_provinces.json；映射缺失/过旧时接口正常返回、区域块置空，
前端降级提示，不影响其余五块。
"""
from __future__ import annotations

import json
import math
import os
import threading
from collections import defaultdict
from datetime import datetime

from .db_utils import get_connection

# ---------- 口径常量 ----------

BOND_TYPE_LABELS = {
    "ordinary": "普通信用债",
    "perpetual": "永续债",
    "tier2": "二级资本债",
    "broker_subordinated": "商行次级债",
    "tlac": "TLAC非资本债",
}
TYPE_KEYS = list(BOND_TYPE_LABELS.values()) + ["其他"]

TERM_KEYS = ["<1Y", "1-3Y", "3-5Y", "5-10Y", ">=10Y", "未知"]

# 分期限月均票面的目标期限（±0.5 年容差，effective_term 为行权前年数）
YIELD_TERM_TARGETS = (("t3", 3.0), ("t5", 5.0), ("t10", 10.0), ("t15", 15.0), ("t30", 30.0))
YIELD_TERM_TOL = 0.5

PROVINCE_FILENAME = "issuer_provinces.json"


def _province_file_path():
    from paths import PRIMARY_PRICING_CACHE

    return os.path.join(os.path.dirname(str(PRIMARY_PRICING_CACHE)), PROVINCE_FILENAME)


# ---------- 聚合 ----------

def _term_bucket(term):
    if term is None:
        return "未知"
    if term < 1:
        return "<1Y"
    if term < 3:
        return "1-3Y"
    if term < 5:
        return "3-5Y"
    if term < 10:
        return "5-10Y"
    return ">=10Y"


def _type_label(bond_type):
    return BOND_TYPE_LABELS.get(bond_type, "其他")


def _ym(issue_date):
    return issue_date[:6] if issue_date and len(issue_date) >= 6 else None


def build_trends(conn, start_ymd: str, end_ymd: str, province_map: dict | None = None) -> dict:
    """从 bond_deviations 聚合历史趋势六块数据（返回可直接 jsonify 的结构）。"""
    rows = conn.execute(
        """
        SELECT issue_date, coupon_rate, effective_term, bond_type, issue_amount_wan, issuer
        FROM bond_deviations
        WHERE issue_date >= ? AND issue_date <= ?
        """,
        (start_ymd, end_ymd),
    ).fetchall()

    monthly_acc = defaultdict(lambda: [0, 0.0, [0.0, 0]])          # ym -> [cnt, plan亿, coupon和/数]
    type_acc = defaultdict(float)                                    # (ym, type) -> plan亿
    term_acc = defaultdict(float)                                    # (ym, bucket) -> plan亿
    yield_term_acc = defaultdict(lambda: [0.0, 0])                   # (ym, tkey) -> coupon和/数
    year_coupons = defaultdict(list)                                 # year -> [coupon]
    province_acc = defaultdict(lambda: [0, 0.0])                     # (year, province) -> [cnt, plan亿]

    for issue_date, coupon, term, bond_type, amount_wan, issuer in rows:
        ym = _ym(issue_date)
        if not ym:
            continue
        year = ym[:4]
        plan_yi = (amount_wan or 0) / 10000.0
        monthly_acc[ym][0] += 1
        monthly_acc[ym][1] += plan_yi
        type_acc[(ym, _type_label(bond_type))] += plan_yi
        term_acc[(ym, _term_bucket(term))] += plan_yi
        if coupon is not None:
            monthly_acc[ym][2][0] += coupon
            monthly_acc[ym][2][1] += 1
            year_coupons[year].append(coupon)
            for tkey, target in YIELD_TERM_TARGETS:
                if term is not None and abs(term - target) <= YIELD_TERM_TOL:
                    acc = yield_term_acc[(ym, tkey)]
                    acc[0] += coupon
                    acc[1] += 1
                    break
        if province_map and issuer:
            prov = province_map.get(issuer)
            if prov:
                province_acc[(year, prov)][0] += 1
                province_acc[(year, prov)][1] += plan_yi

    months = sorted(monthly_acc)
    years = sorted(year_coupons)

    monthly = [
        {
            "ym": f"{ym[:4]}-{ym[4:]}",
            "cnt": monthly_acc[ym][0],
            "plan": round(monthly_acc[ym][1], 2),
            "yield_mean": round(monthly_acc[ym][2][0] / monthly_acc[ym][2][1], 4)
            if monthly_acc[ym][2][1]
            else None,
        }
        for ym in months
    ]

    yield_term_months = sorted({ym for ym, _ in yield_term_acc})
    monthly_yield_term = [
        {
            "ym": f"{ym[:4]}-{ym[4:]}",
            **{
                tkey: round(acc[0] / acc[1], 4) if (acc := yield_term_acc.get((ym, tkey))) and acc[1] else None
                for tkey, _ in YIELD_TERM_TARGETS
            },
        }
        for ym in yield_term_months
    ]

    type_monthly = [
        {"ym": f"{ym[:4]}-{ym[4:]}", **{k: round(type_acc.get((ym, k), 0.0), 2) for k in TYPE_KEYS}}
        for ym in months
    ]
    type_year_total = [
        {"year": yr, **{k: round(sum(v for (ym, t), v in type_acc.items() if t == k and ym[:4] == yr), 2) for k in TYPE_KEYS}}
        for yr in years
    ]

    present_term_keys = [k for k in TERM_KEYS if any(t == k for (_, t) in term_acc)]
    term_monthly = [
        {"ym": f"{ym[:4]}-{ym[4:]}", **{k: round(term_acc.get((ym, k), 0.0), 2) for k in present_term_keys}}
        for ym in months
    ]

    yield_hist = []
    for yr in years:
        ys = year_coupons[yr]
        if not ys:
            continue
        n = len(ys)
        mean = sum(ys) / n
        sd = math.sqrt(sum((y - mean) ** 2 for y in ys) / n)
        bins = defaultdict(int)
        for y in ys:
            bins[round(y, 1)] += 1
        yield_hist.append(
            {
                "year": yr,
                "n": n,
                "mean": round(mean, 6),
                "sd": round(sd, 6),
                "min": round(min(ys), 4),
                "max": round(max(ys), 4),
                "bins": [{"ybin": round(b, 1), "cnt": c} for b, c in sorted(bins.items())],
            }
        )

    province_top_year = []
    if province_map:
        for yr in years:
            items = [
                {"province_name": p, "cnt": c, "plan": round(v, 2)}
                for (y, p), (c, v) in sorted(province_acc.items(), key=lambda kv: -kv[1][1])
                if y == yr
            ][:12]
            if items:
                province_top_year.append({"year": yr, "items": items})

    return {
        "meta": {
            "source": "聚源Oracle·偏离统计缓存",
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "range": f"{months[0][:4]}.{months[0][4:]} ~ {months[-1][:4]}.{months[-1][4:]}" if months else "",
            "years": years,
            "province_generated": (province_map or {}).get("_generated_at", ""),
            "total": len(rows),
        },
        "monthly": monthly,
        "monthly_yield_term": monthly_yield_term,
        "type_monthly": type_monthly,
        "type_keys": TYPE_KEYS,
        "type_year_total": type_year_total,
        "term_monthly": term_monthly,
        "term_keys": present_term_keys,
        "yield_hist": yield_hist,
        "province_top_year": province_top_year,
    }


# ---------- 发行人省份映射（best-effort，每日管道刷新） ----------

# 省份来源：公司主表优先。经 2026-09-28 探测，省份列在 TQ_FMAT_COMPINFO
# （COMPNAME→PROVINCENAME，5711 家发行人命中率 99.8%），而非债券表 TQ_BD_*。
_PROVINCE_SOURCE_PRIORITY = ("TQ_FMAT_COMPINFO", "TQ_FMC_BASICINFO", "TQ_SK_BASICINFO")
_PROVINCE_NAME_COLS = ("COMPNAME", "ISSUER", "COMPANYNAME")


def _discover_province_source(cur):
    """从数据字典发现 (表, 名称列, 省份列)；找不到返回 None。"""
    cur.execute("SELECT DISTINCT table_name FROM user_tab_columns WHERE column_name = 'PROVINCENAME'")
    prov_tables = {r[0] for r in cur.fetchall()}
    if not prov_tables:
        return None
    placeholders = ",".join(f":n{i}" for i in range(len(_PROVINCE_NAME_COLS)))
    cur.execute(
        "SELECT table_name, column_name FROM user_tab_columns "
        f"WHERE column_name IN ({placeholders})",
        {f"n{i}": c for i, c in enumerate(_PROVINCE_NAME_COLS)},
    )
    name_cols: dict[str, str] = {}
    for table, col in cur.fetchall():
        if table in prov_tables and table not in name_cols:
            name_cols[table] = col
    for table in _PROVINCE_SOURCE_PRIORITY:
        if table in name_cols:
            return table, name_cols[table], "PROVINCENAME"
    return None


def load_province_map() -> dict | None:
    """读取本地省份映射文件；不存在返回 None（区域块降级隐藏）。"""
    path = _province_file_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        provinces = payload.get("provinces")
        if not isinstance(provinces, dict) or not provinces:
            return None
        provinces["_generated_at"] = payload.get("generated_at", "")
        return provinces
    except Exception:
        return None


def refresh_provinces(issuers, batch_size: int = 400) -> tuple[bool, str]:
    """从 Oracle 拉发行人→省份映射并落盘。失败返回 (False, 原因)，不抛异常。"""
    issuers = [i for i in dict.fromkeys(issuers) if i]
    if not issuers:
        return False, "无发行人清单"
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            source = _discover_province_source(cur)
            if not source:
                return False, "数据字典未命中省份字段"
            table, name_col, prov_col = source

            wanted = set(issuers)
            mapping: dict[str, str] = {}
            for batch_start in range(0, len(issuers), batch_size):
                batch = issuers[batch_start : batch_start + batch_size]
                placeholders = ",".join(f":b{i}" for i in range(len(batch)))
                binds = {f"b{i}": name for i, name in enumerate(batch)}
                cur.execute(
                    f"SELECT DISTINCT {name_col}, {prov_col} FROM {table} "
                    f"WHERE {name_col} IN ({placeholders}) AND {prov_col} IS NOT NULL",
                    binds,
                )
                for issuer, prov in cur.fetchall():
                    prov = (prov or "").strip()
                    if prov and issuer in wanted:
                        mapping[issuer] = prov

        if not mapping:
            return False, f"{table}.{name_col} 未匹配到发行人"
        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": f"Oracle {table}.{prov_col}",
            "provinces": mapping,
        }
        path = _province_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True, f"{len(mapping)}/{len(issuers)} 家发行人已映射（{table}.{prov_col}）"
    except Exception as exc:  # Oracle 不可达/字段不存在等一律降级
        return False, f"Oracle 拉取失败：{exc}"


# 请求期兜底：映射文件缺失时后台补拉一次（当日只试一次，不阻塞响应）
_province_retry_lock = threading.Lock()
_province_retry_date: str = ""


def maybe_background_refresh_provinces(issuers) -> None:
    global _province_retry_date
    today = datetime.now().strftime("%Y%m%d")
    with _province_retry_lock:
        if _province_retry_date == today:
            return
        _province_retry_date = today
    threading.Thread(
        target=refresh_provinces, args=(issuers,), daemon=True, name="trends-province-refresh"
    ).start()
