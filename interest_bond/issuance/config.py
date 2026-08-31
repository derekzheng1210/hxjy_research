"""发行跟踪模块配置:品种口径、限额、历史快照。路径与Oracle连接见根目录settings.py。"""
from __future__ import annotations

import os
from pathlib import Path

from .. import settings
from paths import CONFIG_DIR

DATA_DIR = settings.DATA_DIR
DB_PATH = Path(os.environ.get("TRACKER_DB_PATH", DATA_DIR / "tracker.db"))
UPDATE_LOCK_PATH = DATA_DIR / "tracker_update.lock"

# 限额工作簿默认在平台根目录的上一级(与原 government_bond_issuance_tracker 一致)
SOURCE_ROOT = Path(os.environ.get("TRACKER_SOURCE_ROOT", CONFIG_DIR))
LIMIT_WORKBOOK = Path(
    os.environ.get(
        "TRACKER_LIMIT_WORKBOOK",
        SOURCE_ROOT / "副本2021年以来国债、地方债发行限额(1).xlsx",
    )
)

ORACLE_USER = settings.ORACLE_USER
ORACLE_PASSWORD = settings.ORACLE_PASSWORD
ORACLE_DSN = settings.ORACLE_DSN
ORACLE_CLIENT = settings.ORACLE_CLIENT

AUTO_UPDATE_TIME = os.environ.get("TRACKER_AUTO_UPDATE_TIME", "07:30")
AUTO_UPDATE_ENABLED = os.environ.get("TRACKER_AUTO_UPDATE", "1") == "1"
HISTORY_START_YEAR = 2021
INCREMENTAL_LOOKBACK_DAYS = int(os.environ.get("TRACKER_LOOKBACK_DAYS", "14"))

CATEGORIES = (
    "一般国债",
    "地方新增一般债",
    "特别国债",
    "地方新增专项债",
    "地方特殊再融资债",
)

CATEGORY_NOTES = {
    "一般国债": "普通国债发行额减普通国债到期额，采用净增加口径。",
    "地方新增一般债": "全称含‘一般债券’且不含‘再融资’，按实际发行额累计。",
    "特别国债": "全称含‘特别国债’，按发行事件累计，不扣除到期。",
    "地方新增专项债": "全称含‘专项债券’且不含‘再融资’，按实际发行额累计。",
    "地方特殊再融资债": "再融资地方债中，用途命中偿还存量债务或置换存量隐性债务等表述。",
}

# 2021-2025年历史结果已确认，不再受Oracle源字段修订影响。
HISTORICAL_SNAPSHOTS = {
    2021: {
        "一般国债": 23200.4251,
        "地方新增一般债": 7832.4560,
        "特别国债": 0.0,
        "地方新增专项债": 35804.6350,
        "地方特殊再融资债": 8738.6308,
    },
    2022: {
        "一般国债": 27611.2692,
        "地方新增一般债": 7182.1140,
        "特别国债": 0.0,
        "地方新增专项债": 40264.1477,
        "地方特殊再融资债": 2199.5827,
    },
    2023: {
        "一般国债": 41079.8445,
        "地方新增一般债": 7006.6573,
        "特别国债": 0.0,
        "地方新增专项债": 39443.9604,
        "地方特殊再融资债": 14182.8668,
    },
    2024: {
        "一般国债": 34687.9922,
        "地方新增一般债": 6986.1614,
        "特别国债": 10000.0,
        "地方新增专项债": 40032.3196,
        "地方特殊再融资债": 25272.8235,
    },
    2025: {
        "一般国债": 49521.3817,
        "地方新增一般债": 7700.2129,
        "特别国债": 18000.0,
        "地方新增专项债": 45916.6799,
        "地方特殊再融资债": 23085.0,
    },
}

FALLBACK_LIMITS = {
    2021: (27500, 8200, 0, 36500, 8170.8389),
    2022: (26500, 7200, 0, 36500, 2199.5827),
    2023: (41600, 7200, 0, 38000, 13885),
    2024: (33400, 7200, 10000, 39000, 25018.1127),
    2025: (48600, 8000, 18000, 44000, 25000),
    2026: (50900, 8000, 16000, 44000, 20000),
}
