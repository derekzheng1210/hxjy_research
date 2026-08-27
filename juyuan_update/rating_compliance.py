"""合规630跟踪评级判定（二级择券工具）。

规则（以查询日为基准，设查询日所在年份为 Y）：
- 查询日在当年6月30日之后：除当年新发债外，债券须在当年1月1日-6月30日
  具有主体跟踪评级；
- 查询日在当年6月30日（含）之前：除当年及去年新发债外，债券须在去年
  1月1日-6月30日或当年1月1日至查询日具有主体跟踪评级；
- 债券本身有债项评级（历史存在任一债项评级记录）时，上述要求升级为
  主体与债项跟踪评级均须在期限内；
- 新发债按统一Excel起息日判断。

评级事件事实由 ``juyuan_update.db.fetch_bond_rating_facts`` 从聚源双表抓取。
缓存只保留一版（BOND_DIR/rating_facts_cache.json），每日更新任务抓取事实、
按当日判定后整体覆盖写入；页面组装数据时直接读缓存的判定结果，缓存跨日
未刷新时按事实现算兜底（不回写缓存）。
"""
from __future__ import annotations

from datetime import date, datetime

from . import config
from .unified_excel import load_json, write_json


def persist_rating_facts(facts: dict, bonds: list[dict], as_of: date) -> dict:
    """Merge issue dates from the unified Excel bond list and overwrite the cache."""
    issue_dates = {b["code"]: b.get("issue_date") or "" for b in bonds}
    for code, fact in facts.items():
        fact["issue_date"] = issue_dates.get(code, "")
    return save_rating_facts_cache(facts, as_of=as_of)


def refresh_rating_compliance_cache() -> dict:
    """Re-fetch rating facts for the current picker pool and overwrite the cache.

    供统一 Excel 后台上传后调用：债券清单或起息日变化时同步重建缓存
    （单版本整体覆盖），也用于每日更新任务。
    """
    from .db import connect, fetch_bond_rating_facts
    from .unified_excel import get_bond_picker_bonds

    bonds = get_bond_picker_bonds()
    if not bonds:
        raise RuntimeError("未找到择券工具债券清单，无法刷新630评级缓存")
    with connect() as conn:
        facts = fetch_bond_rating_facts(conn, [b["code"] for b in bonds])
    return persist_rating_facts(facts, bonds, date.today())


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


def save_rating_facts_cache(facts: dict, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_date": as_of.strftime("%Y-%m-%d"),
        "total_bonds": len(facts),
        "facts": facts,
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


def evaluate_rating_compliance(as_of: date, fact: dict | None) -> dict:
    """Return ``{"status": "ok"|"fail"|"unknown", "reason": str}`` for one bond.

    ``fail`` 表示明确不满足630跟踪评级要求（不可投）；
    ``unknown`` 表示数据不足无法判定（如聚源无该券记录、起息日缺失），
    同样以红点提示投前人工确认，但不从推荐中剔除。
    """
    if not fact:
        return {"status": "unknown", "reason": "暂无评级数据，未校验"}
    as_of = as_of if isinstance(as_of, date) else date.today()
    year = as_of.year
    issue = _parse_date(fact.get("issue_date"))
    issuer_dates = {d for d in (_parse_date(x) for x in fact.get("issuer_dates") or []) if d}
    credit_dates = {d for d in (_parse_date(x) for x in fact.get("credit_dates") or []) if d}
    has_credit_rating = bool(credit_dates)

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

    if issue is not None and issue.year in exempt_years:
        return {"status": "ok", "reason": f"{issue.year}年新发债，豁免630跟踪评级要求"}

    def in_windows(days: set[date]) -> bool:
        return any(start <= day <= end for day in days for start, end in windows)

    missing = []
    if not in_windows(issuer_dates):
        missing.append("主体")
    if has_credit_rating and not in_windows(credit_dates):
        missing.append("债项")
    if not missing:
        return {"status": "ok", "reason": ""}

    target = "与".join(missing) + "跟踪评级"
    reason = f"缺少{window_text}的{target}"
    if issue is None:
        return {"status": "unknown", "reason": f"{reason}，且起息日缺失无法判断新发债豁免"}
    return {"status": "fail", "reason": reason}
