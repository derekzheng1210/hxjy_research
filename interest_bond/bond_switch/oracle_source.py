"""Oracle批量取数：30年国债候选券、成交行情与中债估值。"""
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
    return oracledb.connect(user=config.ORACLE_USER, password=config.ORACLE_PASSWORD, dsn=config.ORACLE_DSN)


def latest_available_dates() -> tuple[str | None, str | None]:
    start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(TRADEDATE) FROM TQ_QT_CBESTIMATE "
            "WHERE TRADEDATE>=:s AND DATASOURCE='1' AND ISVALID=1",
            {"s": start},
        )
        valuation_date = cur.fetchone()[0]
        cur.execute(
            "SELECT MAX(TRADEDATE) FROM TQ_QT_BDIBQUOTE WHERE TRADEDATE>=:s AND ISVALID=1",
            {"s": start},
        )
        quote_date = cur.fetchone()[0]
    return (str(valuation_date) if valuation_date else None, str(quote_date) if quote_date else None)


def fetch_candidates(valuation_date: str, quote_date: str, progress=print) -> list[dict]:
    """取估值日剩余期限26-30Y的存量国债及最近成交行情。"""
    progress(f"筛选30年国债候选券 {valuation_date}")
    sql = """
    WITH est AS (
      SELECT e.ID,e.SECODE,e.TERMTOMATURITY,e.YIELD,e.VALUATIONTYPE,
             ROW_NUMBER() OVER(
               PARTITION BY e.SECODE
               ORDER BY CASE WHEN e.VALUATIONTYPE='1' THEN 0 ELSE 1 END,e.ID DESC
             ) rn
      FROM TQ_QT_CBESTIMATE e
      WHERE e.TRADEDATE=:valuation_date
        AND e.DATASOURCE='1' AND e.ISVALID=1 AND e.YIELD IS NOT NULL
        AND e.TERMTOMATURITY BETWEEN :min_years AND :max_years
    ), quote_day AS (
      SELECT SECODE,DEALS,VOL
      FROM TQ_QT_BDIBQUOTE
      WHERE TRADEDATE=:quote_date AND ISVALID=1
    )
    SELECT b.SYMBOL,b.BONDSNAME,b.BONDNAME,b.SECODE,b.ISSBEGDATE,b.LISTDATE,b.MATURITYDATE,
           NVL(b.CURRENTAMT,b.ACTISSAMT),e.TERMTOMATURITY,e.YIELD,
           NVL(q.DEALS,0),NVL(q.VOL,0)
    FROM est e
    JOIN TQ_BD_NEWESTBASICINFO b ON b.SECODE=e.SECODE AND b.ISVALID=1
    LEFT JOIN quote_day q ON q.SECODE=b.SECODE
    WHERE e.rn=1 AND b.BONDTYPE1='1' AND b.BONDTYPE2='111' AND b.EXCHANGE='001005'
    ORDER BY NVL(q.DEALS,0) DESC,NVL(q.VOL,0) DESC,b.ISSBEGDATE DESC
    """
    with connect() as conn:
        cur = conn.cursor()
        cur.arraysize = 1000
        cur.execute(
            sql,
            {
                "valuation_date": valuation_date,
                "quote_date": quote_date,
                "min_years": config.REMAINING_MIN_YEARS,
                "max_years": config.REMAINING_MAX_YEARS,
            },
        )
        raw = cur.fetchall()
        secodes = [str(r[3]) for r in raw]
        issue_stats: dict[str, tuple[int, int]] = {}
        if secodes:
            placeholders = ",".join(f":s{i}" for i in range(len(secodes)))
            cur.execute(
                f"""SELECT SECODE,COUNT(DISTINCT ISSUEID),MAX(FURISSUTIMES)
                    FROM TQ_BD_ISSUE
                    WHERE ISVALID=1 AND SECODE IN ({placeholders})
                    GROUP BY SECODE""",
                {f"s{i}": code for i, code in enumerate(secodes)},
            )
            for secode, event_count, source_reissues in cur.fetchall():
                issue_stats[str(secode)] = (int(event_count or 0), int(source_reissues or 0))

    out = []
    for row in raw:
        code, short_name, full_name, secode, issue_date, list_date, maturity_date, amount, rem, yld, deals, vol = row
        event_count, source_reissues = issue_stats.get(str(secode), (0, 0))
        out.append(
            {
                "code": str(code), "short_name": str(short_name or code), "full_name": str(full_name or short_name or code),
                "secode": str(secode), "issue_date": str(issue_date or ""), "list_date": str(list_date or ""),
                "maturity_date": str(maturity_date or ""), "outstanding_amount": float(amount) if amount is not None else None,
                "remaining_years": float(rem), "valuation_yield": float(yld), "deal_count": int(deals or 0),
                "volume": float(vol or 0), "reissue_count": max(max(event_count - 1, 0), source_reissues),
            }
        )
    return out


def fetch_candidate_universe(start_ymd: str, end_ymd: str, progress=print) -> list[str]:
    """窗口期内任一交易日剩余期限落在26-30Y带内的全部国债代码（历史候选全集）。"""
    progress(f"筛选窗口期历史候选券 {start_ymd}~{end_ymd}")
    sql = """
    SELECT DISTINCT b.SYMBOL
    FROM TQ_QT_CBESTIMATE e
    JOIN TQ_BD_NEWESTBASICINFO b ON b.SECODE=e.SECODE AND b.ISVALID=1
    WHERE e.TRADEDATE>=:s AND e.TRADEDATE<=:e
      AND e.DATASOURCE='1' AND e.ISVALID=1 AND e.YIELD IS NOT NULL
      AND e.TERMTOMATURITY BETWEEN :min_years AND :max_years
      AND b.BONDTYPE1='1' AND b.BONDTYPE2='111' AND b.EXCHANGE='001005'
    """
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            sql,
            {"s": start_ymd, "e": end_ymd, "min_years": config.REMAINING_MIN_YEARS, "max_years": config.REMAINING_MAX_YEARS},
        )
        codes = sorted({str(r[0]) for r in cur.fetchall()})
    progress(f"历史候选券共{len(codes)}只")
    return codes


def fetch_quote_history(codes: list[str], start_ymd: str, end_ymd: str, progress=print) -> list[tuple[str, str, int, float]]:
    """批量拉取指定券银行间逐日成交（按日汇总，用于每日角色判定）。"""
    if not codes or start_ymd > end_ymd:
        return []
    progress(f"拉取银行间逐日成交 {start_ymd}~{end_ymd} ({len(codes)}只)")
    placeholders = ",".join(f":c{i}" for i in range(len(codes)))
    sql = f"""
    SELECT q.TRADEDATE,b.SYMBOL,SUM(NVL(q.DEALS,0)),SUM(NVL(q.VOL,0))
    FROM TQ_QT_BDIBQUOTE q
    JOIN TQ_BD_NEWESTBASICINFO b ON b.SECODE=q.SECODE AND b.ISVALID=1
    WHERE q.TRADEDATE>=:s AND q.TRADEDATE<=:e AND q.ISVALID=1
      AND b.SYMBOL IN ({placeholders})
    GROUP BY q.TRADEDATE,b.SYMBOL
    ORDER BY q.TRADEDATE
    """
    binds = {"s": start_ymd, "e": end_ymd, **{f"c{i}": c for i, c in enumerate(codes)}}
    out = []
    with connect() as conn:
        cur = conn.cursor()
        cur.arraysize = 5000
        cur.execute(sql, binds)
        for trade_date, code, deals, vol in cur:
            out.append((str(trade_date), str(code), int(deals or 0), float(vol or 0)))
    return out


def fetch_valuation_history(codes: list[str], start_ymd: str, end_ymd: str, progress=print) -> list[tuple[str, str, float, float | None]]:
    """批量拉取指定券中债估值，VALUATIONTYPE优先1，不平均多条估值。"""
    if not codes or start_ymd > end_ymd:
        return []
    progress(f"拉取中债估值 {start_ymd}~{end_ymd} ({len(codes)}只)")
    placeholders = ",".join(f":c{i}" for i in range(len(codes)))
    sql = f"""
    SELECT e.ID,e.TRADEDATE,b.SYMBOL,e.YIELD,e.TERMTOMATURITY,e.VALUATIONTYPE
    FROM TQ_QT_CBESTIMATE e
    JOIN TQ_BD_NEWESTBASICINFO b ON b.SECODE=e.SECODE AND b.ISVALID=1
    WHERE e.TRADEDATE>=:s AND e.TRADEDATE<=:e
      AND e.DATASOURCE='1' AND e.ISVALID=1 AND e.YIELD IS NOT NULL
      AND b.SYMBOL IN ({placeholders})
    ORDER BY e.TRADEDATE,e.ID
    """
    binds = {"s": start_ymd, "e": end_ymd, **{f"c{i}": c for i, c in enumerate(codes)}}
    chosen: dict[tuple[str, str], tuple[int, int, float, float | None]] = {}
    with connect() as conn:
        cur = conn.cursor()
        cur.arraysize = 5000
        cur.execute(sql, binds)
        for row_id, trade_date, code, value, remaining, valuation_type in cur:
            key = (str(trade_date), str(code))
            rank = 0 if str(valuation_type or "") == "1" else 1
            candidate = (rank, -int(row_id or 0), float(value), float(remaining) if remaining is not None else None)
            if key not in chosen or candidate[:2] < chosen[key][:2]:
                chosen[key] = candidate
    return [(d, code, value[2], value[3]) for (d, code), value in sorted(chosen.items())]
