"""角色筛选与估值/利差序列组装。"""
from __future__ import annotations

from . import config, db

ROLE_LABELS = {
    "active": "活跃券",
    "secondary": "次活跃券",
    "tertiary": "次次活跃券",
    "tax_exempt": "免税债券",
}

# 图表选券用的角色虚拟代码：每日按成交量排名动态判定对应个券后取当日估值
ROLE_SERIES_CODES = {"__active": "活跃券", "__secondary": "次活跃券", "__tertiary": "次次活跃券"}
ROLE_ORDER = list(ROLE_SERIES_CODES)


def _core_assign(
    entries: list[dict],
    gap_bp: float = config.ACTIVE_YIELD_GAP_BP,
    tertiary_cutoff: str = config.TERTIARY_ISSUE_CUTOFF,
) -> dict | None:
    """活跃券判定共享核心：供最新快照(select_roles)与逐日动态序列(_daily_roles)使用。

    entries: [{code, yield, deals, vol, issue_date}]，其中 yield 为非空。
    规则：
      - 按(成交笔数, 成交量, 发行日, 代码)排名，第一名为活跃券(primary)；
      - 与其估值差<=gap_bp 的下一顺位并列活跃(仅计入活跃集合)；
      - 次活跃券 = 发行日最新的券（若该券已在活跃集合内，顺延到次新的非活跃券）；
      - 次次活跃券：优先取发行日>=tertiary_cutoff 的非前述角色券中成交量最大者；
        历史上无新发券时，回退为剩余券中(发行日, 成交量)最优者。
    """
    if not entries:
        return None
    ranked = sorted(
        entries,
        key=lambda x: (x["deals"], x["vol"], x["issue_date"], x["code"]),
        reverse=True,
    )
    primary = ranked[0]
    active = [primary["code"]]
    for entry in ranked[1:]:
        if abs(float(entry["yield"]) - float(primary["yield"])) * 100 <= gap_bp + 1e-9:
            active.append(entry["code"])
            break

    newest_order = sorted(entries, key=lambda x: (x["issue_date"], x["code"]), reverse=True)
    secondary = next((e for e in newest_order if e["code"] not in active), None)
    excluded = set(active) | ({secondary["code"]} if secondary else set())
    pool = [
        e for e in entries
        if e["issue_date"] >= tertiary_cutoff and e["code"] not in excluded
    ]
    tertiary = None
    if pool:
        # 优先取满足新发门槛的券中成交量最大者
        tertiary = max(pool, key=lambda x: (x["vol"], x["deals"], x["issue_date"], x["code"]))["code"]
    else:
        # 历史窗口内可能没有达到新发门槛的券：回退到剩余券中按(发行日,成交量)取最优，
        # 保证次次活跃券与免税利差序列能覆盖全历史。
        rest = [e for e in entries if e["code"] not in excluded]
        if rest:
            tertiary = max(rest, key=lambda x: (x["issue_date"], x["vol"], x["deals"], x["code"]))["code"]
    return {"primary": primary["code"], "active": active, "secondary": secondary["code"] if secondary else None, "tertiary": tertiary}


def select_roles(candidates: list[dict]) -> dict[str, list[str]]:
    """按上述共享规则对最新快照确定当前角色。"""
    valid = [
        {
            "code": x["code"], "yield": float(x.get("valuation_yield")),
            "deals": int(x.get("deal_count") or 0), "vol": float(x.get("volume") or 0),
            "issue_date": x.get("issue_date") or "",
        }
        for x in candidates if x.get("valuation_yield") is not None
    ]
    assigned = _core_assign(valid)
    if not assigned:
        return {"active": [], "secondary": [], "tertiary": [], "tax_exempt": []}
    tax = [config.TAX_EXEMPT_CODE] if any(x["code"] == config.TAX_EXEMPT_CODE for x in valid) else []
    return {
        "active": assigned["active"],
        "secondary": [assigned["secondary"]] if assigned["secondary"] else [],
        "tertiary": [assigned["tertiary"]] if assigned["tertiary"] else [],
        "tax_exempt": tax,
    }


def _fmt_date(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _rolling(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    valid: list[float] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        valid.append(float(value))
        tail = valid[-window:]
        out.append(round(sum(tail) / len(tail), 6) if len(tail) >= window else None)
    return out


def dashboard(db_path=None, update_status: dict | None = None) -> dict:
    as_of, bonds, roles = db.load_latest_dashboard(db_path)
    by_code = {x["code"]: x for x in bonds}
    role_out = []
    for role in ("active", "secondary", "tertiary"):
        for ordinal, code in enumerate(roles.get(role, []), 1):
            bond = by_code.get(code)
            if bond:
                active_since = db.role_first_date("active", code, db_path=db_path) if role == "active" else None
                role_out.append(
                    {
                        "role": role,
                        "role_label": ROLE_LABELS[role],
                        "ordinal": ordinal,
                        "bond": bond,
                        "active_since": _fmt_date(active_since) if active_since else None,
                    }
                )
    selectable_codes = []
    for item in role_out:
        if item["bond"]["code"] not in selectable_codes:
            selectable_codes.append(item["bond"]["code"])
    return {
        "as_of_date": _fmt_date(as_of) if as_of else None,
        "quote_date": _fmt_date(db.get_meta("quote_date", db_path=db_path)) if db.get_meta("quote_date", db_path=db_path) else None,
        "roles": role_out,
        "selectable_bonds": [by_code[c] for c in selectable_codes if c in by_code],
        "tax_exempt_bond": by_code.get(config.TAX_EXEMPT_CODE),
        "candidate_count": len(bonds),
        "last_run": db.last_successful_run(db_path),
        "update_status": update_status or {},
        "rules": {
            "remaining_years": [config.REMAINING_MIN_YEARS, config.REMAINING_MAX_YEARS],
            "active_yield_gap_bp": config.ACTIVE_YIELD_GAP_BP,
            "tertiary_issue_cutoff": _fmt_date(config.TERTIARY_ISSUE_CUTOFF),
            "tax_exempt_code": config.TAX_EXEMPT_CODE,
        },
    }


def _daily_roles(db_path=None) -> dict[str, dict[str, tuple[float, str]]]:
    """逐日角色判定：与最新快照(select_roles)使用同一套 _core_assign 规则。

    返回 {角色虚拟代码: {交易日: (当日估值, 当日判定的券代码)}}。
    """
    matrix = db.load_point_matrix(db_path=db_path)
    quotes = db.load_daily_quotes(db_path=db_path)
    attrs = db.snapshot_bond_attrs(db_path)
    out: dict[str, dict[str, tuple[float, str]]] = {k: {} for k in ROLE_ORDER}
    for trade_date, bonds in sorted(matrix.items()):
        day_quotes = quotes.get(trade_date, {})
        entries = []
        for code, (yld, remaining) in bonds.items():
            # 免税债券不参与角色竞争，否则其自身与自身的差值会把利差压成0
            if code == config.TAX_EXEMPT_CODE:
                continue
            if remaining is not None and not (
                config.REMAINING_MIN_YEARS - 1e-9 <= remaining <= config.REMAINING_MAX_YEARS + 1e-9
            ):
                continue
            deals, vol = day_quotes.get(code, (0, 0.0))
            entries.append({
                "code": code, "yield": yld,
                "deals": int(deals), "vol": float(vol),
                "issue_date": (attrs.get(code) or {}).get("issue_date") or "",
            })
        assigned = _core_assign(entries)
        if not assigned:
            continue
        yield_by_code = {e["code"]: e["yield"] for e in entries}
        out["__active"][trade_date] = (yield_by_code[assigned["primary"]], assigned["primary"])
        if assigned["secondary"]:
            out["__secondary"][trade_date] = (yield_by_code[assigned["secondary"]], assigned["secondary"])
        if assigned["tertiary"]:
            out["__tertiary"][trade_date] = (yield_by_code[assigned["tertiary"]], assigned["tertiary"])
    return out


def _role_yields(roles: dict[str, dict[str, tuple[float, str]]]) -> dict[str, dict[str, float]]:
    return {k: {d: pair[0] for d, pair in day.items()} for k, day in roles.items()}


def series(pairs: list[tuple[str, str]], rng: str = "all", db_path=None) -> dict:
    dash = dashboard(db_path)
    allowed = {x["code"] for x in dash["selectable_bonds"]} | set(ROLE_SERIES_CODES)
    clean_pairs: list[tuple[str, str]] = []
    for left, right in pairs:
        if left in allowed and right in allowed and left != right and (left, right) not in clean_pairs:
            clean_pairs.append((left, right))
    if not clean_pairs and len(allowed) >= 2:
        clean_pairs = [("__active", "__secondary")]
    needed_codes = {c for pair in clean_pairs for c in pair}
    role_yields: dict[str, dict[str, float]] = {}
    role_picks: dict[str, dict[str, dict]] = {}
    if needed_codes & set(ROLE_SERIES_CODES):
        roles_raw = _daily_roles(db_path)
        role_yields = _role_yields(roles_raw)
        # 每个交易日角色对应的个券（历史券名从快照中回溯）
        snapshot_names = db.all_snapshot_names(db_path)

        def pick_name(code: str) -> str:
            hit = next((x["short_name"] for x in dash["selectable_bonds"] if x["code"] == code), None)
            return hit or snapshot_names.get(code) or code

        for pseudo, day in roles_raw.items():
            role_picks[pseudo] = {
                d: {"code": pair[1], "name": pick_name(pair[1])} for d, pair in day.items()
            }

    def entity(code: str) -> str:
        if code in ROLE_SERIES_CODES:
            return ROLE_SERIES_CODES[code]
        bond = next((x for x in dash["selectable_bonds"] if x["code"] == code), None)
        return bond["short_name"] if bond else code

    points = db.load_valuations(sorted(needed_codes - set(ROLE_SERIES_CODES)), db_path=db_path)
    bond_series = []
    entity_order: list[str] = []
    for pair in clean_pairs:
        for code in pair:
            if code not in entity_order:
                entity_order.append(code)
    for code in entity_order:
        name = entity(code)
        values_map = role_yields.get(code) if code in ROLE_SERIES_CODES else points.get(code)
        dates = sorted(values_map or {})
        # 角色虚拟券的构成逐日变化，无固定发行日；具体券用于“自发行时”锚点
        bond_info = next((x for x in dash["selectable_bonds"] if x["code"] == code), None)
        entry = {
            "code": code,
            "name": name,
            "issue_date": _fmt_date(bond_info["issue_date"]) if bond_info and bond_info.get("issue_date") else None,
            "dates": [_fmt_date(d) for d in dates],
            "values": [values_map[d] for d in dates],
            "ma": {str(w): _rolling([values_map[d] for d in dates], w) for w in (5, 10, 30)},
        }
        if code in ROLE_SERIES_CODES:
            # tooltip 用：每个交易日该角色实际对应的个券
            picks = role_picks.get(code, {})
            entry["role_bonds"] = {_fmt_date(d): picks.get(d) for d in dates}
        bond_series.append(entry)
    spread_series = []
    for left, right in clean_pairs:
        lm = role_yields.get(left) if left in ROLE_SERIES_CODES else points.get(left, {})
        rm = role_yields.get(right) if right in ROLE_SERIES_CODES else points.get(right, {})
        dates = sorted(set(lm) & set(rm))
        left_name = entity(left)
        right_name = entity(right)
        values = [round((lm[d] - rm[d]) * 100, 4) for d in dates]
        spread_series.append({
            "key": f"{left}:{right}", "left": left, "right": right,
            "name": f"{left_name} - {right_name}",
            "dates": [_fmt_date(d) for d in dates], "values": values,
            "ma": {str(w): _rolling(values, w) for w in (5, 10, 30)},
        })
    return {"bonds": bond_series, "spreads": spread_series, "range": rng}


def tax_spread(rng: str = "all", db_path=None) -> dict:
    dash = dashboard(db_path)
    tax = dash.get("tax_exempt_bond")
    if not tax:
        return {"available": False, "reason": "当前缺少固定免税债券估值"}
    roles = _daily_roles(db_path)
    tertiary_daily = roles.get("__tertiary") or {}
    if not tertiary_daily:
        return {"available": False, "reason": "尚无逐日成交数据，无法每日判定次次活跃券"}
    tax_points = db.load_valuations([config.TAX_EXEMPT_CODE], db_path=db_path).get(config.TAX_EXEMPT_CODE, {})
    dates = sorted(d for d in tertiary_daily if d in tax_points)
    values = [round((tertiary_daily[d][0] - tax_points[d]) * 100, 4) for d in dates]
    # 标注最新一日的次次活跃券是哪只
    latest_d = max(tertiary_daily)
    latest_code = tertiary_daily[latest_d][1]
    snapshot_names = db.all_snapshot_names(db_path)

    def pick_name(code: str) -> str:
        hit = next((x["short_name"] for x in dash["selectable_bonds"] if x["code"] == code), None)
        return hit or snapshot_names.get(code) or code

    third_label = "次次活跃券"
    return {
        "available": bool(dates),
        "name": f"{third_label} - 免税债券",
        "tax_name": tax["short_name"],
        "latest_tertiary": {"date": _fmt_date(latest_d), "code": latest_code, "name": pick_name(latest_code)},
        "tertiary_by_date": {_fmt_date(d): pick_name(pair[1]) for d, pair in tertiary_daily.items()},
        "dates": [_fmt_date(d) for d in dates],
        "values": values,
        "ma": {str(w): _rolling(values, w) for w in (5, 10, 30)},
    }