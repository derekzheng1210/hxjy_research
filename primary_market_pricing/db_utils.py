"""
一级发行非市场化评估系统 - 数据库工具

Oracle 连接与结果集转换统一复用 juyuan_update.db 的实现，
本项目仅注入自身的连接配置（环境变量命名与 juyuan_update 不同）。
"""

from __future__ import annotations

from contextlib import contextmanager

from juyuan_update.db import connect as _juyuan_connect, rows_as_dicts  # noqa: F401  (re-export)

from . import config

_ORACLE_CLIENT_DIR = config.ORACLE_CLIENT_DIR


@contextmanager
def get_connection():
    """获取Oracle数据库连接（上下文管理器）"""
    with _juyuan_connect(
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        dsn=config.DB_DSN,
    ) as conn:
        yield conn
