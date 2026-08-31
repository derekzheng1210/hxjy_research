from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime


SPACE_RE = re.compile(r"\s+")
PHASE_RE = re.compile(r"[\(\[（【]([\u4e00-\u9fa5零〇一二两三四五六七八九十百千]+)期[\)\]）】]")
NEXT_PHASE_RE = re.compile(r"[\(\[（【][\u4e00-\u9fa5零〇一二两三四五六七八九十百千]+期[\)\]）】]")

SPECIAL_REFI_PATTERNS = (
    ("偿还存量债务", re.compile(r"偿还[^。；;,，]{0,40}存量债务")),
    ("置换存量隐性债务", re.compile(r"置换存量隐性债务")),
    ("偿还存量隐性债务", re.compile(r"偿还存量隐性债务")),
    (
        "用于政府存量债务",
        re.compile(r"(?:用于|拟用于|计划用于)[^。；;,，]{0,30}(?:政府)?存量债务"),
    ),
)

ORDINARY_REFI_RE = re.compile(
    r"(?:用于|拟用于|计划用于|专项用于)?偿还[^。；;]{0,100}"
    r"(?:到期|即将到期|分期还本)[^。；;]{0,80}(?:债券|本金|债务)"
)


@dataclass(frozen=True)
class LocalClassification:
    category: str | None
    include_special_refi: bool
    reason: str


def normalize_text(value: object) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def purpose_special_match(purpose: str) -> tuple[bool, str]:
    text = normalize_text(purpose)
    for label, pattern in SPECIAL_REFI_PATTERNS:
        if pattern.search(text):
            return True, label
    return False, ""


def _phase_token(name: str) -> str | None:
    matches = list(PHASE_RE.finditer(normalize_text(name)))
    return matches[-1].group(1) if matches else None


def phase_specific_special_decision(name: str, purpose: str) -> tuple[bool | None, str]:
    """当同一批用途文本明确按期次分配时，优先使用本券期次所在片段。"""
    phase = _phase_token(name)
    text = normalize_text(purpose)
    if not phase or not text:
        return None, ""
    token_re = re.compile(rf"[\(\[（【]{re.escape(phase)}期[\)\]）】]")
    decisions: list[tuple[bool, str]] = []
    for match in token_re.finditer(text):
        tail = text[match.end() :]
        next_match = NEXT_PHASE_RE.search(tail)
        end = match.end() + (next_match.start() if next_match else 280)
        # 从本期次标记开始取段，避免把前一期的用途带入。
        segment = text[match.start() : min(len(text), end)]
        special, label = purpose_special_match(segment)
        ordinary = bool(ORDINARY_REFI_RE.search(segment))
        if special and not ordinary:
            decisions.append((True, f"按{phase}期片段命中{label}"))
        elif ordinary and not special:
            decisions.append((False, f"按{phase}期片段为到期债券再融资"))
    if decisions:
        # 描述可能先在批次列表出现，后面再给出真正用途，优先最后一个明确决定。
        return decisions[-1]
    return None, ""


def classify_local_bond(name: str, purpose: str) -> LocalClassification:
    bond_name = normalize_text(name)
    use = normalize_text(purpose)
    if "再融资" not in bond_name:
        if "一般债券" in bond_name:
            return LocalClassification("地方新增一般债", False, "全称含一般债券且不含再融资")
        if "专项债券" in bond_name:
            return LocalClassification("地方新增专项债", False, "全称含专项债券且不含再融资")
        return LocalClassification(None, False, "非五类政府债")

    phase_decision, phase_reason = phase_specific_special_decision(bond_name, use)
    if phase_decision is True:
        return LocalClassification("地方特殊再融资债", True, phase_reason)
    if phase_decision is False:
        return LocalClassification(None, False, phase_reason)

    special, label = purpose_special_match(use)
    if special:
        mixed = bool(ORDINARY_REFI_RE.search(use))
        reason = f"用途命中{label}"
        if mixed:
            reason += "；未披露可拆分金额，按整只计入"
        return LocalClassification("地方特殊再融资债", True, reason)
    if "偿还存量政府债务" in use or "置换存量政府债务" in use:
        return LocalClassification(None, False, "存量政府债务不属于特殊再融资口径")
    return LocalClassification(None, False, "再融资债用途未命中特殊再融资规则")


def classify_treasury(name: str) -> str:
    return "特别国债" if "特别国债" in normalize_text(name) else "一般国债"


def maturity_years(
    value: object,
    unit: object,
    issue_date: date | datetime | None,
    maturity_date: date | datetime | None,
) -> float | None:
    try:
        if value is not None and int(unit or 1) == 1:
            return float(value)
    except (TypeError, ValueError):
        pass
    if issue_date and maturity_date:
        issue = issue_date.date() if isinstance(issue_date, datetime) else issue_date
        maturity = maturity_date.date() if isinstance(maturity_date, datetime) else maturity_date
        return round((maturity - issue).days / 365.25, 4)
    return None
