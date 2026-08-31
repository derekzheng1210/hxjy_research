"""Read-only MCP server for Juyuan Oracle market data."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import os
import re
from typing import Any, Iterable

import oracledb
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("Juyuan Oracle Market Data")

# Keep mappings explicit: resolving curve names through INSTR(YCURVENAME, ...) is
# too slow for normal MCP use and could select a different curve after a rename.
CURVES: dict[str, dict[str, str]] = {
    "国开债": {"code": "269", "type": "1", "name": "中债国开债收益率曲线"},
    "中短票AAA+": {"code": "260", "type": "1", "name": "中债中短期票据收益率曲线(AAA+)"},
    "中短票AAA": {"code": "214", "type": "1", "name": "中债中短期票据收益率曲线(AAA)"},
    "中短票AA+": {"code": "216", "type": "1", "name": "中债中短期票据收益率曲线(AA+)"},
    "中短票AA": {"code": "201", "type": "1", "name": "中债中短期票据收益率曲线(AA)"},
    "大行二级资本债": {"code": "428", "type": "1", "name": "中债商业银行二级资本债收益率曲线(AAA-)"},
    "股份行二级资本债": {"code": "429", "type": "1", "name": "中债商业银行二级资本债收益率曲线(AA+)"},
}

DEFAULT_TENORS = list(range(11))
_DATE_RE = re.compile(r"^\d{8}$")
_CLIENT_INITIALIZED = False


def _validate_date(value: str, field: str) -> str:
    normalized = str(value or "").replace("-", "").strip()
    if not _DATE_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be YYYYMMDD.")
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid calendar date.") from exc
    return normalized


def _validate_curves(curves: list[str] | None) -> list[str]:
    selected = curves or ["国开债"]
    unknown = [curve for curve in selected if curve not in CURVES]
    if unknown:
        raise ValueError(f"Unsupported curve(s): {', '.join(unknown)}. Available: {', '.join(CURVES)}")
    return list(dict.fromkeys(selected))


def _validate_tenors(tenors: list[float] | None) -> list[float]:
    selected = DEFAULT_TENORS if tenors is None else tenors
    if not selected:
        raise ValueError("tenors must contain at least one maturity.")
    if len(selected) > 31:
        raise ValueError("At most 31 maturities may be requested.")
    normalized = [float(tenor) for tenor in selected]
    if any(tenor < 0 or tenor > 100 for tenor in normalized):
        raise ValueError("Each maturity must be between 0 and 100 years.")
    return list(dict.fromkeys(normalized))


def _calendar_chunks(start_date: str, end_date: str, days: int = 31) -> Iterable[tuple[str, str]]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=days - 1), end)
        yield current.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")
        current = chunk_end + timedelta(days=1)


def _init_oracle_client() -> None:
    global _CLIENT_INITIALIZED
    if _CLIENT_INITIALIZED:
        return
    client_dir = os.environ.get("JUYUAN_ORACLE_CLIENT", "").strip()
    if client_dir and os.path.isdir(client_dir):
        try:
            oracledb.init_oracle_client(lib_dir=client_dir)
        except Exception as exc:
            if "already" not in str(exc).lower() or "initialized" not in str(exc).lower():
                raise
    _CLIENT_INITIALIZED = True


@contextmanager
def _connect():
    required = ("JUYUAN_DB_USER", "JUYUAN_DB_PASSWORD", "JUYUAN_DB_DSN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing required environment variable(s): " + ", ".join(missing))
    _init_oracle_client()
    conn = oracledb.connect(
        user=os.environ["JUYUAN_DB_USER"],
        password=os.environ["JUYUAN_DB_PASSWORD"],
        dsn=os.environ["JUYUAN_DB_DSN"],
    )
    try:
        yield conn
    finally:
        conn.close()


def _latest_curve_date(conn, curve_code: str) -> str | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(TRADEDATE)
        FROM TQ_QT_YIELDCURVE
        WHERE YCURVECODE = :curve_code
          AND YCURVETYPE = '1'
          AND MATURITY = 1
          AND ISVALID = 1
        """,
        {"curve_code": curve_code},
    )
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def _latest_valuation_date(conn) -> str | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(TRADEDATE)
        FROM TQ_QT_CBESTIMATE
        WHERE DATASOURCE = '1'
          AND ISVALID = 1
          AND YIELD IS NOT NULL
        """
    )
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def _curve_rows(conn, selected_curves: list[str], start_date: str, end_date: str, tenors: list[float]) -> dict[str, Any]:
    code_to_curve = {CURVES[name]["code"]: name for name in selected_curves}
    code_binds = {f"c{i}": code for i, code in enumerate(code_to_curve)}
    tenor_binds = {f"t{i}": tenor for i, tenor in enumerate(tenors)}
    code_placeholders = ", ".join(f":c{i}" for i in range(len(code_binds)))
    tenor_placeholders = ", ".join(f":t{i}" for i in range(len(tenor_binds)))
    data: dict[str, dict[str, dict[str, float]]] = {}
    cur = conn.cursor()
    for chunk_start, chunk_end in _calendar_chunks(start_date, end_date):
        cur.execute(
            f"""
            SELECT TRADEDATE, YCURVECODE, MATURITY, YIELD
            FROM TQ_QT_YIELDCURVE
            WHERE TRADEDATE >= :start_date
              AND TRADEDATE <= :end_date
              AND YCURVECODE IN ({code_placeholders})
              AND YCURVETYPE = '1'
              AND MATURITY IN ({tenor_placeholders})
              AND ISVALID = 1
            ORDER BY TRADEDATE, YCURVECODE, MATURITY
            """,
            {**code_binds, **tenor_binds, "start_date": chunk_start, "end_date": chunk_end},
        )
        for trade_date, curve_code, maturity, yield_value in cur.fetchall():
            if maturity is None or yield_value is None:
                continue
            curve_name = code_to_curve.get(str(curve_code))
            if curve_name is None:
                continue
            date_key = str(trade_date)
            data.setdefault(date_key, {}).setdefault(curve_name, {})[str(float(maturity)).rstrip("0").rstrip(".")] = float(yield_value)
    return data


@mcp.tool()
def get_latest_market_dates() -> dict[str, str | None]:
    """Return the newest valid ChinaBond curve and bond-valuation dates in Oracle."""
    with _connect() as conn:
        return {
            "curve_date": _latest_curve_date(conn, CURVES["国开债"]["code"]),
            "cnbd_valuation_date": _latest_valuation_date(conn),
        }


@mcp.tool()
def get_yield_curve(
    curves: list[str] | None = None,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    tenors: list[float] | None = None,
) -> dict[str, Any]:
    """Get ChinaBond curve yields (percent) by date and maturity.

    Use `trade_date` for a snapshot. Use both `start_date` and `end_date` for
    history; the range is limited to 366 calendar days and read in 31-day chunks.
    Available curves: 国开债, 中短票AAA+, 中短票AAA, 中短票AA+, 中短票AA,
    大行二级资本债, 股份行二级资本债.
    """
    if trade_date and (start_date or end_date):
        raise ValueError("Use either trade_date or start_date/end_date, not both.")
    if bool(start_date) != bool(end_date):
        raise ValueError("start_date and end_date must be supplied together.")
    selected_curves = _validate_curves(curves)
    selected_tenors = _validate_tenors(tenors)
    with _connect() as conn:
        if trade_date:
            start = end = _validate_date(trade_date, "trade_date")
        elif start_date and end_date:
            start = _validate_date(start_date, "start_date")
            end = _validate_date(end_date, "end_date")
            if start > end:
                raise ValueError("start_date must not be after end_date.")
            if (datetime.strptime(end, "%Y%m%d") - datetime.strptime(start, "%Y%m%d")).days > 365:
                raise ValueError("A history request may span at most 366 calendar days.")
        else:
            latest = _latest_curve_date(conn, CURVES[selected_curves[0]]["code"])
            if not latest:
                return {"dates": [], "curves": selected_curves, "data": {}}
            start = end = latest
        data = _curve_rows(conn, selected_curves, start, end, selected_tenors)
    return {
        "start_date": start,
        "end_date": end,
        "curves": [{"name": name, "code": CURVES[name]["code"]} for name in selected_curves],
        "tenors_years": selected_tenors,
        "yield_unit": "percent",
        "data": data,
    }


def _exchange_codes(wind_code: str) -> tuple[str, ...]:
    suffix = wind_code.rsplit(".", 1)
    if len(suffix) != 2:
        return ()
    return {"SH": ("001002", "001006"), "SZ": ("001003", "001007"), "IB": ("001005", "001007")}.get(suffix[1], ())


@mcp.tool()
def get_cnbd_valuations(codes: list[str], trade_date: str | None = None) -> dict[str, Any]:
    """Get ChinaBond valuation yields (percent) for Wind-style bond codes.

    Data is from TQ_QT_CBESTIMATE, not the Shanghai Clearing House table.
    The selection uses DATASOURCE='1', prefers VALUATIONTYPE='1', and never
    averages multiple valuation rows. If trade_date is omitted, the latest
    available ChinaBond valuation date is used.
    """
    raw_codes = list(dict.fromkeys(str(code or "").strip().upper() for code in codes if str(code or "").strip()))
    if not raw_codes:
        raise ValueError("codes must contain at least one bond code.")
    if len(raw_codes) > 100:
        raise ValueError("At most 100 bond codes may be requested at once.")
    if any(not re.fullmatch(r"[A-Z0-9]+(?:\.(?:IB|SH|SZ))?", code) for code in raw_codes):
        raise ValueError("Bond codes must be alphanumeric Wind-style codes, for example 102681601.IB.")
    bare_to_raw: dict[str, list[str]] = {}
    for code in raw_codes:
        bare_to_raw.setdefault(code.split(".", 1)[0], []).append(code)
    with _connect() as conn:
        selected_date = _validate_date(trade_date, "trade_date") if trade_date else _latest_valuation_date(conn)
        if not selected_date:
            return {"trade_date": None, "yield_unit": "percent", "valuations": []}
        binds = {"trade_date": selected_date, **{f"c{i}": code for i, code in enumerate(bare_to_raw)}}
        placeholders = ", ".join(f":c{i}" for i in range(len(bare_to_raw)))
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT b.SYMBOL, b.EXCHANGE, b.BONDSNAME, e.YIELD, e.NETPRICE,
                   e.DIRTYPRICE, e.DATASOURCE, e.VALUATIONTYPE
            FROM TQ_QT_CBESTIMATE e
            JOIN TQ_BD_NEWESTBASICINFO b
              ON b.SECODE = e.SECODE
             AND b.ISVALID = 1
            WHERE e.TRADEDATE = :trade_date
              AND b.SYMBOL IN ({placeholders})
              AND e.DATASOURCE = '1'
              AND e.ISVALID = 1
              AND e.YIELD IS NOT NULL
            """,
            binds,
        )
        candidates: dict[str, list[dict[str, Any]]] = {}
        for symbol, exchange, name, yield_value, net_price, dirty_price, datasource, valuation_type in cur.fetchall():
            bare = str(symbol).strip().upper()
            candidates.setdefault(bare, []).append({
                "symbol": bare,
                "exchange": str(exchange or ""),
                "bond_name": str(name or ""),
                "yield": float(yield_value),
                "net_price": float(net_price) if net_price is not None else None,
                "dirty_price": float(dirty_price) if dirty_price is not None else None,
                "data_source": str(datasource),
                "valuation_type": str(valuation_type or ""),
            })
    valuations: list[dict[str, Any]] = []
    for raw in raw_codes:
        bare = raw.split(".", 1)[0]
        rows = candidates.get(bare, [])
        allowed_exchanges = _exchange_codes(raw)
        matched = [row for row in rows if not allowed_exchanges or row["exchange"] in allowed_exchanges]
        choices = matched or rows
        choices.sort(key=lambda row: (0 if row["valuation_type"] == "1" else 1, row["exchange"]))
        if choices:
            valuations.append({"requested_code": raw, **choices[0]})
        else:
            valuations.append({"requested_code": raw, "status": "not_found"})
    return {"trade_date": selected_date, "yield_unit": "percent", "valuations": valuations}


if __name__ == "__main__":
    mcp.run()
