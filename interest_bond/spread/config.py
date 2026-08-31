"""利差跟踪模块配置:曲线与利差定义、更新策略。路径与Oracle连接见根目录settings.py。"""
from __future__ import annotations

import os
from pathlib import Path

from .. import settings

DATA_DIR = settings.DATA_DIR
DB_PATH = Path(os.environ.get("SPREAD_DB_PATH", DATA_DIR / "spreads.db"))
LOG_DIR = settings.LOG_DIR
UPDATE_LOCK_PATH = DATA_DIR / "spread_update.lock"

ORACLE_USER = settings.ORACLE_USER
ORACLE_PASSWORD = settings.ORACLE_PASSWORD
ORACLE_DSN = settings.ORACLE_DSN
ORACLE_CLIENT = settings.ORACLE_CLIENT

# ---- 收益率曲线定义(探查结论,见README) ----
# 表: TQ_QT_YIELDCURVE (TRADEDATE, YCURVECODE, YCURVETYPE, MATURITY, YIELD, ISVALID)
CURVES = {
    # curve_id: (YCURVECODE, YCURVETYPE, 名称, 采集期限)
    "treasury": {
        "code": "203",
        "type": "1",
        "name": "中债国债收益率曲线",
        "tenors": (10.0, 30.0, 50.0),
    },
    "local": {
        "code": "479",
        "type": "1",
        "name": "财政部-中国地方政府债券收益率曲线",
        "tenors": (10.0, 30.0),
    },
}

# ---- 利差定义:spread = long - short,单位bp ----
SPREADS = [
    {
        "key": "tsy30_tsy10",
        "name": "国债30Y-10Y",
        "long": ("treasury", 30.0),
        "short": ("treasury", 10.0),
        "color": "#4361ee",
        "desc": "30年与10年国债收益率之差,超长端期限利差。",
    },
    {
        "key": "tsy50_tsy30",
        "name": "国债50Y-30Y",
        "long": ("treasury", 50.0),
        "short": ("treasury", 30.0),
        "color": "#f59e0b",
        "desc": "50年与30年国债收益率之差,超长久期补偿。",
    },
    {
        "key": "lgb30_tsy10",
        "name": "30Y地方债-10Y国债",
        "long": ("local", 30.0),
        "short": ("treasury", 10.0),
        "color": "#10b981",
        "desc": "30年地方债与10年国债收益率之差,含期限与品种溢价。",
    },
    {
        "key": "lgb30_tsy30",
        "name": "30Y地方债-30Y国债",
        "long": ("local", 30.0),
        "short": ("treasury", 30.0),
        "color": "#e63946",
        "desc": "同期限地方债对国债的品种利差。",
    },
]

# ---- 历史与更新 ----
HISTORY_START = "20150101"          # 全量回补起点;地方债曲线2022年前后才开始有数据
INCREMENTAL_OVERLAP_DAYS = 10       # 增量更新回看重叠天数,吸收源数据修订
FETCH_CHUNK_DAYS = 180              # Oracle按区间分块拉取,避免单次大扫描
AUTO_UPDATE_ENABLED = os.environ.get("SPREAD_AUTO_UPDATE", "1") == "1"
AUTO_UPDATE_TIME = os.environ.get("SPREAD_AUTO_UPDATE_TIME", "07:30")
