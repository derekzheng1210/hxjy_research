# -*- coding: utf-8 -*-
"""jgxw_app_plus 配置

数据策略：
- 信用利差 / 信用收益率：只读引用 juyuan_credit_tools_portal 现有数据文件
  （由 portal 的 juyuan_update 管道每日更新，本项目零管道、零新增历史读取）
- 国债 / 国开债 / 地方债收益率（含 15/20/30Y 长端）：本项目 data/rate_curves_cache.json，
  由 tools/update_rate_curves.py 维护（复用 portal 的 juyuan_update.db 数据访问层）
"""
import os
from pathlib import Path

# 运态数据路径统一由项目根 paths.py 管理（PORTAL_DATA_ROOT 环境变量定位）
from paths import DATA_DIR, STRATEGY_DIR, STD_DEV_DIR, INSTITUTION_FLOW_DIR

PORTAL_DATA_DIR = DATA_DIR

# portal 现有数据文件（只读）
STRATEGY_CURVES_CACHE = STRATEGY_DIR / "strategy_curves_cache.json"
STD_DEV_CURVES_CACHE = STD_DEV_DIR / "data" / "oracle_latest_curves_cache.json"
STD_DEV_SPREAD_JS = STD_DEV_DIR / "data" / "spread_data.js"

# 本项目自有利率曲线缓存（tools/update_rate_curves.py 生成与增量更新，含国债/国开债/地方债）
RATE_CURVES_CACHE = INSTITUTION_FLOW_DIR / "rate_curves_cache.json"

# 利率曲线（自有缓存提供，含长端）
RATE_CURVES = ["国债", "国开债", "地方债"]
RATE_TENOR_LABELS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]

# 信用曲线（portal 缓存提供）
CREDIT_CURVES = ["中短票AAA", "中短票AA+", "中短票AA", "大行二级资本债", "股份行二级资本债"]
CREDIT_TENOR_LABELS = ["1M", "1Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y", "9Y", "10Y"]

# 信用曲线 -> 对应利差品种（两倍标准差模块口径，基准均为国开债）
CURVE_TO_SPREAD = {
    "中短票AAA": "中短票AAA-国开",
    "中短票AA+": "中短票AA+-国开",
    "中短票AA": "中短票AA-国开",
    "大行二级资本债": "大行二级资本债-国开",
    "股份行二级资本债": "股份行二级资本债-国开",
}

# portal strategy 缓存曲线名 -> 统一对外名
STRATEGY_NAME_MAP = {
    "政金债": "国开债",
    "AAA信用债": "中短票AAA",
    "AA+信用债": "中短票AA+",
    "大行二级资本债": "大行二级资本债",
    "股份行二级资本债": "股份行二级资本债",
}


def tenor_label(t) -> str:
    """浮点期限 -> 展示标签（0.08 -> 1M, 1 -> 1Y）"""
    t = float(t)
    if abs(t - 0.08) < 1e-9:
        return "1M"
    return f"{int(t)}Y" if t.is_integer() else f"{t:g}Y"
