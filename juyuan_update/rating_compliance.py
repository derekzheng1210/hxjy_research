"""合规630跟踪评级判定（二级择券工具）。

规则（以查询日为基准，设查询日所在年份为 Y）：
- 查询日在当年6月30日之后：除当年新发债外，债券须在当年1月1日-6月30日
  具有主体跟踪评级；
- 查询日在当年6月30日（含）之前：除当年及去年新发债外，债券须在去年
  1月1日-6月30日或当年1月1日至查询日具有主体跟踪评级；
- 债券本身有债项评级（历史存在任一债项评级记录）时，上述要求升级为
  主体与债项跟踪评级均须在期限内；
- 新发债按 Oracle 池起息日判断；
- 主体"无存续有效评级"（研报口径，2026-09）时吊销新发债豁免，回退窗口
  判定：窗口内有跟踪评级记录的当年 630 仍判合规，否则不合规。

主体存续有效评级口径（与"无存续有效评级的发债主体"研报对齐）：
- 仅境内评级机构：国际机构（联合评级国际/中国诚信(亚太)/惠誉国际/穆迪/
  标普国际）及银行类噪音不计；
- 机构评级有效 = 未被终止公告终止（该机构终止后又有新评级事件视为重启，
  数据事实优先于公告语义）且 有效截止日 >= 查询日；有效截止日取该机构
  最新评级动作对应债项评级记录的 RATEEXPDATE（TQ_BD_CREDITRATE），
  缺省为评级日 + 365 天；
- 终止公告按标题归因：机构发起（机构全称/简称/繁体前缀）终止该机构；
  发行人自发的"终止主体(及债项)"公告视为全机构终止候选；终止对象是
  担保方主体、或只终止特定债项的不算主体终止；同一机构终止后重启、
  发行人公告后其他机构仍在评的均以事件事实覆盖。

评级事件事实由 ``juyuan_update.db.fetch_bond_rating_facts`` 从聚源双表抓取，
主体评级状态原始数据由 ``juyuan_update.db.fetch_issuer_rating_raw`` 抓取。
缓存只保留一版（BOND_DIR/rating_facts_cache.json），每日更新任务抓取事实、
按当日判定后整体覆盖写入；页面组装数据时直接读缓存的判定结果，缓存跨日
未刷新时按事实现算兜底（不回写缓存）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import config
from .unified_excel import load_json, write_json

# 境外/国际评级机构不计入境内有效评级（惠誉博华、标普中国属境内保留）
INTERNATIONAL_AGENCIES = {
    "联合评级国际有限公司",
    "中国诚信(亚太)信用评级有限公司",
    "惠誉国际信用评级有限公司",
    "穆迪投资者服务公司",
    "标准普尔评级服务公司",
}

# 终止公告标题里的机构简称/繁体变体 -> 机构全称（归因时全称本身也直接匹配）
AGENCY_ALIASES = {
    "中诚信国际信用评级有限责任公司": ["中诚信国际", "中誠信國際"],
    "联合资信评估股份有限公司": ["联合资信", "聯合資信"],
    "联合信用评级有限公司": ["联合信用", "聯合信用"],
    "东方金诚国际信用评估有限公司": ["东方金诚", "東方金誠"],
    "大公国际资信评估有限公司": ["大公国际", "大公國際"],
    "中证鹏元资信评估股份有限公司": ["中证鹏元", "中證鵬元"],
    "上海新世纪资信评估投资服务有限公司": ["上海新世纪", "上海新世紀", "新世纪评级", "新世紀評級"],
    "远东资信评估有限公司": ["远东资信", "遠東資信"],
    "中债资信评估有限责任公司": ["中债资信", "中債資信"],
    "安融信用评级有限公司": ["安融信用", "安融评级"],
    "标普信用评级(中国)有限公司": ["标普信用评级"],
    "惠誉博华信用评级有限公司": ["惠誉博华", "惠譽博華"],
    "大普信用评级股份有限公司": ["大普信用"],
    "安泰信用评级有限责任公司": ["安泰信用"],
    "中诚信证评数据科技有限公司": ["中诚信证评"],
}

# 主体评级状态 -> 展示/判定文案
STATUS_NOTES = {
    "valid": "主体有存续有效评级",
    "terminated": "主体评级已全部终止",
    "expired": "主体评级已到期未续",
    "mixed_terminated_expired": "主体评级已终止/到期，无存续有效评级",
    "never_rated": "主体无评级记录",
}


def persist_rating_facts(
    facts: dict, bonds: list[dict], as_of: date, issuer_status: dict | None = None
) -> dict:
    """Merge Oracle issue dates and issuer rating status, then overwrite the cache."""
    issue_dates = {b["code"]: b.get("issue_date") or "" for b in bonds}
    issuer_of = {b["code"]: str(b.get("issuer") or "").strip() for b in bonds}
    for code, fact in facts.items():
        fact["issue_date"] = issue_dates.get(code, "")
        status = (issuer_status or {}).get(issuer_of.get(code, ""))
        if status:
            fact["issuer_rating"] = {"valid": bool(status["valid"]), "note": status["note"]}
        else:
            # 无该主体状态信息（如主体状态抓取失败）时不误标，evaluate 按有效豁免处理
            fact.pop("issuer_rating", None)
    return save_rating_facts_cache(facts, as_of=as_of, issuer_status=issuer_status)


def refresh_rating_compliance_cache() -> dict:
    """Re-fetch rating facts for the current picker pool and overwrite the cache.

    债券池或起息日随 Oracle 更新后同步重建缓存（单版本整体覆盖），
    也可由维护任务单独调用。
    """
    from .db import connect, fetch_bond_rating_facts, fetch_issuer_rating_raw
    from .unified_excel import get_bond_picker_bonds

    bonds = get_bond_picker_bonds()
    if not bonds:
        raise RuntimeError("未找到择券工具债券清单，无法刷新630评级缓存")
    with connect() as conn:
        facts = fetch_bond_rating_facts(conn, [b["code"] for b in bonds])
        issuer_raw = fetch_issuer_rating_raw(conn, bonds)
    issuer_status = build_issuer_rating_status(issuer_raw, date.today())
    return persist_rating_facts(facts, bonds, date.today(), issuer_status=issuer_status)


def load_rating_facts_cache() -> dict:
    return load_json(config.RATING_FACTS_CACHE, {"generated_at": "", "facts": {}})


def build_compliance_verdicts(facts: dict, as_of: date) -> dict[str, list[str]]:
    """Compute the per-bond ``[status, reason]`` verdicts for one as-of date."""
    return {
        code: [verdict["status"], verdict["reason"]]
        for code, verdict in (
            (code, evaluate_rating_compliance(as_of, fact)) for code, fact in facts.items()
        )
    }


def save_rating_facts_cache(
    facts: dict, as_of: date | None = None, issuer_status: dict | None = None
) -> dict:
    as_of = as_of or date.today()
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_date": as_of.strftime("%Y-%m-%d"),
        "total_bonds": len(facts),
        "facts": facts,
        "issuer_status": issuer_status or {},
        "compliance": build_compliance_verdicts(facts, as_of),
    }
    write_json(config.RATING_FACTS_CACHE, payload)
    return payload


def _parse_date(value) -> date | None:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 主体存续有效评级判定（研报口径）
# ---------------------------------------------------------------------------

def classify_termination_title(
    title: str, issuer: str, known_agencies=()
) -> tuple[str, str | None]:
    """把终止/撤销评级公告标题归因为 ``('agency', 机构全称)`` / ``('issuer', 发行人)`` / ``('other', None)``。

    机构发起：标题以机构全称或简称（含繁体）开头；发行人发起：标题以发行人
    名开头，或"关于终止XX主体信用评级的公告"式（以"关于"开头、主体名在标题
    中且明确含"主体"，纯债项/ABS 档级终止不匹配）。受托管理人报告（券商名
    开头）等其余情况归 other，不做主体终止依据。
    """
    title = str(title or "")
    for full, shorts in AGENCY_ALIASES.items():
        if title.startswith(full) or any(title.startswith(s) for s in shorts):
            return "agency", full
    for agency in known_agencies:
        if agency and title.startswith(agency):
            return "agency", agency
    if issuer and title.startswith(issuer):
        return "issuer", issuer
    if (
        issuer
        and title.startswith(("关于", "關於"))
        and issuer in title
        and ("主体" in title or "主體" in title)
    ):
        return "issuer", issuer
    return "other", None


def _title_terminates_issuer_rating(title: str) -> bool:
    """标题是否明确终止本主体评级；终止对象为担保方主体时不算。"""
    if "担保方" in title or "擔保方" in title:
        return False
    return "主体" in title or "主體" in title


def _title_generic_termination(title: str) -> bool:
    """发行人自发公告未写"主体"也未限定"债项"（如"关于终止评级的公告"）。"""
    return "债项" not in title and "債項" not in title


def _plus_one_year(day: str) -> str:
    parsed = date.fromisoformat(day[:10])
    try:
        return parsed.replace(year=parsed.year + 1).isoformat()
    except ValueError:  # 2月29日
        return parsed.replace(year=parsed.year + 1, month=3, day=1).isoformat()


def compute_issuer_rating_status(issuer: str, raw: dict, as_of: date) -> dict:
    """按研报口径计算一个主体的存续有效评级状态。

    ``raw`` 为 ``fetch_issuer_rating_raw`` 返回的单主体数据：
    ``{"events": {机构: [评级日]}, "bond_ratings": {机构: [[评级日, 有效截止日]]},
    "announcements": [[公告日, 标题]]}``。返回
    ``{"valid": bool, "reason": str, "note": str, "agencies": {机构: 状态明细}}``。
    """
    as_of_str = as_of.isoformat() if isinstance(as_of, date) else str(as_of)[:10]
    events = raw.get("events") or {}
    domestic = {
        agency: days
        for agency, days in events.items()
        if agency and "银行" not in agency and agency not in INTERNATIONAL_AGENCIES
    }
    agency_last = {agency: max(days) for agency, days in domestic.items() if days}

    term_agency: dict[str, list[str]] = {}
    term_full: list[str] = []
    known = set(domestic) | set(AGENCY_ALIASES)
    for day, title in sorted(raw.get("announcements") or []):
        kind, who = classify_termination_title(title, issuer, known)
        if kind == "agency":
            if _title_terminates_issuer_rating(title):
                term_agency.setdefault(who or "", []).append(day)
        elif kind == "issuer":
            if _title_terminates_issuer_rating(title) or _title_generic_termination(title):
                term_full.append(day)

    bond_ratings = raw.get("bond_ratings") or {}
    agencies: dict[str, dict] = {}
    any_valid = False
    for agency, last in sorted(agency_last.items()):
        terminations = term_agency.get(agency, []) + term_full
        terminated_on = max(terminations) if terminations else ""
        if terminated_on and terminated_on >= last:
            agencies[agency] = {
                "last": last, "terminated_on": terminated_on,
                "valid_until": "", "status": "terminated",
            }
            continue
        # 有效截止日：该机构 [最后评级日-90天, 最后评级日] 窗口内债项评级的
        # 最大 RATEEXPDATE（聚源债项评级表），缺省为评级日 + 365 天
        lookback = (date.fromisoformat(last) - timedelta(days=90)).isoformat()
        expiries = [
            exp for credit_day, exp in bond_ratings.get(agency, [])
            if credit_day and lookback <= credit_day <= last and exp
        ]
        valid_until = max(expiries) if expiries and max(expiries) > last else _plus_one_year(last)
        valid = valid_until >= as_of_str
        any_valid = any_valid or valid
        agencies[agency] = {
            "last": last, "terminated_on": terminated_on,
            "valid_until": valid_until, "status": "valid" if valid else "expired",
        }

    if not agency_last:
        reason, valid = "never_rated", False
    elif any_valid:
        reason, valid = "valid", True
    else:
        statuses = {info["status"] for info in agencies.values()}
        if statuses == {"terminated"}:
            reason = "terminated"
        elif statuses == {"expired"}:
            reason = "expired"
        else:
            reason = "mixed_terminated_expired"
        valid = False
    return {
        "valid": valid,
        "reason": reason,
        "note": STATUS_NOTES.get(reason, "主体无存续有效评级"),
        "agencies": agencies,
    }


def build_issuer_rating_status(raw_by_issuer: dict, as_of: date) -> dict:
    """批量计算主体评级状态；仅对抓到原始数据的主体产出（抓取失败不误标）。"""
    return {
        issuer: compute_issuer_rating_status(issuer, raw, as_of)
        for issuer, raw in (raw_by_issuer or {}).items()
    }


def evaluate_rating_compliance(as_of: date, fact: dict | None) -> dict:
    """Return ``{"status": "ok"|"fail"|"unknown", "reason": str}`` for one bond.

    ``fail`` 表示明确不满足630跟踪评级要求（不可投）；
    ``unknown`` 表示数据不足无法判定（如聚源无该券记录、起息日缺失），
    同样以红点提示投前人工确认，但不从推荐中剔除。
    主体无存续有效评级（fact["issuer_rating"]["valid"] is False）时，
    新发债豁免被吊销并回退窗口判定；fact 无该键（旧缓存/抓取失败）时
    视为有效，维持豁免。
    """
    if not fact:
        return {"status": "unknown", "reason": "暂无评级数据，未校验"}
    as_of = as_of if isinstance(as_of, date) else date.today()
    year = as_of.year
    issue = _parse_date(fact.get("issue_date"))
    issuer_dates = {d for d in (_parse_date(x) for x in fact.get("issuer_dates") or []) if d}
    credit_dates = {d for d in (_parse_date(x) for x in fact.get("credit_dates") or []) if d}
    has_credit_rating = bool(credit_dates)
    issuer_rating = fact.get("issuer_rating") or {}
    rating_invalid = issuer_rating.get("valid") is False
    rating_note = str(issuer_rating.get("note") or "主体无存续有效评级")

    if as_of > date(year, 6, 30):
        exempt_years = {year}
        windows = [(date(year, 1, 1), date(year, 6, 30))]
        window_text = f"{year}年1月1日-6月30日"
    else:
        exempt_years = {year - 1, year}
        windows = [
            (date(year - 1, 1, 1), date(year - 1, 6, 30)),
            (date(year, 1, 1), as_of),
        ]
        window_text = f"{year - 1}年1月1日-6月30日或{year}年1月1日至今"

    def in_windows(days: set[date]) -> bool:
        return any(start <= day <= end for day in days for start, end in windows)

    def window_missing() -> list[str]:
        missing = []
        if not in_windows(issuer_dates):
            missing.append("主体")
        if has_credit_rating and not in_windows(credit_dates):
            missing.append("债项")
        return missing

    if issue is not None and issue.year in exempt_years:
        if not rating_invalid:
            return {"status": "ok", "reason": f"{issue.year}年新发债，豁免630跟踪评级要求"}
        # 主体无存续有效评级：吊销新发债豁免，回退窗口判定
        missing = window_missing()
        if not missing:
            return {"status": "ok", "reason": f"{rating_note}；{window_text}有跟踪评级，当年630判定仍合规"}
        target = "与".join(missing) + "跟踪评级"
        return {"status": "fail", "reason": f"{rating_note}，且缺少{window_text}的{target}"}

    missing = window_missing()
    if not missing:
        return {"status": "ok", "reason": ""}
    target = "与".join(missing) + "跟踪评级"
    reason = f"缺少{window_text}的{target}"
    if issue is None:
        return {"status": "unknown", "reason": f"{reason}，且起息日缺失无法判断新发债豁免"}
    return {"status": "fail", "reason": reason}
