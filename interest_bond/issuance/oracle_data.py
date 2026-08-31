from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import oracledb

from . import config
from .classification import normalize_text


_CLIENT_INITIALIZED = False


def _init_client() -> None:
    global _CLIENT_INITIALIZED
    if _CLIENT_INITIALIZED:
        return
    if config.ORACLE_CLIENT and os.path.isdir(config.ORACLE_CLIENT):
        try:
            oracledb.init_oracle_client(lib_dir=config.ORACLE_CLIENT)
        except Exception as exc:
            if "already" not in str(exc).lower():
                raise
    _CLIENT_INITIALIZED = True


def connect():
    _init_client()
    return oracledb.connect(
        user=config.ORACLE_USER,
        password=config.ORACLE_PASSWORD,
        dsn=config.ORACLE_DSN,
    )


def _ymd(value: date) -> str:
    return value.strftime("%Y%m%d")


def fetch_update_payload(
    *,
    mode: str,
    last_success_date: date | None,
    include_progress_history: bool = False,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    current_year = today.year
    tomorrow = today + timedelta(days=1)
    year_start = date(current_year, 1, 1)
    if include_progress_history or mode == "full" or not last_success_date:
        start = date(config.HISTORY_START_YEAR, 1, 1)
        full_history = True
    else:
        start = max(year_start, last_success_date - timedelta(days=config.INCREMENTAL_LOOKBACK_DAYS))
        full_history = False

    with connect() as conn:
        cur = conn.cursor()
        cur.arraysize = 5000
        if include_progress_history:
            treasury_filter = ""
            local_filter = ""
        elif full_history:
            treasury_filter = "AND (b.MATURITYYEAR >= 20 OR i.ISSBEGDATE >= :year_start)"
            local_filter = (
                "AND ((BOND_MATURITY_UNIT = 1 AND BOND_MATURITY_VALUE >= 20) "
                "OR ISSUE_START_DATE >= :year_start_ts)"
            )
        else:
            treasury_filter = ""
            local_filter = ""

        treasury_binds = {
            "start_date": _ymd(start),
            "end_date": _ymd(tomorrow),
        }
        if full_history and not include_progress_history:
            treasury_binds["year_start"] = _ymd(year_start)
        cur.execute(
            f"""
            WITH x AS (
              SELECT i.ISSUEID, i.ACTISSAMT, i.PLANISSAMT, i.ISSBEGDATE,
                     b.BONDNAME, b.MATURITYYEAR,
                     ROW_NUMBER() OVER (PARTITION BY i.ISSUEID ORDER BY i.SECODE) rn
              FROM TQ_BD_ISSUE i
              JOIN TQ_BD_BASICINFO b
                ON b.SECODE = i.SECODE AND b.ISVALID = 1
              WHERE i.ISVALID = 1
                AND b.BONDTYPE1 = '1'
                AND b.BONDTYPE2 = '111'
                AND i.ISSBEGDATE >= :start_date
                AND i.ISSBEGDATE < :end_date
                {treasury_filter}
            )
            SELECT ISSUEID, NVL(ACTISSAMT, PLANISSAMT), ISSBEGDATE, BONDNAME, MATURITYYEAR
            FROM x WHERE rn = 1
            """,
            treasury_binds,
        )
        treasury_issues = [
            {
                "issue_id": str(issue_id),
                "amount": float(amount or 0),
                "issue_date": datetime.strptime(str(issue_date)[:8], "%Y%m%d").date(),
                "name": normalize_text(name),
                "term_years": float(term) if term is not None else None,
            }
            for issue_id, amount, issue_date, name, term in cur.fetchall()
            if issue_date and name and amount is not None
        ]

        # 政金债：国家开发银行、中国进出口银行、中国农业发展银行发行的金融债。
        cur.execute(
            f"""
            WITH x AS (
              SELECT i.ISSUEID, i.ACTISSAMT, i.PLANISSAMT, i.ISSBEGDATE,
                     b.BONDNAME, b.ISSUERNAME,
                     ROW_NUMBER() OVER (PARTITION BY i.ISSUEID ORDER BY i.SECODE) rn
              FROM TQ_BD_ISSUE i
              JOIN TQ_BD_BASICINFO b
                ON b.SECODE = i.SECODE AND b.ISVALID = 1
              WHERE i.ISVALID = 1
                AND b.ISSUERNAME IN ('国家开发银行', '中国进出口银行', '中国农业发展银行')
                AND i.ISSBEGDATE >= :start_date
                AND i.ISSBEGDATE < :end_date
            )
            SELECT ISSUEID, NVL(ACTISSAMT, PLANISSAMT), ISSBEGDATE, BONDNAME, ISSUERNAME
            FROM x WHERE rn = 1
            """,
            {"start_date": treasury_binds["start_date"], "end_date": treasury_binds["end_date"]},
        )
        policy_financial_issues = [
            {
                "issue_id": str(issue_id),
                "amount": float(amount or 0),
                "issue_date": datetime.strptime(str(issue_date)[:8], "%Y%m%d").date(),
                "name": normalize_text(name),
                "issuer": normalize_text(issuer),
            }
            for issue_id, amount, issue_date, name, issuer in cur.fetchall()
            if issue_date and name and issuer and amount is not None
        ]

        local_binds = {
            "start_ts": datetime.combine(start, datetime.min.time()),
            "end_ts": datetime.combine(tomorrow, datetime.min.time()),
        }
        if full_history and not include_progress_history:
            local_binds["year_start_ts"] = datetime.combine(year_start, datetime.min.time())
        cur.execute(
            f"""
            SELECT ISSUE_START_DATE, ACTUAL_ISSUE_AMOUNT,
                   DBMS_LOB.SUBSTR(BOND_FULL_NAME, 700, 1),
                   DBMS_LOB.SUBSTR(CAPITAL_COLLECTION_USAGE, 3500, 1),
                   DBMS_LOB.SUBSTR(BOND_CODE, 300, 1),
                   BOND_UNI_CODE, BOND_MATURITY_VALUE, BOND_MATURITY_UNIT,
                   ACTUAL_MATURITY_DATE, UPDATE_TIME
            FROM DM.T_BOND_BASIC_INFO
            WHERE BOND_TYPE = 2
              AND DELETED = 0
              AND ISSUE_START_DATE >= :start_ts
              AND ISSUE_START_DATE < :end_ts
              AND ACTUAL_ISSUE_AMOUNT > 0
              {local_filter}
            """,
            local_binds,
        )
        local_raw = cur.fetchall()

        # 首次补齐历史时拉取2021年以来到期事件；日常更新只重取当年截至数据日。
        maturity_start = date(config.HISTORY_START_YEAR, 1, 1) if include_progress_history else year_start
        cur.execute(
            """
            WITH d AS (
              SELECT BONDNAME, MATURITYDATE, MAX(ACTISSAMT) AMT
              FROM TQ_BD_NEWESTBASICINFO
              WHERE ISVALID = 1
                AND BONDTYPE1 = '1'
                AND BONDTYPE2 = '111'
                AND MATURITYDATE >= :start_date
                AND MATURITYDATE < :end_date
              GROUP BY BONDNAME, MATURITYDATE
            )
            SELECT BONDNAME, MATURITYDATE, AMT FROM d
            """,
            {"start_date": _ymd(maturity_start), "end_date": _ymd(tomorrow)},
        )
        maturities = [
            {
                "name": normalize_text(name),
                "maturity_date": datetime.strptime(str(maturity_date)[:8], "%Y%m%d").date(),
                "amount": float(amount or 0),
            }
            for name, maturity_date, amount in cur.fetchall()
            if name and maturity_date and amount is not None and "特别国债" not in normalize_text(name)
        ]

    grouped: dict[tuple[int, str], dict] = {}
    for (
        issue_date,
        amount_wan,
        name,
        purpose,
        code,
        uni_code,
        term_value,
        term_unit,
        maturity_date,
        update_time,
    ) in local_raw:
        if not issue_date or not name or amount_wan is None:
            continue
        bond_name = normalize_text(name)
        key = (issue_date.year, bond_name)
        rec = grouped.setdefault(
            key,
            {
                "issue_date": issue_date.date(),
                "amount": 0.0,
                "name": bond_name,
                "purposes": [],
                "codes": set(),
                "uni_codes": set(),
                "term_value": term_value,
                "term_unit": term_unit,
                "maturity_date": maturity_date.date() if maturity_date else None,
                "update_time": update_time,
            },
        )
        rec["issue_date"] = min(rec["issue_date"], issue_date.date())
        rec["amount"] = max(rec["amount"], float(amount_wan) / 10000.0)
        if purpose:
            rec["purposes"].append(normalize_text(purpose))
        if code:
            rec["codes"].add(normalize_text(code))
        if uni_code is not None:
            rec["uni_codes"].add(str(uni_code))
        if update_time and (not rec["update_time"] or update_time > rec["update_time"]):
            rec["update_time"] = update_time
        if rec["term_value"] is None and term_value is not None:
            rec["term_value"] = term_value
            rec["term_unit"] = term_unit
        if rec["maturity_date"] is None and maturity_date:
            rec["maturity_date"] = maturity_date.date()

    local_issues = []
    for rec in grouped.values():
        rec["purpose"] = " ".join(dict.fromkeys(rec.pop("purposes")))
        rec["codes"] = sorted(rec["codes"])
        rec["uni_codes"] = sorted(rec["uni_codes"])
        local_issues.append(rec)

    return {
        "mode": mode,
        "full_history": full_history,
        "include_progress_history": include_progress_history,
        "current_year": current_year,
        "as_of_date": today,
        "query_start": start,
        "treasury_issues": treasury_issues,
        "policy_financial_issues": policy_financial_issues,
        "local_issues": local_issues,
        "treasury_maturities": maturities,
    }
