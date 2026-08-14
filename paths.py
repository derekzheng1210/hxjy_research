"""集中数据路径定义。

运行态数据外置于独立目录，由环境变量 ``PORTAL_DATA_ROOT`` 指定，
未设置时默认回退到项目同级 ``../juyuan_credit_data``。

通过 ``.env`` 文件（python-dotenv，在 app.py 顶部 load_dotenv）或
系统环境变量设置 ``PORTAL_DATA_ROOT``。迁移到新机器时只需调整该变量。
"""
import os
from pathlib import Path

# 项目根目录（代码所在）
BASE_DIR = Path(__file__).resolve().parent

# 运态数据根目录：环境变量优先，否则用项目同级 juyuan_credit_data
DATA_ROOT = Path(os.environ.get("PORTAL_DATA_ROOT", BASE_DIR.parent / "juyuan_credit_data"))

# 各业务数据子目录（原 data/ 结构）
DATA_DIR = DATA_ROOT / "data"
BOND_DIR = DATA_DIR / "bond_picker"
STRATEGY_DIR = DATA_DIR / "strategy_dashboard"
SPREAD_DIR = DATA_DIR / "spread_monitor"
INDUSTRY_DIR = DATA_DIR / "industry_prosperity"
STD_DEV_DIR = DATA_DIR / "credit_std_dev"
INSTITUTION_FLOW_DIR = DATA_DIR / "institution_flow"
PRIMARY_PRICING_CACHE = DATA_DIR / "primary_market_pricing" / "cache.db"

# 用户上传与日志
UPLOADS_DIR = DATA_ROOT / "uploads"
LOGS_DIR = DATA_ROOT / "logs"

# 一级发行定价底稿输出目录
WORKPAPER_DIR = DATA_ROOT / "primary_market_pricing"

# 源码资产，保留在项目内（随仓库分发）
CONFIG_DIR = BASE_DIR / "config"
