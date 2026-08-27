import os
from pathlib import Path

# 运态数据路径统一由项目根 paths.py 管理（PORTAL_DATA_ROOT 环境变量定位）
from paths import (
    DATA_DIR,
    SPREAD_DIR,
    STD_DEV_DIR,
    STRATEGY_DIR,
    UPLOADS_DIR,
    BOND_DIR,
)

PROJECT2_BOND_EXCEL = SPREAD_DIR / "bond_list.xlsx"
UNIFIED_EXCEL = UPLOADS_DIR / "unified_credit_data.xlsx"
BOND_STATIC_JSON = DATA_DIR / "bond_static.json"
COUNTERPARTY_LIMITS_JSON = DATA_DIR / "counterparty_limits.json"
UPDATE_SETTINGS_JSON = DATA_DIR / "update_settings.json"
BOND_PICKER_YIELDS_CACHE = BOND_DIR / "oracle_latest_yields_cache.json"
RATING_FACTS_CACHE = BOND_DIR / "rating_facts_cache.json"
SPREAD_HISTORY_CACHE = SPREAD_DIR / "spread_history_cache.json"
SPREAD_JS = SPREAD_DIR / "spread_data.js"
STD_DEV_JS = STD_DEV_DIR / "data" / "spread_data.js"
STD_DEV_CURVES_CACHE = STD_DEV_DIR / "data" / "oracle_latest_curves_cache.json"
STRATEGY_HTML = STRATEGY_DIR / "信用债策略仪表盘.html"
STRATEGY_FUND_PRICES_FROZEN = STRATEGY_DIR / "fund_prices_frozen.json"
STRATEGY_CURVES_CACHE = STRATEGY_DIR / "strategy_curves_cache.json"

DB_USER = os.environ.get("JUYUAN_DB_USER", "finchina")
DB_PASSWORD = os.environ.get("JUYUAN_DB_PASSWORD", "finchina")
DB_DSN = os.environ.get("JUYUAN_DB_DSN", "10.6.60.118:1521/orcl")
ORACLE_CLIENT = os.environ.get("JUYUAN_ORACLE_CLIENT", r"C:\oracle\instantclient_23_0")

START_DATE = os.environ.get("JUYUAN_CURVE_START_DATE", "20220104")
BENCHMARK_NAME_KEYWORD = os.environ.get("JUYUAN_BENCHMARK_KEYWORD", "中长期纯债")
BENCHMARK_CODE = os.environ.get("JUYUAN_BENCHMARK_CODE", "")
UPDATE_LEGACY_PAGES = os.environ.get("JUYUAN_UPDATE_LEGACY_PAGES", "0") == "1"
UPDATE_STD_DEV = os.environ.get("JUYUAN_UPDATE_STD_DEV", "1") == "1"

DATE_LABELS = ["当前", "昨日", "一周前", "一月前", "年初"]

# Curve names are deliberately business-facing. The DB layer resolves them to
# YCURVECODE with an INSTR(YCURVENAME, :name) query on indexed trade dates.
CURVE_DEFS = {
    "国开债": "中债国开债收益率曲线",
    "中短票AAA+": "中债中短期票据收益率曲线(AAA+)",
    "中短票AAA": "中债中短期票据收益率曲线(AAA)",
    "中短票AA+": "中债中短期票据收益率曲线(AA+)",
    "中短票AA": "中债中短期票据收益率曲线(AA)",
    "大行二级资本债": "中债商业银行二级资本债收益率曲线(AAA-)",
    "股份行二级资本债": "中债商业银行二级资本债收益率曲线(AA+)",
}

STRATEGY_CURVE_DEFS = {
    "国开债": CURVE_DEFS["国开债"],
    "中短票AAA": CURVE_DEFS["中短票AAA"],
    "中短票AA+": CURVE_DEFS["中短票AA+"],
    "大行二级资本债": CURVE_DEFS["大行二级资本债"],
    "股份行二级资本债": CURVE_DEFS["股份行二级资本债"],
}

# Optional hard-coded Juyuan curve codes. Fill these after confirming the exact
# CNBD curve codes in your database; empty values use name discovery fallback.
CURVE_CODE_OVERRIDES = {
    "国开债": "269",
    "中短票AAA+": "260",
    "中短票AAA": "214",
    "中短票AA+": "216",
    "中短票AA": "201",
    "大行二级资本债": "428",
    "股份行二级资本债": "429",
}

LONG_END_CURVE = "中短票AAA+"
ONE_MONTH_TENOR = 0.08
SPREAD_MONITOR_TENORS = (0, ONE_MONTH_TENOR, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 30)
STD_DEV_TENORS = (ONE_MONTH_TENOR, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)

STD_DEV_SPREADS = [
    ("中短票AAA-国开", "中短票AAA", "国开债"),
    ("中短票AA+-国开", "中短票AA+", "国开债"),
    ("中短票AA-国开", "中短票AA", "国开债"),
    ("大行二级资本债-国开", "大行二级资本债", "国开债"),
    ("股份行二级资本债-国开", "股份行二级资本债", "国开债"),
]

RATING_TO_CURVE = {
    "AAA+": "中短票AAA",
    "AAA": "中短票AAA",
    "AAA-": "中短票AAA",
    "AA+": "中短票AA+",
    "AA": "中短票AA",
    "AA(2)": "中短票AA",
    "AA-": "中短票AA",
    "A+": "中短票AA",
    "A": "中短票AA",
    "A-": "中短票AA",
}
