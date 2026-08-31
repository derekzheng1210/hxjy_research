"""新老券利差跟踪配置。"""
from __future__ import annotations

import os
from pathlib import Path

from .. import settings

DATA_DIR = settings.DATA_DIR
DB_PATH = Path(os.environ.get("BOND_SWITCH_DB_PATH", DATA_DIR / "bond_switch.db"))

ORACLE_USER = settings.ORACLE_USER
ORACLE_PASSWORD = settings.ORACLE_PASSWORD
ORACLE_DSN = settings.ORACLE_DSN
ORACLE_CLIENT = settings.ORACLE_CLIENT

HISTORY_START = os.environ.get("BOND_SWITCH_HISTORY_START", "20240101")
INCREMENTAL_OVERLAP_DAYS = 10
AUTO_UPDATE_ENABLED = os.environ.get("BOND_SWITCH_AUTO_UPDATE", "1") == "1"
AUTO_UPDATE_TIME = os.environ.get("BOND_SWITCH_AUTO_UPDATE_TIME", "08:00")

REMAINING_MIN_YEARS = 26.0
REMAINING_MAX_YEARS = 30.0
ACTIVE_YIELD_GAP_BP = 1.0
TERTIARY_ISSUE_CUTOFF = "20250901"
TAX_EXEMPT_CODE = "2500002"
