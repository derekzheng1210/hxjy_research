"""
一级发行非市场化评估系统 - 数据库工具
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import oracledb

from . import config


_CLIENT_INITIALIZED = False


def init_oracle_client() -> None:
    """初始化 Oracle thick client（仅初始化一次）"""
    global _CLIENT_INITIALIZED
    if _CLIENT_INITIALIZED:
        return
    if config.ORACLE_CLIENT_DIR and os.path.isdir(config.ORACLE_CLIENT_DIR):
        try:
            oracledb.init_oracle_client(lib_dir=config.ORACLE_CLIENT_DIR)
        except Exception as exc:
            if "already been initialized" not in str(exc):
                raise
    _CLIENT_INITIALIZED = True


@contextmanager
def get_connection():
    """获取Oracle数据库连接（上下文管理器）"""
    init_oracle_client()
    conn = oracledb.connect(
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        dsn=config.DB_DSN,
    )
    try:
        yield conn
    finally:
        conn.close()


def rows_as_dicts(cursor) -> list[dict]:
    """将cursor结果转为字典列表"""
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
