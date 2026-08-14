from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import os
from typing import Iterable

import oracledb

from . import config


_CLIENT_INITIALIZED = False


def _init_client() -> None:
    global _CLIENT_INITIALIZED
    if _CLIENT_INITIALIZED:
        return
    if config.ORACLE_CLIENT and os.path.isdir(config.ORACLE_CLIENT):
        try:
            oracledb.init_oracle_client(lib_dir=config.ORACLE_CLIENT)
        except Exception as exc:
            # Calling init twice raises in some environments; connecting below
            # will surface real client problems.
            if "init_oracle_client() was already called" not in str(exc):
                raise
    _CLIENT_INITIALIZED = True


@contextmanager
def connect():
    _init_client()
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
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def yyyymmdd(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")[:8]


DEFAULT_CURVE_TENORS = tuple(range(11))


def _default_curve_code() -> str | None:
    overrides = getattr(config, "CURVE_CODE_OVERRIDES", {}) or {}
    for code in overrides.values():
        if code:
            return str(code)
    return None


def _calendar_chunks(start_ymd: str, end_ymd: str, days: int = 31):
    start = datetime.strptime(start_ymd, "%Y%m%d").date()
    end = datetime.strptime(end_ymd, "%Y%m%d").date()
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=days - 1), end)
        yield current.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")
        current = chunk_end + timedelta(days=1)


def _curve_code_key(meta: dict) -> str:
    return str(meta.get("code", ""))


def _fetch_curve_rows(
    conn,
    curve_codes: dict[str, dict],
    where_sql: str,
    binds: dict,
    tenors: Iterable[int | float] | None = None,
) -> dict[str, dict[float, dict[str, float]]]:
    code_to_key = {_curve_code_key(meta): key for key, meta in curve_codes.items() if meta.get("code")}
    if not code_to_key:
        return {key: {} for key in curve_codes}
    tenor_values = list(DEFAULT_CURVE_TENORS if tenors is None else tenors)
    params = {
        **binds,
        **{f"c{i}": code for i, code in enumerate(code_to_key)},
        **{f"t{i}": tenor for i, tenor in enumerate(tenor_values)},
    }
    code_placeholders = ",".join(f":c{i}" for i in range(len(code_to_key)))
    tenor_placeholders = ",".join(f":t{i}" for i in range(len(tenor_values)))
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT TRADEDATE, YCURVECODE, MATURITY, YIELD
        FROM TQ_QT_YIELDCURVE
        WHERE {where_sql}
          AND YCURVECODE IN ({code_placeholders})
          AND YCURVETYPE = '1'
          AND MATURITY IN ({tenor_placeholders})
          AND ISVALID = 1
        ORDER BY TRADEDATE, YCURVECODE, MATURITY
        """,
        params,
    )
    result: dict[str, dict[float, dict[str, float]]] = {key: {} for key in curve_codes}
    for trade_date, curve_code, maturity, value in cur.fetchall():
        if maturity is None or value is None:
            continue
        key = code_to_key.get(str(curve_code))
        if not key:
            continue
        result.setdefault(key, {}).setdefault(float(maturity), {})[str(trade_date)] = float(value)
    return result


def latest_curve_date(conn, fallback_days: int = 20) -> str:
    cur = conn.cursor()
    today = date.today().strftime("%Y%m%d")
    curve_code = _default_curve_code()
    if curve_code:
        since = (date.today() - timedelta(days=fallback_days)).strftime("%Y%m%d")
        cur.execute(
            """
            SELECT MAX(TRADEDATE)
            FROM TQ_QT_YIELDCURVE
            WHERE TRADEDATE >= :since
              AND TRADEDATE <= :today
              AND YCURVECODE = :curve_code
              AND YCURVETYPE = '1'
              AND MATURITY = 1
              AND ISVALID = 1
            """,
            {"since": since, "today": today, "curve_code": curve_code},
        )
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0])
        cur.execute(
            """
            SELECT MAX(TRADEDATE)
            FROM TQ_QT_YIELDCURVE
            WHERE TRADEDATE <= :today
              AND YCURVECODE = :curve_code
              AND YCURVETYPE = '1'
              AND MATURITY = 1
              AND ISVALID = 1
            """,
            {"today": today, "curve_code": curve_code},
        )
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0])
    cur.execute(
        "SELECT MAX(TRADEDATE) FROM TQ_QT_YIELDCURVE WHERE TRADEDATE <= :today",
        {"today": today},
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return today


def trading_dates(conn, start_date: str, end_date: str) -> list[str]:
    cur = conn.cursor()
    curve_code = _default_curve_code()
    if curve_code:
        cur.execute(
            """
            SELECT DISTINCT TRADEDATE
            FROM TQ_QT_YIELDCURVE
            WHERE TRADEDATE >= :start_date
              AND TRADEDATE <= :end_date
              AND YCURVECODE = :curve_code
              AND YCURVETYPE = '1'
              AND MATURITY = 1
              AND ISVALID = 1
            ORDER BY TRADEDATE
            """,
            {"start_date": start_date, "end_date": end_date, "curve_code": curve_code},
        )
        return [str(r[0]) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT DISTINCT TRADEDATE
        FROM TQ_QT_YIELDCURVE
        WHERE TRADEDATE >= :start_date
          AND TRADEDATE <= :end_date
        ORDER BY TRADEDATE
        """,
        {"start_date": start_date, "end_date": end_date},
    )
    return [r[0] for r in cur.fetchall()]


def nearest_ubestimate_date(conn, target_date: str, before: bool = False) -> str | None:
    cur = conn.cursor()
    op = "<" if before else "<="
    cur.execute(
        f"SELECT MAX(TDATE) FROM TQ_BD_SHCLEST WHERE TDATE {op} :target_date",
        {"target_date": target_date},
    )
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def ubestimate_reference_dates(conn, end_date: str) -> dict[str, str]:
    current = nearest_ubestimate_date(conn, end_date)
    if not current:
        return {}
    current_dt = datetime.strptime(current, "%Y%m%d").date()
    targets = {
        "当前": current,
        "昨日": nearest_ubestimate_date(conn, current, before=True),
        "一周前": nearest_ubestimate_date(conn, (current_dt - timedelta(days=7)).strftime("%Y%m%d")),
        "一月前": nearest_ubestimate_date(conn, (current_dt - timedelta(days=30)).strftime("%Y%m%d")),
        "年初": nearest_ubestimate_date(conn, f"{current_dt.year}0101", before=True),
    }
    return {label: dt for label, dt in targets.items() if dt}


def nearest_shclest_date(conn, target_date: str, before: bool = False) -> str | None:
    return nearest_ubestimate_date(conn, target_date, before=before)


def shclest_reference_dates(conn, end_date: str) -> dict[str, str]:
    return ubestimate_reference_dates(conn, end_date)


def resolve_curve_codes(conn, curve_defs: dict[str, str], trade_date: str) -> dict[str, dict]:
    cur = conn.cursor()
    resolved = {}
    for key, keyword in curve_defs.items():
        override = getattr(config, "CURVE_CODE_OVERRIDES", {}).get(key)
        if override:
            resolved[key] = {
                "code": override,
                "name": keyword,
                "type": "1",
                "keyword": keyword,
            }
            continue
        cur.execute(
            """
            SELECT YCURVECODE, YCURVENAME, YCURVETYPE
            FROM (
                SELECT DISTINCT YCURVECODE, YCURVENAME, YCURVETYPE
                FROM TQ_QT_YIELDCURVE
                WHERE TRADEDATE = :trade_date
                  AND INSTR(YCURVENAME, :keyword) > 0
                  AND YCURVETYPE = '1'
                  AND ROWNUM <= 2000
            )
            WHERE ROWNUM = 1
            """,
            {"trade_date": trade_date, "keyword": keyword},
        )
        row = cur.fetchone()
        if row:
            resolved[key] = {
                "code": row[0],
                "name": row[1],
                "type": row[2],
                "keyword": keyword,
            }
    return resolved


def fetch_curve_series(
    conn,
    curve_codes: dict[str, dict],
    start_date: str,
    end_date: str,
    tenors: Iterable[int | float] | None = None,
) -> dict[str, dict[float, dict[str, float]]]:
    result: dict[str, dict[float, dict[str, float]]] = {key: {} for key in curve_codes}
    start_ymd = yyyymmdd(start_date)
    end_ymd = yyyymmdd(end_date)
    for chunk_start, chunk_end in _calendar_chunks(start_ymd, end_ymd):
        chunk = _fetch_curve_rows(
            conn,
            curve_codes,
            "TRADEDATE >= :start_date AND TRADEDATE <= :end_date",
            {"start_date": chunk_start, "end_date": chunk_end},
            tenors=tenors,
        )
        for key, by_tenor in chunk.items():
            target = result.setdefault(key, {})
            for tenor, values in by_tenor.items():
                target.setdefault(tenor, {}).update(values)
    return result


def fetch_curve_series_for_dates(
    conn,
    curve_codes: dict[str, dict],
    dates: Iterable[str],
    tenors: Iterable[int | float] | None = None,
) -> dict[str, dict[float, dict[str, float]]]:
    dates = [yyyymmdd(d) for d in dict.fromkeys(dates) if d]
    result: dict[str, dict[float, dict[str, float]]] = {key: {} for key in curve_codes}
    if not dates:
        return result
    date_binds = {f"d{i}": d for i, d in enumerate(dates)}
    placeholders = ",".join(f":d{i}" for i in range(len(dates)))
    return _fetch_curve_rows(
        conn,
        curve_codes,
        f"TRADEDATE IN ({placeholders})",
        date_binds,
        tenors=tenors,
    )


def fetch_bond_yields(conn, codes: Iterable[str], trade_date: str, batch_size: int = 300) -> dict[str, float | None]:
    codes = [c for c in dict.fromkeys(codes) if c]
    values = {c: None for c in codes}
    cur = conn.cursor()
    for start in range(0, len(codes), batch_size):
        batch = codes[start:start + batch_size]
        binds = {f"c{i}": code for i, code in enumerate(batch)}
        placeholders = ",".join(f":c{i}" for i in range(len(batch)))
        sql = f"""
            SELECT SYMBOL, YIELD
            FROM TQ_BD_BONDYIELDS
            WHERE ESTMATEDATE = :trade_date
              AND SYMBOL IN ({placeholders})
              AND ISVALID = 1
              AND ROWNUM <= :limit
        """
        binds["trade_date"] = trade_date
        binds["limit"] = max(len(batch) * 5, 10)
        cur.execute(sql, binds)
        for symbol, value in cur.fetchall():
            if symbol in values and value is not None:
                values[symbol] = float(value)
    return values


def fetch_bond_yields_by_secode(conn, secodes: Iterable[str], trade_date: str, batch_size: int = 300) -> dict[str, float | None]:
    secodes = [c for c in dict.fromkeys(secodes) if c]
    values = {c: None for c in secodes}
    cur = conn.cursor()
    for start in range(0, len(secodes), batch_size):
        batch = secodes[start:start + batch_size]
        binds = {f"c{i}": code for i, code in enumerate(batch)}
        placeholders = ",".join(f":c{i}" for i in range(len(batch)))
        sql = f"""
            SELECT SECODE, YIELD
            FROM TQ_BD_BONDYIELDS
            WHERE ESTMATEDATE = :trade_date
              AND SECODE IN ({placeholders})
              AND ISVALID = 1
              AND ROWNUM <= :limit
        """
        binds["trade_date"] = trade_date
        binds["limit"] = max(len(batch) * 5, 10)
        cur.execute(sql, binds)
        for secode, value in cur.fetchall():
            if secode in values and value is not None:
                values[secode] = float(value)
    return values


def fetch_shclest_yields(conn, codes: Iterable[str], trade_date: str) -> dict[str, float]:
    """Fetch ChinaBond clearing-estimate yields from the TDATE-indexed table.

    TQ_BD_BONDYIELDS has no visible index on date/code in this schema and is too
    slow for the full spread monitor universe. TQ_BD_SHCLEST is indexed by TDATE,
    so reading one date slice and filtering locally is much faster.
    """
    code_to_raw = {
        str(code or "").strip().upper().split(".")[0]: str(code or "").strip().upper()
        for code in codes
        if code
    }
    if not code_to_raw:
        return {}
    cur = conn.cursor()
    cur.execute(
        """
        SELECT SYMBOL, AVG(DISTINCT YIELD)
        FROM TQ_BD_SHCLEST
        WHERE TDATE = :trade_date
          AND ISVALID = 1
          AND YIELD IS NOT NULL
        GROUP BY SYMBOL
        """,
        {"trade_date": trade_date},
    )
    values: dict[str, float] = {}
    for symbol, value in cur.fetchall():
        key = str(symbol or "").strip().upper()
        raw = code_to_raw.get(key)
        if raw and value is not None:
            values[raw] = float(value)
    return values


def fetch_shclest_yields_by_secode(conn, secodes: Iterable[str], trade_date: str, batch_size: int = 300) -> dict[str, float]:
    """Fetch ChinaBond valuation yields.

    BESTIMATE is the indexed ChinaBond valuation source in this database.
    DATASOURCE=1 matches the expected ChinaBond yield.
    """
    secodes = [str(code or "").strip() for code in dict.fromkeys(secodes) if code]
    if not secodes:
        return {}
    cur = conn.cursor()
    values: dict[str, float] = {}
    for start in range(0, len(secodes), batch_size):
        batch = secodes[start:start + batch_size]
        binds = {f"c{i}": code for i, code in enumerate(batch)}
        placeholders = ",".join(f":c{i}" for i in range(len(batch)))
        binds["trade_date"] = trade_date
        cur.execute(
            f"""
            SELECT b.SECODE, e.YIELD
            FROM BESTIMATE e
            JOIN TQ_BD_NEWESTBASICINFO b
              ON b.SYMBOL = e.SYMBOL
             AND b.ISVALID = 1
            WHERE e.TDATE = :trade_date
              AND b.SECODE IN ({placeholders})
              AND e.DATASOURCE = 1
              AND e.YIELD IS NOT NULL
            """,
            binds,
        )
        for secode, value in cur.fetchall():
            if secode and value is not None:
                values[str(secode)] = float(value)
    return values


def _wind_exchange_codes(raw_code: str) -> tuple[str, ...]:
    suffix = str(raw_code or "").strip().upper().rsplit(".", 1)
    if len(suffix) != 2:
        return ()
    return {
        "SH": ("001002", "001006"),
        "SZ": ("001003", "001007"),
        "IB": ("001005", "001007"),
    }.get(suffix[1], ())


def _valuation_type_rank(valuation_type: str | None) -> int:
    text = str(valuation_type or "").strip()
    if text == "2":
        return 0
    if text == "1":
        return 1
    return 2


def _select_cnbd_yield(rows: list[tuple[str, str | None, str | None, float]]) -> float:
    # DATASOURCE=5 + VALUATIONTYPE=1 stores callable/exercise valuation rows
    # for examples like 21天投债01. Other DATASOURCE=5 rows are not a safe
    # substitute for non-callable bonds, so fall back to DATASOURCE=1.
    exercise_rows = [
        row for row in rows
        if str(row[1] or "").strip() == "5" and str(row[2] or "").strip() == "1"
    ]
    if exercise_rows:
        exercise_rows.sort(key=lambda row: row[0])
        return exercise_rows[0][3]
    datasource_one_rows = [row for row in rows if str(row[1] or "").strip() == "1"]
    selected_rows = datasource_one_rows or rows
    selected_rows.sort(key=lambda row: (_valuation_type_rank(row[2]), row[0]))
    return selected_rows[0][3]


def fetch_cnbd_yields_by_symbol(conn, symbols: Iterable[str], trade_date: str, batch_size: int = 300) -> dict[str, float]:
    raw_codes = [str(symbol or "").strip().upper() for symbol in dict.fromkeys(symbols) if symbol]
    code_meta = {
        raw: {"bare": raw.split(".", 1)[0], "exchanges": _wind_exchange_codes(raw)}
        for raw in raw_codes
    }
    wanted = {meta["bare"] for meta in code_meta.values()}
    if not wanted:
        return {}
    cur = conn.cursor()
    cur.execute(
        """
        SELECT b.SYMBOL, b.EXCHANGE, e.YIELD, e.DATASOURCE, e.VALUATIONTYPE
        FROM TQ_QT_CBESTIMATE e
        JOIN TQ_BD_NEWESTBASICINFO b
          ON b.SECODE = e.SECODE
         AND b.ISVALID = 1
        WHERE e.TRADEDATE = :trade_date
          AND e.DATASOURCE IN ('1', '5')
          AND e.ISVALID = 1
          AND e.YIELD IS NOT NULL
        """,
        {"trade_date": str(trade_date)},
    )
    candidates: dict[str, list[tuple[str, str | None, str | None, float]]] = {}
    for symbol, exchange, value, datasource, valuation_type in cur.fetchall():
        key = str(symbol or "").strip().upper()
        if key in wanted and value is not None:
            candidates.setdefault(key, []).append((
                str(exchange or "").strip(),
                datasource,
                valuation_type,
                float(value),
            ))
    values: dict[str, float] = {}
    for raw, meta in code_meta.items():
        rows = candidates.get(meta["bare"], [])
        if not rows:
            continue
        exchanges = meta["exchanges"]
        matched = [row for row in rows if not exchanges or row[0] in exchanges]
        selected_rows = matched or rows
        values[raw] = _select_cnbd_yield(selected_rows)
    return values


def resolve_bond_codes(conn, raw_codes: Iterable[str], batch_size: int = 500) -> dict[str, dict]:
    """Map Wind-style bond codes to Juyuan identifiers before touching large yield tables."""
    raw_codes = [str(c or "").strip().upper() for c in dict.fromkeys(raw_codes) if c]
    bare_codes = {c.split(".")[0]: c for c in raw_codes}
    resolved: dict[str, dict] = {}
    cur = conn.cursor()
    keys = list(bare_codes)
    table_defs = [
        ("TQ_BD_NEWESTBASICINFO", ("SYMBOL", "SELFDEFCODE")),
        ("TQ_BD_BASICINFO", ("SYMBOL", "SELFDEFCODE", "CBSKCODE")),
    ]
    for table, code_cols in table_defs:
        missing = [k for k in keys if bare_codes[k] not in resolved]
        if not missing:
            break
        for start in range(0, len(missing), batch_size):
            batch = missing[start:start + batch_size]
            binds = {f"c{i}": code for i, code in enumerate(batch)}
            placeholders = ",".join(f":c{i}" for i in range(len(batch)))
            predicates = " OR ".join(f"{col} IN ({placeholders})" for col in code_cols)
            select_cols = ", ".join(code_cols)
            sql = f"""
                SELECT SECODE, SYMBOL, SELFDEFCODE, EXCHANGE, BONDNAME, {select_cols}
                FROM {table}
                WHERE ({predicates})
                  AND ISVALID = 1
                  AND ROWNUM <= :limit
            """
            binds["limit"] = max(len(batch) * 4, 20)
            cur.execute(sql, binds)
            for row in cur.fetchall():
                secode, symbol, selfdef, exchange, name, *candidates = row
                for candidate in candidates:
                    key = str(candidate or "").strip().upper()
                    if key in bare_codes:
                        raw = bare_codes[key]
                        resolved.setdefault(raw, {
                            "secode": str(secode) if secode is not None else "",
                            "symbol": str(symbol) if symbol is not None else "",
                            "exchange": str(exchange) if exchange is not None else "",
                            "name": str(name) if name is not None else "",
                            "source_table": table,
                        })
    return resolved


def discover_benchmark(conn, keyword: str) -> dict | None:
    if config.BENCHMARK_CODE:
        return {"table": "CONFIG", "code": config.BENCHMARK_CODE, "name": keyword}
    cur = conn.cursor()
    # Prefer small/security master tables before quote tables.
    for table, name_col, code_col in [
        ("SECURITYCODE", "SNAME", "SYMBOL"),
        ("IDCOMPT", "INAME", "SYMBOL"),
    ]:
        try:
            cur.execute(
                f"""
                SELECT {code_col}, {name_col}
                FROM FINCHINA.{table}
                WHERE INSTR({name_col}, :keyword) > 0
                  AND ROWNUM <= 20
                """,
                {"keyword": keyword},
            )
            rows = cur.fetchall()
            if rows:
                code, name = rows[0]
                return {"table": table, "code": code, "name": name}
        except Exception:
            continue
    return None


def fetch_benchmark_close(conn, start_date: str, end_date: str, keyword: str) -> list[dict]:
    benchmark = discover_benchmark(conn, keyword)
    if not benchmark:
        return []
    code = benchmark["code"]
    candidates = [
        ("IBBONDINDEX", "TDATE", "SYMBOL", "DCLOSE"),
        ("CHDQUOTE", "TDATE", "SYMBOL", "TCLOSE"),
        ("FQUOTE", "TDATE", "SYMBOL", "TCLOSE"),
    ]
    cur = conn.cursor()
    for table, date_col, code_col, close_col in candidates:
        try:
            cur.execute(
                f"""
                SELECT {date_col}, {close_col}
                FROM FINCHINA.{table}
                WHERE {code_col} = :code
                  AND {date_col} >= :start_date
                  AND {date_col} <= :end_date
                  AND ROWNUM <= 5000
                ORDER BY {date_col}
                """,
                {"code": code, "start_date": int(start_date), "end_date": int(end_date)},
            )
            rows = [
                {"date": str(d), "close": float(v)}
                for d, v in cur.fetchall()
                if d is not None and v is not None
            ]
            if rows:
                for row in rows:
                    row["name"] = benchmark["name"]
                    row["code"] = code
                return rows
        except Exception:
            continue
    return []
