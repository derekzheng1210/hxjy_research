# -*- coding: utf-8 -*-
"""从中长期纯债基金指数的量化数据库（MySQL）同步每日收盘价。

数据源：financedata.cmfindexeod（万得 885008.WI 中长期纯债型基金指数），
替代原「基金指数 Excel 上传」：由每日更新任务自动同步，写入
fund_prices_frozen.json 供策略仪表盘使用。

连接凭证不写入代码：FUND_INDEX_DB_USER / FUND_INDEX_DB_PASSWORD 必须配置在
环境变量或 .env 中；主机、端口与库名可用 FUND_INDEX_DB_HOST /
FUND_INDEX_DB_PORT / FUND_INDEX_DB_NAME 覆盖默认值。
"""
from __future__ import annotations

import os

import pymysql

from . import config
from .unified_excel import write_json

FUND_CODE = "885008.WI"
# 全量同步起点：仪表盘回测区间自 2023 年起，留足余量并便于历史扩展。
DEFAULT_START_DATE = "20100101"

_SQL = (
    "SELECT TRADE_DT, S_DQ_CLOSE FROM cmfindexeod "
    "WHERE S_INFO_WINDCODE = %s AND TRADE_DT >= %s "
    "ORDER BY TRADE_DT"
)


def _db_config() -> dict:
    user = os.environ.get("FUND_INDEX_DB_USER", "")
    password = os.environ.get("FUND_INDEX_DB_PASSWORD", "")
    if not user or not password:
        raise RuntimeError(
            "基金指数数据库账号未配置：请在环境变量或 .env 中设置 "
            "FUND_INDEX_DB_USER / FUND_INDEX_DB_PASSWORD"
        )
    return {
        "host": os.environ.get("FUND_INDEX_DB_HOST", "quantstudio.mysql.rds.aliyuncs.com"),
        "port": int(os.environ.get("FUND_INDEX_DB_PORT", "3306")),
        "user": user,
        "password": password,
        "database": os.environ.get("FUND_INDEX_DB_NAME", "financedata"),
        "charset": "gbk",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": 15,
        "read_timeout": 60,
    }


def fetch_fund_index_rows(start_date: str = DEFAULT_START_DATE) -> list[dict]:
    """拉取指数收盘价并转换为冻结序列格式：[{"date": "YYYY-MM-DD", "close": float}]。"""
    connection = pymysql.connect(**_db_config())
    try:
        with connection.cursor() as cursor:
            cursor.execute(_SQL, (FUND_CODE, start_date))
            raw_rows = cursor.fetchall()
    finally:
        connection.close()

    dedup: dict[str, dict] = {}
    for row in raw_rows:
        dt = str(row.get("TRADE_DT") or "").strip()[:8]
        if len(dt) != 8 or not dt.isdigit():
            continue
        try:
            close = round(float(row.get("S_DQ_CLOSE")), 6)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        date_text = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}"
        dedup[date_text] = {"date": date_text, "close": close}
    return [dedup[d] for d in sorted(dedup)]


def refresh_fund_index(start_date: str | None = None) -> dict:
    """同步指数序列到冻结缓存文件；查询结果为空时保留最近一次缓存。"""
    rows = fetch_fund_index_rows(start_date or DEFAULT_START_DATE)
    if not rows:
        raise RuntimeError(f"基金指数查询结果为空（{FUND_CODE}），已保留最近一次缓存")
    write_json(config.STRATEGY_FUND_PRICES_FROZEN, rows)
    return {
        "fund_prices": len(rows),
        "fund_start": rows[0]["date"],
        "fund_end": rows[-1]["date"],
    }
