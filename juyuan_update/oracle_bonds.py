from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from . import config
from .unified_excel import (
    load_bond_static,
    load_json,
    normalize_bond_code,
    normalize_rating,
    save_bond_static,
    write_json,
)


# 财汇 TQ_BD_PROBASICINFO/TQ_BD_NEWESTBASICINFO 的 BONDTYPE2 口径。
# 511/512 为政策性银行债，522 为商业银行混合资本债，1011 为项目收益票据，均不纳入。
INCLUDED_BOND_TYPE2 = (
    "311",  # 汇金债券
    "321",  # 中国铁道部一般债券
    "521",  # 商业银行普通金融债
    "523",  # 商业银行次级金融债
    "531",  # 保险公司普通金融债
    "532",  # 保险公司次级金融债
    "541",  # 证券公司普通金融债
    "542",  # 证券公司次级金融债
    "543",  # 证券公司短期融资券
    "590",  # 其他金融机构金融债
    "611",  # 中央企业债券
    "612",  # 地方企业债券
    "621",  # 普通公司债
    "631",  # 一般短期融资券
    "632",  # 超短期融资债券
    "641",  # 中期票据
)

EXCLUDED_POLICY_BANKS = ("国家开发银行", "中国进出口银行", "中国农业发展银行")
MIN_REMAINING_TERM = float(os.environ.get("JUYUAN_BOND_MIN_REMAINING_TERM", "0.2"))
FULL_REFRESH_DAYS = max(1, int(os.environ.get("JUYUAN_BOND_FULL_REFRESH_DAYS", "7")))
MAX_SWITCH_DIFF_RATIO = float(os.environ.get("JUYUAN_BOND_MAX_SWITCH_DIFF_RATIO", "0.10"))
FORCE_SWITCH = os.environ.get("JUYUAN_BOND_FORCE_SWITCH", "0") == "1"

_DATE_RE = re.compile(r"^\d{8}$")
_OPTION_TERM_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*\+")
_PERPETUAL_RE = re.compile(r"\+\s*N\b", re.I)


def _yyyymmdd(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value or "").strip().replace("-", "")[:8]
    return text if _DATE_RE.match(text) else ""


def _date(value) -> date | None:
    text = _yyyymmdd(value)
    if not text or text <= "19010101":
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def is_perpetual_bond(name: str = "", option_memo: str = "", exercise_type: str = "") -> bool:
    text = " ".join(str(value or "") for value in (name, option_memo, exercise_type))
    return "永续" in text or bool(_PERPETUAL_RE.search(text))


def effective_maturity_date(
    *,
    as_of: date,
    start_date=None,
    maturity_date=None,
    put_date=None,
    redeem_date=None,
    option_memo: str = "",
) -> tuple[date | None, str]:
    """Return the first exercise date, not the legal final maturity.

    Direct Oracle PUTDATE/REDEEMDATE fields win.  Some 3+2 bonds only expose
    the structure in CVTBDEXPIREMEMP, so the first leg is derived from STARTDATE.
    A past exercise date is deliberately not replaced by the final maturity:
    the requested universe treats 3+2 as the three-year exercise term.
    """
    option_dates = [d for d in (_date(put_date), _date(redeem_date)) if d]
    if option_dates:
        return min(option_dates), "oracle_exercise_date"

    match = _OPTION_TERM_RE.search(str(option_memo or ""))
    start = _date(start_date)
    if match and start:
        years = float(match.group(1))
        return start + timedelta(days=round(years * 365)), "option_memo_first_leg"

    return _date(maturity_date), "maturity_date"


def remaining_term(effective_date: date | None, as_of: date) -> float | None:
    if not effective_date:
        return None
    return round((effective_date - as_of).days / 365.0, 4)


def _wind_code(symbol, exchange) -> str:
    bare = str(symbol or "").strip().upper()
    if not bare:
        return ""
    # Interbank symbols are commonly 7-9 digits and must remain one .IB bond
    # even when the master table also carries cross-market SECODE rows.
    if len(bare) != 6:
        return normalize_bond_code(bare)
    exchange_text = str(exchange or "").strip()
    suffix = {
        "001005": ".IB",
        "001002": ".SH",
        "001006": ".SH",
        "001003": ".SZ",
        "001007": ".SZ",
    }.get(exchange_text)
    return bare + suffix if suffix else normalize_bond_code(bare)


def _batched(values: list[str], size: int = 500):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _fetch_latest_ratings(conn, secodes: Iterable[str]) -> dict[str, str]:
    secodes = list(dict.fromkeys(str(value) for value in secodes if value))
    ratings: dict[str, tuple[str, str]] = {}
    cur = conn.cursor()
    for batch in _batched(secodes):
        binds = {f"s{i}": value for i, value in enumerate(batch)}
        placeholders = ",".join(f":s{i}" for i in range(len(batch)))
        cur.execute(
            f"""
            SELECT SECODE, STDCREDIT, HIDECREDITDATE
            FROM TQ_BD_NEWHIDECREDIT
            WHERE SECODE IN ({placeholders})
              AND CREDITSOURCE = '1'
              AND HIDECREDITSTATUS = '1'
              AND ISVALID = 1
              AND STDCREDIT IS NOT NULL
            """,
            binds,
        )
        for secode, rating, rating_date in cur.fetchall():
            key = str(secode)
            candidate = (normalize_rating(rating), _yyyymmdd(rating_date))
            if candidate[0] and (key not in ratings or candidate[1] > ratings[key][1]):
                ratings[key] = candidate
    return {key: value[0] for key, value in ratings.items()}


def _candidate_sql(extra_where: str = "", *, restrict_types: bool = True) -> str:
    type_filter = ""
    if restrict_types:
        type_list = ",".join(f"'{value}'" for value in INCLUDED_BOND_TYPE2)
        type_filter = f"AND n.BONDTYPE2 IN ({type_list})"
    return f"""
        SELECT n.SYMBOL, n.EXCHANGE, n.SECODE, n.BONDSNAME, n.COMPNAME,
               n.BONDTYPE2, n.STARTDATE, n.MATURITYDATE,
               n.CVTBDEXPIREMEMP, n.EXERTYPE,
               NULL AS PUTDATE, NULL AS REDEEMDATE,
               n.ISSUBDEBT, n.ISCITYINVERT, n.GUARANTOR,
               n.DATA_DOWNLOAD_TIME, n.ISVALID, n.RAISEMODE, n.CALCAMODE,
               n.ISSUECOMPCODE
        FROM TQ_BD_NEWESTBASICINFO n
        WHERE n.SYMBOL IS NOT NULL
          {type_filter}
          {extra_where}
    """


def _attach_option_dates(conn, rows: list[tuple]) -> list[tuple]:
    """Attach the earliest valid exercise dates without duplicating master rows."""
    secodes = list(dict.fromkeys(str(row[2]) for row in rows if row[2]))
    option_dates: dict[str, tuple[str | None, str | None]] = {}
    cur = conn.cursor()
    for batch in _batched(secodes):
        binds = {f"s{i}": value for i, value in enumerate(batch)}
        placeholders = ",".join(f":s{i}" for i in range(len(batch)))
        cur.execute(
            f"""
            SELECT SECODE, PUTDATE, REDEEMDATE
            FROM TQ_BD_BASICINFO
            WHERE SECODE IN ({placeholders})
              AND ISVALID = 1
            """,
            binds,
        )
        for secode, put_date, redeem_date in cur.fetchall():
            key = str(secode)
            old_put, old_redeem = option_dates.get(key, (None, None))
            put_text = _yyyymmdd(put_date)
            redeem_text = _yyyymmdd(redeem_date)
            if put_text > "19010101" and (old_put is None or put_text < old_put):
                old_put = put_text
            if redeem_text > "19010101" and (old_redeem is None or redeem_text < old_redeem):
                old_redeem = redeem_text
            option_dates[key] = (old_put, old_redeem)
    attached = []
    for row in rows:
        put_date, redeem_date = option_dates.get(str(row[2]), (None, None))
        values = list(row)
        values[10] = put_date
        values[11] = redeem_date
        attached.append(tuple(values))
    return attached


def _fetch_full_rows(conn, as_of: date) -> list[tuple]:
    cur = conn.cursor()
    cur.arraysize = 5000
    cur.execute(
        _candidate_sql(
            """
            AND n.ISVALID = 1
            AND n.RAISEMODE = '1'
            AND n.CALCAMODE = '20'
            AND (n.STARTDATE IS NULL OR n.STARTDATE <= :as_of)
            AND n.MATURITYDATE > :as_of
            AND n.COMPNAME NOT LIKE '%国家开发银行%'
            AND n.COMPNAME NOT LIKE '%中国进出口银行%'
            AND n.COMPNAME NOT LIKE '%中国农业发展银行%'
            """
        ),
        {"as_of": as_of.strftime("%Y%m%d")},
    )
    return _attach_option_dates(conn, cur.fetchall())


def _fetch_entity_types(conn, company_codes: Iterable[str]) -> dict[str, tuple[str, str, str]]:
    company_codes = list(dict.fromkeys(str(value) for value in company_codes if value))
    result: dict[str, tuple[str, str, str]] = {}
    cur = conn.cursor()
    label_by_prefix = {
        "A01": "中央国有企业",
        "A02": "地方国有企业",
        "A04": "民营企业",
        "A05": "集体企业",
        "A06": "外资企业",
        "A07": "公众企业",
    }
    for batch in _batched(company_codes):
        binds = {f"c{i}": value for i, value in enumerate(batch)}
        placeholders = ",".join(f":c{i}" for i in range(len(batch)))
        cur.execute(
            f"""
            SELECT COMPCODE, ORGTYPECODE, SORGTYPECODE
            FROM TQ_COMP_ORGTYPE
            WHERE COMPCODE IN ({placeholders})
              AND CLASSTYPE = 'A'
              AND ISVALID = 1
            """,
            binds,
        )
        for company_code, org_type, sub_type in cur.fetchall():
            key = str(company_code)
            org_text = str(org_type or "")
            sub_text = str(sub_type or "")
            prefix = next((item for item in label_by_prefix if org_text.startswith(item)), "")
            label = label_by_prefix.get(prefix, "其他企业")
            result.setdefault(key, (label, org_text, sub_text))
    return result


def _row_to_bond(
    row: tuple,
    rating: str,
    as_of: date,
    entity_meta: tuple[str, str, str] | None = None,
) -> tuple[dict | None, str | None]:
    (
        symbol, exchange, secode, name, issuer, bond_type2, start_date,
        maturity_date, option_memo, exercise_type, put_date, redeem_date,
        is_subdebt, is_city, guarantor, download_time, is_valid,
        raise_mode, calc_mode, issue_company_code,
    ) = row
    if str(is_valid or "") != "1":
        return None, "invalid"
    if str(raise_mode or "") != "1":
        return None, "not_public"
    if str(calc_mode or "") != "20":
        return None, "not_fixed_rate"
    if str(bond_type2 or "") not in INCLUDED_BOND_TYPE2:
        return None, "bond_type"
    if any(keyword in str(issuer or "") for keyword in EXCLUDED_POLICY_BANKS):
        return None, "policy_bank"
    issue_date = _date(start_date)
    if issue_date and issue_date > as_of:
        return None, "not_issued"
    if not rating:
        return None, "no_implied_rating"
    if is_perpetual_bond(str(name or ""), str(option_memo or ""), str(exercise_type or "")):
        return None, "perpetual"

    effective_date, term_source = effective_maturity_date(
        as_of=as_of,
        start_date=start_date,
        maturity_date=maturity_date,
        put_date=put_date,
        redeem_date=redeem_date,
        option_memo=str(option_memo or ""),
    )
    term = remaining_term(effective_date, as_of)
    if term is None:
        return None, "missing_term"
    if term < MIN_REMAINING_TERM:
        return None, "term_below_minimum"

    code = _wind_code(symbol, exchange)
    entity, entity_type_code, entity_subtype_code = entity_meta or ("其他企业", "", "")
    return {
        "code": code,
        "raw_code": str(symbol or "").strip(),
        "secode": str(secode or ""),
        "name": str(name or "").strip(),
        "term": term,
        "term_source": term_source,
        "effective_maturity_date": effective_date.strftime("%Y-%m-%d") if effective_date else "",
        "issue_date": issue_date.strftime("%Y-%m-%d") if issue_date else "",
        "implied_rating": rating,
        "issuer": str(issuer or "").strip(),
        "entity": entity,
        "entity_type_code": entity_type_code,
        "entity_subtype_code": entity_subtype_code,
        "issue_company_code": str(issue_company_code or ""),
        "ct": "是" if str(is_city or "") == "1" else "否",
        "sub": "是" if str(is_subdebt or "") == "1" else "否",
        "tech": "是" if "科创" in str(name or "") else "否",
        "guarantor": str(guarantor or "").strip(),
        "internal_rating": "",
        "is_holding": False,
        "bond_type2": str(bond_type2 or ""),
        "oracle_download_time": str(download_time or ""),
    }, None


def build_full_oracle_universe(conn, as_of_date: str) -> tuple[list[dict], dict]:
    as_of = datetime.strptime(as_of_date.replace("-", ""), "%Y%m%d").date()
    rows = _fetch_full_rows(conn, as_of)
    ratings = _fetch_latest_ratings(conn, (row[2] for row in rows))
    entities = _fetch_entity_types(conn, (row[19] for row in rows))
    counts: dict[str, int] = {"oracle_rows": len(rows)}
    bonds_by_code: dict[str, dict] = {}
    for row in rows:
        bond, reason = _row_to_bond(
            row,
            ratings.get(str(row[2]), ""),
            as_of,
            entities.get(str(row[19]), None),
        )
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
        elif bond:
            bonds_by_code.setdefault(bond["code"], bond)
    bonds = sorted(bonds_by_code.values(), key=lambda item: item["code"])
    counts["selected"] = len(bonds)
    return bonds, counts


def _fetch_changed_secodes(conn, watermark: str) -> set[str]:
    """Read only recently downloaded master/rating rows between full reconciliations."""
    changed: set[str] = set()
    cur = conn.cursor()
    for table in ("TQ_BD_NEWESTBASICINFO", "TQ_BD_NEWHIDECREDIT"):
        cur.execute(
            f"""
            SELECT SECODE
            FROM {table}
            WHERE DATA_DOWNLOAD_TIME > TO_DATE(:watermark, 'YYYY-MM-DD HH24:MI:SS')
              AND SECODE IS NOT NULL
            """,
            {"watermark": watermark},
        )
        changed.update(str(row[0]) for row in cur.fetchall() if row[0])
    return changed


def _fetch_rows_by_secode(conn, secodes: Iterable[str]) -> dict[str, tuple]:
    secodes = list(dict.fromkeys(str(value) for value in secodes if value))
    rows: dict[str, tuple] = {}
    cur = conn.cursor()
    for batch in _batched(secodes):
        binds = {f"s{i}": value for i, value in enumerate(batch)}
        placeholders = ",".join(f":s{i}" for i in range(len(batch)))
        cur.execute(
            _candidate_sql(
                f"AND n.SECODE IN ({placeholders})",
                restrict_types=False,
            ),
            binds,
        )
        for row in _attach_option_dates(conn, cur.fetchall()):
            rows.setdefault(str(row[2]), row)
    return rows


def build_incremental_oracle_universe(
    conn,
    as_of_date: str,
    current_payload: dict,
) -> tuple[list[dict], dict]:
    as_of = datetime.strptime(as_of_date.replace("-", ""), "%Y%m%d").date()
    watermark = str(current_payload.get("oracle_watermark") or current_payload.get("generated_at") or "")[:19]
    if not watermark:
        return build_full_oracle_universe(conn, as_of_date)

    changed_secodes = _fetch_changed_secodes(conn, watermark)
    changed_rows = _fetch_rows_by_secode(conn, changed_secodes)
    ratings = _fetch_latest_ratings(conn, changed_secodes)
    entities = _fetch_entity_types(conn, (row[19] for row in changed_rows.values()))
    bonds_by_secode = {
        str(item.get("secode") or ""): dict(item)
        for item in current_payload.get("bonds") or []
        if item.get("secode")
    }
    counts = {"incremental_changed_secodes": len(changed_secodes)}

    for secode in changed_secodes:
        row = changed_rows.get(secode)
        if row is None:
            bonds_by_secode.pop(secode, None)
            counts["removed_missing"] = counts.get("removed_missing", 0) + 1
            continue
        bond, reason = _row_to_bond(
            row,
            ratings.get(secode, ""),
            as_of,
            entities.get(str(row[19]), None),
        )
        if bond:
            bonds_by_secode[secode] = bond
            counts["upserted"] = counts.get("upserted", 0) + 1
        else:
            bonds_by_secode.pop(secode, None)
            counts[reason or "removed"] = counts.get(reason or "removed", 0) + 1

    # Even without Oracle changes, remaining exercise terms move every day.
    refreshed: list[dict] = []
    for bond in bonds_by_secode.values():
        effective = _date(bond.get("effective_maturity_date"))
        term = remaining_term(effective, as_of)
        if term is None or term < MIN_REMAINING_TERM:
            counts["term_below_minimum"] = counts.get("term_below_minimum", 0) + 1
            continue
        bond["term"] = term
        refreshed.append(bond)
    refreshed.sort(key=lambda item: item["code"])
    counts["selected"] = len(refreshed)
    return refreshed, counts


def compare_bond_universes(old_bonds: list[dict], new_bonds: list[dict]) -> dict:
    old_codes = {str(item.get("code") or "").upper() for item in old_bonds if item.get("code")}
    new_codes = {str(item.get("code") or "").upper() for item in new_bonds if item.get("code")}
    intersection = old_codes & new_codes
    denominator = max(len(old_codes), 1)
    return {
        "old_total": len(old_codes),
        "new_total": len(new_codes),
        "intersection": len(intersection),
        "old_only": len(old_codes - new_codes),
        "new_only": len(new_codes - old_codes),
        "old_only_sample": sorted(old_codes - new_codes)[:100],
        "new_only_sample": sorted(new_codes - old_codes)[:100],
        "symmetric_diff_ratio": round(len(old_codes ^ new_codes) / denominator, 6),
    }


def refresh_oracle_bond_universe(conn, as_of_date: str) -> dict:
    """Build the Oracle pool and protect the first Excel-to-Oracle cutover.

    Oracle is read in one filtered master-table pass plus indexed rating batches.
    The approved production pool is cached locally; daily term recalculation then
    needs no historical Oracle scan.  A full reconciliation is repeated weekly.
    """
    current = load_bond_static()
    old_bonds = list(current.get("bonds") or [])
    source_is_oracle = str(current.get("source_file") or "").startswith("oracle:")
    last_full_text = str(current.get("last_full_refresh") or "")[:10]
    try:
        last_full = datetime.strptime(last_full_text, "%Y-%m-%d").date()
    except ValueError:
        last_full = None
    as_of = datetime.strptime(as_of_date.replace("-", ""), "%Y%m%d").date()
    full_refresh = not source_is_oracle or last_full is None or (as_of - last_full).days >= FULL_REFRESH_DAYS
    if full_refresh:
        new_bonds, filter_counts = build_full_oracle_universe(conn, as_of_date)
    else:
        new_bonds, filter_counts = build_incremental_oracle_universe(conn, as_of_date, current)
    comparison = compare_bond_universes(old_bonds, new_bonds)
    review_required = bool(
        old_bonds
        and comparison["symmetric_diff_ratio"] > MAX_SWITCH_DIFF_RATIO
        and not FORCE_SWITCH
    )
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_date": as_of_date,
        "filter_counts": filter_counts,
        "comparison": comparison,
        "threshold": MAX_SWITCH_DIFF_RATIO,
        "review_required": review_required,
        "source_was_oracle": source_is_oracle,
        "refresh_mode": "full" if full_refresh else "incremental",
    }
    write_json(config.ORACLE_BOND_RECONCILIATION_JSON, report)

    if review_required:
        write_json(
            config.ORACLE_BOND_CANDIDATE_JSON,
            {**report, "total_bonds": len(new_bonds), "bonds": new_bonds},
        )
        return {**report, "applied": False, "total_bonds": len(new_bonds)}

    payload = save_bond_static(new_bonds, "oracle:TQ_BD_NEWESTBASICINFO+TQ_BD_BASICINFO+TQ_BD_NEWHIDECREDIT")
    payload["as_of_date"] = as_of_date
    payload["filter_counts"] = filter_counts
    payload["oracle_watermark"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload["last_full_refresh"] = (
        datetime.now().strftime("%Y-%m-%d") if full_refresh
        else current.get("last_full_refresh")
    )
    write_json(config.BOND_STATIC_JSON, payload)
    return {**report, "applied": True, "total_bonds": len(new_bonds)}


def load_oracle_reconciliation() -> dict:
    return load_json(config.ORACLE_BOND_RECONCILIATION_JSON, {})
