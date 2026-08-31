from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from . import config
from . import database


def available_years(db_path=None) -> list[int]:
    with database.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT year FROM limits UNION SELECT year FROM annual_snapshots ORDER BY year"
        ).fetchall()
    return [int(row[0]) for row in rows]


def dashboard(year: int, db_path=None) -> dict:
    today = date.today()
    with database.connect(db_path) as conn:
        amount_rows = conn.execute(
            "SELECT category,amount,source,locked FROM annual_snapshots WHERE year=?", (year,)
        ).fetchall()
        limit_rows = conn.execute("SELECT category,amount FROM limits WHERE year=?", (year,)).fetchall()
        last_success_date = database.get_meta(conn, "last_success_date")
        last_success_at = database.get_meta(conn, "last_success_at")
        last_message = database.get_meta(conn, "last_update_message", "尚未执行Oracle更新")
    amounts = {row["category"]: float(row["amount"]) for row in amount_rows}
    sources = {row["category"]: row["source"] for row in amount_rows}
    limits = {row["category"]: float(row["amount"]) for row in limit_rows}
    items = []
    for category in config.CATEGORIES:
        issued = round(amounts.get(category, 0.0), 4)
        limit = round(limits.get(category, 0.0), 4)
        remaining = round(limit - issued, 4) if limit else 0.0
        progress = issued / limit if limit else None
        validation = "none"
        label = "进行中" if year >= today.year else "接近限额"
        if year < today.year and limit:
            deviation = abs(issued - limit) / limit
            if deviation > 0.20:
                validation, label = "danger", "偏差超过20%"
            elif deviation > 0.10:
                validation, label = "warning", "偏差超过10%"
            else:
                validation = "ok"
        items.append(
            {
                "category": category,
                "issued": issued,
                "limit": limit,
                "remaining": remaining,
                "progress": progress,
                "validation": validation,
                "status_label": label,
                "source": sources.get(category, "尚无数据"),
                "note": config.CATEGORY_NOTES[category],
            }
        )
    return {
        "year": year,
        "current_year": today.year,
        "as_of_date": last_success_date or (f"{year}-12-31" if year < today.year else None),
        "last_success_at": last_success_at,
        "update_message": last_message,
        "items": items,
    }


def long_term_series(scope: str, db_path=None) -> dict:
    if scope not in {"treasury", "local", "total"}:
        raise ValueError("invalid scope")
    where = "term_years >= 20"
    params: tuple = ()
    if scope != "total":
        where += " AND scope=?"
        params = (scope,)
    with database.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT year,issue_date,SUM(amount) daily
            FROM bond_cache
            WHERE {where}
            GROUP BY year,issue_date
            ORDER BY year,issue_date
            """,
            params,
        ).fetchall()
        as_of = database.get_meta(conn, "last_success_date")
    grouped: dict[int, list[dict]] = defaultdict(list)
    cumulative: dict[int, float] = defaultdict(float)
    for row in rows:
        year = int(row["year"])
        daily = float(row["daily"])
        cumulative[year] += daily
        dt = date.fromisoformat(row["issue_date"])
        grouped[year].append(
            {
                "date": dt.isoformat(),
                "day": dt.strftime("%m-%d"),
                "daily": round(daily, 4),
                "cumulative": round(cumulative[year], 4),
            }
        )
    return {
        "scope": scope,
        "as_of_date": as_of,
        "years": [{"year": year, "points": points} for year, points in sorted(grouped.items())],
    }


def policy_financial_series(db_path=None) -> dict:
    """返回三家政策性银行金融债的年内累计发行节奏。"""
    with database.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT year,issue_date,SUM(amount) daily
            FROM policy_financial_bond_cache
            GROUP BY year,issue_date
            ORDER BY year,issue_date
            """
        ).fetchall()
        as_of = database.get_meta(conn, "last_success_date")
    grouped: dict[int, list[dict]] = defaultdict(list)
    cumulative: dict[int, float] = defaultdict(float)
    for row in rows:
        year = int(row["year"])
        daily = float(row["daily"])
        cumulative[year] += daily
        dt = date.fromisoformat(row["issue_date"])
        grouped[year].append(
            {
                "date": dt.isoformat(),
                "day": dt.strftime("%m-%d"),
                "daily": round(daily, 4),
                "cumulative": round(cumulative[year], 4),
            }
        )
    return {
        "scope": "policy_financial",
        "as_of_date": as_of,
        "years": [{"year": year, "points": points} for year, points in sorted(grouped.items())],
    }


def issuance_progress_series(year: int, db_path=None) -> dict:
    """返回五类债券的年内累计节奏，历史年按实际全年规模归一化。"""
    today = date.today()
    daily: dict[str, dict[str, float]] = {
        category: defaultdict(float) for category in config.CATEGORIES
    }
    with database.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT issue_date,classification,include_special_refi,amount
            FROM bond_cache WHERE year=? ORDER BY issue_date
            """,
            (year,),
        ).fetchall()
        maturity_rows = conn.execute(
            "SELECT maturity_date,amount FROM treasury_maturities WHERE year=? ORDER BY maturity_date",
            (year,),
        ).fetchall()
        limit_rows = conn.execute(
            "SELECT category,amount FROM limits WHERE year=?", (year,)
        ).fetchall()
        snapshot_rows = conn.execute(
            "SELECT category,amount FROM annual_snapshots WHERE year=?", (year,)
        ).fetchall()
        history_loaded = (
            database.get_meta(conn, "issuance_progress_history_loaded", "0") == "1"
        )

    for row in rows:
        category = (
            "地方特殊再融资债"
            if row["include_special_refi"]
            else row["classification"]
        )
        if category in daily:
            daily[category][row["issue_date"]] += float(row["amount"])
    for row in maturity_rows:
        daily["一般国债"][row["maturity_date"]] -= float(row["amount"])

    limits = {row["category"]: float(row["amount"]) for row in limit_rows}
    snapshots = {row["category"]: float(row["amount"]) for row in snapshot_rows}
    uses_limit = year >= today.year
    series = []
    for category in config.CATEGORIES:
        events = sorted(daily[category].items())
        raw_total = sum(amount for _, amount in events)
        actual_total = raw_total if uses_limit else snapshots.get(category, raw_total)
        denominator = limits.get(category, 0.0) if uses_limit else actual_total
        scale = 1.0 if uses_limit or not raw_total else actual_total / raw_total
        cumulative = 0.0
        points = []
        for issue_date, amount in events:
            normalized_amount = amount * scale
            cumulative += normalized_amount
            points.append(
                {
                    "date": issue_date,
                    "day": issue_date[5:],
                    "daily": round(normalized_amount, 4),
                    "cumulative": round(cumulative, 4),
                    "progress": round(cumulative / denominator, 8)
                    if denominator
                    else None,
                }
            )
        series.append(
            {
                "category": category,
                "basis": "limit" if uses_limit else "actual",
                "denominator": round(denominator, 4),
                "actual_total": round(actual_total, 4),
                "raw_total": round(raw_total, 4),
                "points": points,
            }
        )
    return {
        "year": year,
        "basis": "limit" if uses_limit else "actual",
        "basis_label": "年度限额=100%" if uses_limit else "全年实际发行规模=100%",
        "history_loaded": history_loaded,
        "series": series,
    }


def issuance_progress_compare(
    categories: list[str] | str, years: list[int], db_path=None
) -> dict:
    if isinstance(categories, str):
        categories = [categories]
    clean_categories: list[str] = []
    for category in categories:
        if category not in config.CATEGORIES:
            raise ValueError("债券品种不正确")
        if category not in clean_categories:
            clean_categories.append(category)
    if not clean_categories:
        raise ValueError("请至少选择一个比较品种")
    clean_years = sorted(set(int(year) for year in years))
    if not clean_years:
        raise ValueError("请至少选择一个比较年份")
    if len(clean_years) > 20:
        raise ValueError("一次最多比较20个年份")
    result = []
    for year in clean_years:
        data = issuance_progress_series(year, db_path)
        selected = [
            item for item in data["series"] if item["category"] in clean_categories
        ]
        daily: dict[str, float] = defaultdict(float)
        for item in selected:
            for point in item["points"]:
                daily[point["date"]] += point["daily"]
        denominator = sum(item["denominator"] for item in selected)
        cumulative = 0.0
        points = []
        for issue_date, amount in sorted(daily.items()):
            cumulative += amount
            points.append(
                {
                    "date": issue_date,
                    "day": issue_date[5:],
                    "daily": round(amount, 4),
                    "cumulative": round(cumulative, 4),
                    "progress": round(cumulative / denominator, 8)
                    if denominator
                    else None,
                }
            )
        result.append(
            {
                "year": year,
                "basis": data["basis"],
                "denominator": round(denominator, 4),
                "actual_total": round(
                    sum(item["actual_total"] for item in selected), 4
                ),
                "raw_total": round(sum(item["raw_total"] for item in selected), 4),
                "points": points,
            }
        )
    return {
        "categories": clean_categories,
        "category": "＋".join(clean_categories),
        "years": result,
    }


def special_refi_details(year: int, db_path=None) -> list[dict]:
    with database.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT issue_date,name,codes,amount,reason,purpose
            FROM bond_cache
            WHERE year=? AND scope='local' AND include_special_refi=1
            ORDER BY issue_date,name
            """,
            (year,),
        ).fetchall()
    return [
        {
            "issue_date": row["issue_date"],
            "name": row["name"],
            "codes": row["codes"],
            "amount": round(float(row["amount"]), 4),
            "reason": row["reason"],
            "purpose": row["purpose"],
        }
        for row in rows
    ]


def list_limits(db_path=None) -> list[dict]:
    with database.connect(db_path) as conn:
        rows = conn.execute("SELECT year,category,amount,updated_at FROM limits ORDER BY year,category").fetchall()
    by_year: dict[int, dict] = {}
    for row in rows:
        item = by_year.setdefault(int(row["year"]), {"year": int(row["year"]), "values": {}})
        item["values"][row["category"]] = float(row["amount"])
        item["updated_at"] = row["updated_at"]
    return list(by_year.values())


def save_limits(payload: list[dict], actor: str, db_path=None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with database.transaction(db_path) as conn:
        for item in payload:
            year = int(item["year"])
            if year < 2000 or year > 2200:
                raise ValueError("年份必须在2000至2200之间")
            values = item.get("values") or {}
            for category in config.CATEGORIES:
                amount = float(values.get(category, 0))
                if amount < 0:
                    raise ValueError("限额不能为负数")
                old = conn.execute(
                    "SELECT amount FROM limits WHERE year=? AND category=?", (year, category)
                ).fetchone()
                old_amount = float(old[0]) if old else None
                conn.execute(
                    """
                    INSERT INTO limits(year,category,amount,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(year,category) DO UPDATE SET amount=excluded.amount,updated_at=excluded.updated_at
                    """,
                    (year, category, amount, now),
                )
                if old_amount != amount:
                    conn.execute(
                        "INSERT INTO limit_audit(year,category,old_amount,new_amount,changed_at,actor) VALUES(?,?,?,?,?,?)",
                        (year, category, old_amount, amount, now, actor),
                    )


def update_status(db_path=None) -> dict:
    with database.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM update_runs ORDER BY id DESC LIMIT 1").fetchone()
        last_success_at = database.get_meta(conn, "last_success_at")
    return {
        "latest": dict(row) if row else None,
        "last_success_at": last_success_at,
    }


def update_logs(db_path=None, limit: int = 10) -> list[dict]:
    with database.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM update_runs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 50)),)
        ).fetchall()
    return [dict(row) for row in rows]
