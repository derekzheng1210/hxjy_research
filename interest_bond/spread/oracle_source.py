"""Oracle(聚源/财汇)收益率曲线拉取。表: TQ_QT_YIELDCURVE。"""
from __future__ import annotations

import os
from datetime import date, timedelta

import oracledb

from . import config

_client_initialized = False


def _init_client() -> None:
    global _client_initialized
    if _client_initialized:
        return
    if config.ORACLE_CLIENT and os.path.isdir(config.ORACLE_CLIENT):
        try:
            oracledb.init_oracle_client(lib_dir=config.ORACLE_CLIENT)
        except Exception as exc:
            if "already" not in str(exc).lower():
                raise
    _client_initialized = True


def connect():
    _init_client()
    return oracledb.connect(
        user=config.ORACLE_USER,
        password=config.ORACLE_PASSWORD,
        dsn=config.ORACLE_DSN,
    )


def _chunks(start_ymd: str, end_ymd: str, days: int):
    start = date(int(start_ymd[:4]), int(start_ymd[4:6]), int(start_ymd[6:8]))
    end = date(int(end_ymd[:4]), int(end_ymd[4:6]), int(end_ymd[6:8]))
    while start <= end:
        chunk_end = min(start + timedelta(days=days - 1), end)
        yield start.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")
        start = chunk_end + timedelta(days=1)


def fetch_curve(curve_id: str, start_ymd: str, end_ymd: str, progress=print) -> list[tuple[str, str, float, float]]:
    """拉取一条曲线在[start,end]内全部点位,返回 (trade_date, curve_id, tenor, yield) 列表。"""
    meta = config.CURVES[curve_id]
    tenors = meta["tenors"]
    placeholders = ",".join(f":t{i}" for i in range(len(tenors)))
    binds_base = {f"t{i}": t for i, t in enumerate(tenors)}
    binds_base["code"] = meta["code"]
    binds_base["ctype"] = meta["type"]
    rows: dict[tuple[str, float], float] = {}
    with connect() as conn:
        cur = conn.cursor()
        cur.arraysize = 2000
        for chunk_start, chunk_end in _chunks(start_ymd, end_ymd, config.FETCH_CHUNK_DAYS):
            progress(f"Oracle拉取 {meta['name']}({meta['code']}) {chunk_start}~{chunk_end}")
            binds = dict(binds_base, s=chunk_start, e=chunk_end)
            cur.execute(
                f"""
                SELECT TRADEDATE, MATURITY, YIELD
                FROM TQ_QT_YIELDCURVE
                WHERE TRADEDATE >= :s AND TRADEDATE <= :e
                  AND YCURVECODE = :code
                  AND YCURVETYPE = :ctype
                  AND MATURITY IN ({placeholders})
                  AND ISVALID = 1
                ORDER BY TRADEDATE
                """,
                binds,
            )
            for trade_date, maturity, value in cur.fetchall():
                if trade_date is None or maturity is None or value is None:
                    continue
                rows[(str(trade_date), float(maturity))] = float(value)
    return [(d, curve_id, t, v) for (d, t), v in sorted(rows.items())]


def latest_available_date() -> str | None:
    """曲线表中最近一个交易日(YYYYMMDD),仅扫近期窗口以减轻Oracle压力。"""
    probe_start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(TRADEDATE) FROM TQ_QT_YIELDCURVE WHERE TRADEDATE >= :s",
            {"s": probe_start},
        )
        row = cur.fetchone()
    return str(row[0]) if row and row[0] else None
