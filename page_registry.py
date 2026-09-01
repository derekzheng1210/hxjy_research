"""门户页面目录与后台显隐配置。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from paths import PAGE_VISIBILITY_FILE


PAGE_SECTIONS = (
    {
        "key": "management",
        "title": "管理与协同",
        "pages": (
            {"key": "internal_knowledge_base", "endpoint": "internal_knowledge_base.index", "title": "内部知识库", "description": "统一管理内部研究成果、团队评阅、意见反馈与知识检索。", "meta_key": None},
        ),
    },
    {
        "key": "interest_rate",
        "title": "利率债研究",
        "pages": (
            {"key": "rate_spread", "endpoint": "spread.index", "title": "超长端利率利差跟踪", "description": "跟踪国债超长端期限利差与地方债品种利差。", "meta_key": "rate_spread"},
            {"key": "rate_bond_switch", "endpoint": "bond_switch.index", "title": "新老券利差跟踪", "description": "观察30年国债活跃券分层、新老券与税收利差。", "meta_key": "rate_bond_switch"},
            {"key": "rate_issuance", "endpoint": "issuance.index", "title": "国债·地方债发行跟踪", "description": "分析政府债限额进度、发行节奏与超长期供给。", "meta_key": "rate_issuance"},
        ),
    },
    {
        "key": "credit",
        "title": "信用债研究",
        "pages": (
            {"key": "secondary_bond_picker", "endpoint": "secondary_bond_picker", "title": "二级择券工具", "description": "融合中债估值与经纪商挂盘，提供情绪、推荐名单和交易筛选。", "meta_key": "bond_picker"},
            {"key": "bond_picker", "endpoint": "bond_picker", "title": "收益率倒挂挖掘工具", "description": "寻找同主体期限倒挂与收益率曲线凸点。", "meta_key": "bond_picker"},
            {"key": "strategy_dashboard", "endpoint": "strategy_dashboard", "title": "信用骑乘策略", "description": "识别当前市场占优的信用骑乘与期限策略。", "meta_key": "strategy_dashboard"},
            {"key": "spread_monitor", "endpoint": "spread_monitor", "title": "存量债利差监控", "description": "查看全市场与重点债券的利差变化。", "meta_key": "spread_monitor"},
            {"key": "credit_std_dev", "endpoint": "credit_std_dev", "title": "信用债两倍标准差", "description": "跟踪信用利差相对均值与标准差区间的位置。", "meta_key": "credit_std_dev"},
            {"key": "primary_market_pricing", "endpoint": "primary_market_pricing.index", "title": "一级发行研究", "description": "分析一级发行定价偏离、非市场化比例与发飞情况。", "meta_key": "primary_market_pricing"},
            {"key": "bond_detail", "endpoint": "bond_detail", "title": "债券详查", "description": "集中查看单券的评级位置、主体曲线、骑乘收益、报价和利差。", "meta_key": None, "default_visible": False},
        ),
    },
    {
        "key": "institution",
        "title": "机构行为",
        "pages": (
            {"key": "institution_flow", "endpoint": "institution_flow", "title": "机构行为监测", "description": "观察机构净买入，并叠加利率、信用收益率与利差走势。", "meta_key": "institution_flow"},
        ),
    },
    {
        "key": "macro",
        "title": "中观研究",
        "pages": (
            {"key": "industry_prosperity", "endpoint": "industry_prosperity", "title": "行业景气度跟踪", "description": "跟踪行业景气变化与中观基本面线索。", "meta_key": "industry_prosperity"},
            {"key": "ipm_tracker", "endpoint": "ipm_tracker.whale_dashboard", "title": "行业景气高频跟踪", "description": "跟踪消费、金融地产、科技、制造、周期与公用行业的高频指标及异动。", "meta_key": "ipm_tracker"},
        ),
    },
)


def all_pages() -> list[dict]:
    return [dict(page, section=section["title"]) for section in PAGE_SECTIONS for page in section["pages"]]


def load_page_visibility() -> dict[str, bool]:
    defaults = {page["key"]: bool(page.get("default_visible", True)) for page in all_pages()}
    try:
        raw = json.loads(PAGE_VISIBILITY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    for key in defaults:
        if key in raw:
            defaults[key] = bool(raw[key])
    return defaults


def save_page_visibility(visible_keys) -> dict[str, bool]:
    selected = {str(key) for key in visible_keys}
    payload = {page["key"]: page["key"] in selected for page in all_pages()}
    PAGE_VISIBILITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".page-visibility-", suffix=".json", dir=PAGE_VISIBILITY_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, PAGE_VISIBILITY_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return payload


def visible_sections(visibility: dict[str, bool] | None = None) -> list[dict]:
    visibility = visibility or load_page_visibility()
    result = []
    for section in PAGE_SECTIONS:
        pages = [
            dict(page)
            for page in section["pages"]
            if visibility.get(page["key"], bool(page.get("default_visible", True)))
        ]
        if pages:
            result.append({**section, "pages": pages})
    return result
