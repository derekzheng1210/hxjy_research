"""利率债模块共享配置，运行数据统一落在门户数据根目录。"""

from __future__ import annotations

import os
from pathlib import Path

from paths import DATA_ROOT

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BOND_MONITOR_DATA_DIR", DATA_ROOT / "interest_bond"))
LOG_DIR = Path(os.environ.get("BOND_MONITOR_LOG_DIR", DATA_ROOT / "logs" / "interest_bond"))

ORACLE_USER = os.environ.get("JUYUAN_DB_USER", "finchina")
ORACLE_PASSWORD = os.environ.get("JUYUAN_DB_PASSWORD", "finchina")
ORACLE_DSN = os.environ.get("JUYUAN_DB_DSN", "10.6.60.118:1521/orcl")
ORACLE_CLIENT = os.environ.get("JUYUAN_ORACLE_CLIENT", r"C:\oracle\instantclient_23_0")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
