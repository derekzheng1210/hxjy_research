"""
一级发行非市场化评估系统 - 数据获取模块

负责从Oracle数据库获取：
1. 指定时间区间内新发债券（票面利率、发行截止日、期限、发行人）
2. 发行人存续债列表
3. 存续债估值数据（BESTIMATE表）
4. 评级曲线数据（TQ_QT_YIELDCURVE表）
5. 中债隐含评级（TQ_BD_NEWHIDECREDIT表）
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from . import config
from .db_utils import get_connection


def fetch_new_issues(conn, start_date: str, end_date: str, issuer: str = None) -> pd.DataFrame:
    """
    获取指定统一发行日期区间内的新发债券信息。

    日期口径：
    - 公司债、金融债：发行起始日（ISSBEGDATE）
    - 短融、中票：发行截止日（ISSENDDATE）
    - 资产支持证券/票据剔除

    Args:
        issuer: 可选，指定发行人时只查该发行人（避免全表扫描卡死）

    Returns:
        DataFrame with columns: SECODE, SYMBOL, BONDSNAME, ISSUER, COUPONRATE,
                                ISSUE_DATE, ISSBEGDATE, ISSENDDATE, STARTDATE,
                                MATURITYDATE, MATURITYYEAR, RATING
    """
    cur = conn.cursor()
    issuer_filter = ""
    params = {"start_date": start_date, "end_date": end_date}
    if issuer:
        issuer_filter = "AND n.COMPNAME = :issuer"
        params["issuer"] = issuer

    included_types = ",".join(f"'{t}'" for t in config.INCLUDED_BONDTYPE1)
    excluded_types = ",".join(f"'{t}'" for t in config.EXCLUDED_BONDTYPE1)
    start_date_types = ",".join(f"'{t}'" for t in config.START_DATE_BONDTYPE1)
    end_date_types = ",".join(f"'{t}'" for t in config.END_DATE_BONDTYPE2)
    excluded_issuer_filters = " ".join(
        f"AND n.COMPNAME NOT LIKE '%{keyword}%'"
        for keyword in config.EXCLUDED_ISSUER_KEYWORDS
    )
    excluded_bond_filters = " ".join(
        f"AND b.BONDSNAME NOT LIKE '%{keyword}%'"
        for keyword in config.EXCLUDED_BOND_NAME_KEYWORDS
    ) + " " + " ".join(
        f"AND b.BONDSNAME NOT LIKE '{pattern}'"
        for pattern in getattr(config, "EXCLUDED_BOND_NAME_LIKE_PATTERNS", ())
    )
    start_date_expr = "NVL(i.BIDDATE, b.ISSBEGDATE)"

    common_cols = """
        b.SECODE, b.SYMBOL, b.BONDSNAME, n.COMPNAME AS ISSUER,
        b.COUPONRATE, b.INITIALISSAMT AS ISSUE_AMOUNT_WAN,
        b.ISSBEGDATE, b.ISSENDDATE, b.ENTRYDATE,
        {issue_date_expr} AS ISSUE_DATE,
        b.STARTDATE, b.MATURITYDATE, b.MATURITYYEAR, b.RAISEMODE,
        b.BONDTYPE1, b.BONDTYPE2, n.NEWISSUERATE AS RATING,
        b.PUTDATE, b.REDEEMDATE, b.CVTBDEXPIREMEMP, n.EXCHANGE,
        CASE
            WHEN b.PUTDATE IS NOT NULL AND b.PUTDATE > '19010101'
            THEN ROUND((TO_DATE(b.PUTDATE, 'YYYYMMDD') - TO_DATE(b.STARTDATE, 'YYYYMMDD')) / 365.0, 4)
            WHEN b.REDEEMDATE IS NOT NULL AND b.REDEEMDATE > '19010101'
            THEN ROUND((TO_DATE(b.REDEEMDATE, 'YYYYMMDD') - TO_DATE(b.STARTDATE, 'YYYYMMDD')) / 365.0, 4)
            ELSE b.MATURITYYEAR
        END AS EFFECTIVE_TERM
    """
    common_from_where = f"""
        FROM TQ_BD_BASICINFO b
        JOIN TQ_BD_NEWESTBASICINFO n ON n.SECODE = b.SECODE AND n.ISVALID = 1
        LEFT JOIN TQ_BD_ISSUE i ON i.SECODE = b.SECODE AND i.ISVALID = 1
        WHERE b.ISVALID = 1
          AND NVL(b.BONDTYPE1, '0') NOT IN ({excluded_types})
          AND b.BONDTYPE1 IN ({included_types})
          {excluded_issuer_filters}
          {excluded_bond_filters}
          AND b.MATURITYYEAR >= 0.25
          {issuer_filter}
    """
    sql = f"""
        SELECT {common_cols.format(issue_date_expr=start_date_expr)}
        {common_from_where}
          AND b.BONDTYPE1 IN ({start_date_types})
          AND {start_date_expr} >= :start_date
          AND {start_date_expr} <= :end_date

        UNION ALL

        SELECT {common_cols.format(issue_date_expr="b.ISSENDDATE")}
        {common_from_where}
          AND NVL(b.BONDTYPE1, '0') NOT IN ({start_date_types})
          AND b.BONDTYPE2 IN ({end_date_types})
          AND b.ISSENDDATE >= :start_date
          AND b.ISSENDDATE <= :end_date

        UNION ALL

        SELECT {common_cols.format(issue_date_expr="b.ISSENDDATE")}
        {common_from_where}
          AND NVL(b.BONDTYPE1, '0') NOT IN ({start_date_types})
          AND NVL(b.BONDTYPE2, '0') NOT IN ({end_date_types})
          AND b.ISSENDDATE >= :start_date
          AND b.ISSENDDATE <= :end_date
    """
    cur.execute(sql, params)
    cols = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    # 按SYMBOL去重（同一债券可能因SECODE不同出现多行）
    if not df.empty:
        df = df.drop_duplicates(subset=["SYMBOL"], keep="first")
    return df


def fetch_issuer_outstanding(conn, issuer: str, ref_date: str) -> pd.DataFrame:
    """
    获取指定发行人在某日期的存续债列表

    Args:
        issuer: 发行人名称（COMPNAME）
        ref_date: 参考日期（YYYYMMDD格式），取该日期时点的存续债

    Returns:
        DataFrame with: SECODE, SYMBOL, BONDSNAME, MATURITYDATE, RATING
    """
    cur = conn.cursor()
    included_types = ",".join(f"'{t}'" for t in config.INCLUDED_BONDTYPE1)
    excluded_types = ",".join(f"'{t}'" for t in config.EXCLUDED_BONDTYPE1)
    excluded_issuer_filters = " ".join(
        f"AND COMPNAME NOT LIKE '%{keyword}%'"
        for keyword in config.EXCLUDED_ISSUER_KEYWORDS
    )
    excluded_bond_filters = " ".join(
        f"AND BONDSNAME NOT LIKE '%{keyword}%'"
        for keyword in config.EXCLUDED_BOND_NAME_KEYWORDS
    ) + " " + " ".join(
        f"AND BONDSNAME NOT LIKE '{pattern}'"
        for pattern in getattr(config, "EXCLUDED_BOND_NAME_LIKE_PATTERNS", ())
    )
    sql = f"""
        SELECT SECODE, SYMBOL, BONDSNAME, STARTDATE, MATURITYDATE,
               RAISEMODE, NEWISSUERATE AS RATING, CVTBDEXPIREMEMP, EXCHANGE
        FROM TQ_BD_NEWESTBASICINFO
        WHERE COMPNAME = :issuer
          AND ISVALID = 1
          AND NVL(BONDTYPE1, '0') NOT IN ({excluded_types})
          AND BONDTYPE1 IN ({included_types})
          {excluded_issuer_filters}
          {excluded_bond_filters}
          AND STARTDATE < :ref_date
          AND MATURITYDATE > :ref_date
    """
    cur.execute(sql, {"issuer": issuer, "ref_date": ref_date})
    cols = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def fetch_all_valuations_for_date(conn, trade_date: str) -> dict[str, dict]:
    """
    一次性读取 BESTIMATE 表单日全量估值数据（日期切片策略）。

    参考 juyuan_credit_tools_portal 的优化方式：
    BESTIMATE 表在 TDATE 列上有索引，读取单日全量数据是一次高效的索引扫描；
    而 SYMBOL IN (...) 强制 Oracle 做多次索引跳跃查找，数据量大时极慢。

    Args:
        trade_date: 估值日期（YYYYMMDD格式）

    Returns:
        {symbol: {"yield": float, "term": float}} 全量字典
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT SYMBOL, YIELD, TERMTOMATURITY
        FROM BESTIMATE
        WHERE TDATE = :d
          AND YIELD IS NOT NULL
          AND DATASOURCE = '1'
    """, {"d": trade_date})

    results = {}
    for symbol, yld, term in cur.fetchall():
        if yld is not None and term is not None:
            results[str(symbol)] = {
                "yield": float(yld),
                "term": float(term),
            }
    return results


def fetch_valuations_for_symbols_range(
    conn,
    symbols: list[str],
    start_date: str,
    end_date: str,
    batch_size: int = 800,
) -> dict[str, dict[str, dict]]:
    """
    Fetch BESTIMATE rows for a bounded symbol set and date range.

    Returns:
        {symbol: {trade_date: {"yield": float, "term": float}}}
    """
    if not symbols:
        return {}

    unique_symbols = list(dict.fromkeys(str(s) for s in symbols if s))
    if not unique_symbols:
        return {}

    cur = conn.cursor()
    cur.arraysize = 5000
    results: dict[str, dict[str, dict]] = {}

    for i in range(0, len(unique_symbols), batch_size):
        batch = unique_symbols[i:i + batch_size]
        placeholders = ",".join(f":s{j}" for j in range(len(batch)))
        params = {f"s{j}": s for j, s in enumerate(batch)}
        params.update({"start_date": start_date, "end_date": end_date})

        sql = f"""
            SELECT /*+ LEADING(b) INDEX(b) */ b.SYMBOL, b.TDATE, b.YIELD, b.TERMTOMATURITY
            FROM BESTIMATE b
            WHERE b.TDATE >= :start_date
              AND b.TDATE <= :end_date
              AND b.SYMBOL IN ({placeholders})
              AND b.YIELD IS NOT NULL
              AND b.TERMTOMATURITY IS NOT NULL
              AND b.DATASOURCE = '1'
        """
        cur.execute(sql, params)
        for symbol, trade_date, yld, term in cur.fetchall():
            sym = str(symbol)
            d = str(trade_date)
            results.setdefault(sym, {})[d] = {
                "yield": float(yld),
                "term": float(term),
            }

    return results


def fetch_valuations_for_symbols_dates(
    conn,
    symbols: list[str],
    trade_dates: list[str],
    symbol_batch_size: int = 800,
    date_batch_size: int = 1,
) -> dict[str, dict[str, dict]]:
    """
    Fetch BESTIMATE rows for selected symbols and selected dates.

    The selected-date path is much faster for issuers with scattered issue dates:
    it avoids scanning every calendar date between the first and last issue.
    """
    if not symbols or not trade_dates:
        return {}

    unique_symbols = list(dict.fromkeys(str(s) for s in symbols if s))
    unique_dates = list(dict.fromkeys(str(d)[:8] for d in trade_dates if d))
    if not unique_symbols or not unique_dates:
        return {}

    cur = conn.cursor()
    cur.arraysize = 5000
    results: dict[str, dict[str, dict]] = {}

    def run_query(symbol_batch: list[str], date_batch: list[str]) -> None:
        symbol_placeholders = ",".join(f":s{j}" for j in range(len(symbol_batch)))
        date_placeholders = ",".join(f":d{j}" for j in range(len(date_batch)))
        params = {f"s{j}": s for j, s in enumerate(symbol_batch)}
        params.update({f"d{j}": d for j, d in enumerate(date_batch)})

        sql = f"""
            SELECT /*+ LEADING(b) INDEX(b) */ b.SYMBOL, b.TDATE, b.YIELD, b.TERMTOMATURITY
            FROM BESTIMATE b
            WHERE b.TDATE IN ({date_placeholders})
              AND b.SYMBOL IN ({symbol_placeholders})
              AND b.YIELD IS NOT NULL
              AND b.TERMTOMATURITY IS NOT NULL
              AND b.DATASOURCE = '1'
        """
        cur.execute(sql, params)
        for symbol, trade_date, yld, term in cur.fetchall():
            sym = str(symbol)
            d = str(trade_date)
            results.setdefault(sym, {})[d] = {
                "yield": float(yld),
                "term": float(term),
            }

    for si in range(0, len(unique_symbols), symbol_batch_size):
        symbol_batch = unique_symbols[si:si + symbol_batch_size]

        for di in range(0, len(unique_dates), date_batch_size):
            date_batch = unique_dates[di:di + date_batch_size]
            try:
                run_query(symbol_batch, date_batch)
            except Exception:
                if len(date_batch) == 1:
                    raise
                for trade_date in date_batch:
                    run_query(symbol_batch, [trade_date])

    return results


def fetch_implied_ratings(conn, secodes: list[str]) -> dict[str, str]:
    """
    批量获取债券的中债隐含评级（从 TQ_BD_NEWHIDECREDIT 表）。

    参考 juyuan_credit_tools_portal 项目中 implied_rating 的使用方式，
    用隐含评级替代发行时评级来选择收益率曲线。

    表结构：TQ_BD_NEWHIDECREDIT（债券最新隐含评级表（中债））
    - SECODE: 证券内码（关联键）
    - CREDITSOURCE: 评级来源（1=中债登）
    - HIDECREDITSTATUS: 当前状态（1=最新）
    - STDCREDIT: 标准隐藏评级（如 AAA, AA+, AA, AA(2) 等）
    - ISVALID: 是否有效（1=是）

    Args:
        secodes: 证券内码列表

    Returns:
        {secode: implied_rating_str} 字典
    """
    if not secodes:
        return {}

    cur = conn.cursor()
    results = {}

    # 分批查询，每批500个（避免 Oracle IN 子句限制）
    batch_size = 500
    for i in range(0, len(secodes), batch_size):
        batch = secodes[i:i + batch_size]
        placeholders = ",".join(f":s{j}" for j in range(len(batch)))
        params = {f"s{j}": s for j, s in enumerate(batch)}

        sql = f"""
            SELECT SECODE, STDCREDIT
            FROM TQ_BD_NEWHIDECREDIT
            WHERE SECODE IN ({placeholders})
              AND CREDITSOURCE = '1'
              AND HIDECREDITSTATUS = '1'
              AND ISVALID = 1
        """
        cur.execute(sql, params)
        for secode, stdcredit in cur.fetchall():
            if secode and stdcredit:
                results[str(secode)] = str(stdcredit).strip()

    return results


def fetch_bond_valuations(conn, symbols: list[str], trade_date: str) -> dict[str, dict]:
    """
    批量获取债券估值数据（从BESTIMATE表）

    优化策略：使用日期切片读取全量数据，Python端过滤目标symbols。
    相比原来的 SYMBOL IN (...) 分批查询，SQL往返从 N/200 次降为 1 次。

    Args:
        symbols: 债券代码列表
        trade_date: 估值日期

    Returns:
        {symbol: {"yield": float, "term": float}} 字典
    """
    if not symbols:
        return {}

    # 日期切片：一次读取该日所有估值，Python端过滤
    all_vals = fetch_all_valuations_for_date(conn, trade_date)
    symbol_set = set(symbols)
    return {sym: val for sym, val in all_vals.items() if sym in symbol_set}


def fetch_curve(conn, curve_code: str, trade_date: str) -> list[tuple[float, float]]:
    """
    获取指定曲线在指定日期的完整点位

    Args:
        curve_code: 曲线代码（如 '216' 为AA+）
        trade_date: 交易日期

    Returns:
        [(maturity, yield), ...] 按期限排序
    """
    cur = conn.cursor()
    sql = """
        SELECT MATURITY, YIELD
        FROM TQ_QT_YIELDCURVE
        WHERE TRADEDATE = :d
          AND YCURVECODE = :c
          AND YCURVETYPE = '1'
          AND ISVALID = 1
        ORDER BY MATURITY
    """
    cur.execute(sql, {"d": trade_date, "c": curve_code})
    return [(float(m), float(y)) for m, y in cur.fetchall() if m is not None and y is not None]


def fetch_nearest_curve_date(conn, target_date: str, curve_code: str = "216") -> Optional[str]:
    """
    获取最近的有曲线数据的交易日（向前找）

    Args:
        target_date: 目标日期
        curve_code: 曲线代码

    Returns:
        最近的交易日字符串 或 None
    """
    cur = conn.cursor()
    sql = """
        SELECT MAX(TRADEDATE)
        FROM TQ_QT_YIELDCURVE
        WHERE TRADEDATE <= :d
          AND TRADEDATE >= :d_minus
          AND YCURVECODE = :c
          AND YCURVETYPE = '1'
          AND ISVALID = 1
          AND MATURITY = 1
    """
    # 往前找30天范围
    from datetime import timedelta
    target_dt = datetime.strptime(target_date[:8], "%Y%m%d")
    d_minus = (target_dt - timedelta(days=30)).strftime("%Y%m%d")

    cur.execute(sql, {"d": target_date, "d_minus": d_minus, "c": curve_code})
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def fetch_nearest_valuation_date(conn, symbol: str, target_date: str) -> Optional[str]:
    """
    获取某债券最近的有估值的日期（向前找）
    """
    cur = conn.cursor()
    from datetime import timedelta
    target_dt = datetime.strptime(target_date[:8], "%Y%m%d")
    d_minus = (target_dt - timedelta(days=15)).strftime("%Y%m%d")

    sql = """
        SELECT MAX(TDATE)
        FROM BESTIMATE
        WHERE SYMBOL = :s
          AND TDATE <= :d
          AND TDATE >= :d_minus
          AND YIELD IS NOT NULL
          AND DATASOURCE = '1'
    """
    cur.execute(sql, {"s": symbol, "d": target_date, "d_minus": d_minus})
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def get_all_issuers(conn, start_date: str, end_date: str) -> list[str]:
    """获取指定时间区间内所有发行人名称"""
    cur = conn.cursor()
    included_types = ",".join(f"'{t}'" for t in config.INCLUDED_BONDTYPE1)
    excluded_types = ",".join(f"'{t}'" for t in config.EXCLUDED_BONDTYPE1)
    start_date_types = ",".join(f"'{t}'" for t in config.START_DATE_BONDTYPE1)
    end_date_types = ",".join(f"'{t}'" for t in config.END_DATE_BONDTYPE2)
    excluded_issuer_filters = " ".join(
        f"AND n.COMPNAME NOT LIKE '%{keyword}%'"
        for keyword in config.EXCLUDED_ISSUER_KEYWORDS
    )
    excluded_bond_filters = " ".join(
        f"AND b.BONDSNAME NOT LIKE '%{keyword}%'"
        for keyword in config.EXCLUDED_BOND_NAME_KEYWORDS
    ) + " " + " ".join(
        f"AND b.BONDSNAME NOT LIKE '{pattern}'"
        for pattern in getattr(config, "EXCLUDED_BOND_NAME_LIKE_PATTERNS", ())
    )
    start_date_expr = "NVL(i.BIDDATE, b.ISSBEGDATE)"
    sql = f"""
        SELECT DISTINCT COMPNAME
        FROM (
            SELECT n.COMPNAME,
                   CASE
                       WHEN b.BONDTYPE1 IN ({start_date_types}) THEN {start_date_expr}
                       WHEN b.BONDTYPE2 IN ({end_date_types}) THEN b.ISSENDDATE
                       ELSE b.ISSENDDATE
                   END AS ISSUE_DATE
            FROM TQ_BD_BASICINFO b
            JOIN TQ_BD_NEWESTBASICINFO n ON n.SECODE = b.SECODE AND n.ISVALID = 1
            LEFT JOIN TQ_BD_ISSUE i ON i.SECODE = b.SECODE AND i.ISVALID = 1
            WHERE b.ISVALID = 1
              AND NVL(b.BONDTYPE1, '0') NOT IN ({excluded_types})
              AND b.BONDTYPE1 IN ({included_types})
              {excluded_issuer_filters}
              {excluded_bond_filters}
              AND b.MATURITYYEAR >= 0.25
              AND n.COMPNAME IS NOT NULL
        )
        WHERE ISSUE_DATE >= :start_date
          AND ISSUE_DATE <= :end_date
    """
    cur.execute(sql, {"start_date": start_date, "end_date": end_date})
    return [r[0] for r in cur.fetchall() if r[0]]

