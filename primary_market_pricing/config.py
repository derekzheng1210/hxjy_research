"""
一级发行非市场化评估系统 - 配置文件
"""

import os

# ============ Oracle 数据库连接 ============
ORACLE_CLIENT_DIR = r"C:\oracle\instantclient_23_0"
DB_USER = os.environ.get("DB_USER", "finchina")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "finchina")
DB_DSN = os.environ.get("DB_DSN", "10.6.60.118:1521/orcl")

# ============ 评级曲线代码映射 ============
# 中债中短期票据收益率曲线（隐含评级→曲线代码）
# 评级来源：优先使用 BESTIMATE.IMPLIEDRATING（中债隐含评级），
# 不可用时回退到 TQ_BD_NEWESTBASICINFO.NEWISSUERATE（发行时评级）。
# 参考 juyuan_credit_tools_portal 项目的 RATING_TO_CURVE 映射逻辑。
RATING_CURVE_CODES = {
    "AAA+": "260",   # 中债中短期票据收益率曲线(AAA+)
    "AAA":  "214",   # 中债中短期票据收益率曲线(AAA)
    "AAA-": "214",   # 同AAA
    "AA+":  "216",   # 中债中短期票据收益率曲线(AA+)
    "AA":   "201",   # 中债中短期票据收益率曲线(AA)
    "AA(2)":"201",   # 同AA（隐含评级特有等级）
    "AA-":  "201",   # 同AA
    "A+":   "201",   # 同AA
    "A":    "201",   # 同AA
    "A-":   "201",   # 同AA
}

# 曲线可用的关键期限点（年）
CURVE_TENORS = [0.08, 0.25, 0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 30]

# ============ 偏离度阈值 ============
DEVIATION_THRESHOLD_BP = 3  # 偏离阈值：3BP，低于合理价格3BP以上判定为非市场化

# 交易所科创债规则：名称含 K 且交易场所为上交所/深交所。
# EXCHANGE 为财汇 TQ_BD_NEWESTBASICINFO 的交易场所代码。
EXCHANGE_BOND_EXCHANGES = ("001002", "001003", "001018")
KECHUANG_NEARBY_TERM_YEARS = 2.0
KECHUANG_FALLBACK_FAIR_PRICE_ADJUSTMENT = -0.05  # 5BP，收益率单位为百分比
ORDINARY_KECHUANG_FALLBACK_FAIR_PRICE_ADJUSTMENT = 0.05

# ============ 债券类型筛选 ============
# 财汇债券类型：5=金融债，6=信用债；7=资产支持证券，8=资产支持票据（均剔除）
INCLUDED_BONDTYPE1 = ("5", "6")
EXCLUDED_BONDTYPE1 = ("7", "8")
START_DATE_BONDTYPE1 = ("5", "6")
END_DATE_BONDTYPE2 = ("631", "632", "641")  # 短融、中票使用发行截止日
EXCLUDED_ISSUER_KEYWORDS = ("中国农业发展银行", "中国进出口银行", "国家开发银行")
EXCLUDED_BOND_NAME_KEYWORDS = ()
EXCLUDED_BOND_NAME_LIKE_PATTERNS = ("__国开%", "__进出%", "__农发%")

# ============ Flask 配置 ============
FLASK_PORT = 5002
FLASK_DEBUG = True
