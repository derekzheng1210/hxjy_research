import json
import gzip
import hmac
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, session, url_for
from juyuan_update import config as juyuan_config
from juyuan_update.tasks import get_status as get_update_status, start_update
from juyuan_update.unified_excel import (
    get_bond_picker_bonds,
    load_bond_picker_yields_cache,
    load_bond_static,
    load_counterparty_limits,
)
from juyuan_update.oracle_bonds import load_oracle_reconciliation
from juyuan_update.rating_compliance import (
    evaluate_rating_compliance,
    load_rating_facts_cache,
    refresh_rating_compliance_cache,
)
from juyuan_update.neiping_portal_fetch import load_portal_data
import llm_config
from primary_market_pricing.app import pricing_bp
from internal_knowledge_base import bp as internal_knowledge_base_bp
from ipm_tracker import bp as ipm_tracker_bp, init_app as init_ipm_tracker
from ipm_tracker.routes import CACHE_FILE as IPM_TRACKER_CACHE
from interest_bond import (
    bond_switch_bp,
    init_bond_switch,
    init_issuance,
    init_spread,
    issuance_bp,
    spread_bp,
)
from interest_bond import settings as interest_bond_settings
from interest_bond.bond_switch import db as interest_bond_switch_db
from interest_bond.issuance import database as interest_issuance_db
from interest_bond.spread import db as interest_spread_db
from page_registry import PAGE_SECTIONS, load_page_visibility, save_page_visibility, visible_sections
import institution_flow_config
import institution_flow_data
from bond_detail import build_bond_detail, search_bonds
from broker_market import (
    MARKET_DIR,
    data_version as bond_picker_data_version,
    ensure_directories as ensure_broker_directories,
    load_emotion_history,
    load_preferences as load_broker_preferences,
    load_snapshot as load_broker_snapshot,
    merge_bond_rows,
    record_market_emotion,
    public_status as broker_scheduler_status,
    save_preferences as save_broker_preferences,
    start_scheduler as start_broker_scheduler,
    trigger_update as trigger_broker_update,
)

# 运态数据路径统一由 paths.py 管理（PORTAL_DATA_ROOT 环境变量定位）
from paths import (
    BASE_DIR,
    DATA_DIR,
    BOND_DIR,
    STRATEGY_DIR,
    SPREAD_DIR,
    INDUSTRY_DIR,
    STD_DEV_DIR,
    CONFIG_DIR,
    UPLOADS_DIR,
    PRIMARY_PRICING_CACHE,
)

STRATEGY_HTML = STRATEGY_DIR / "信用债策略仪表盘.html"
SPREAD_JS = SPREAD_DIR / "spread_data.js"
INDUSTRY_HTML = INDUSTRY_DIR / "行业景气度跟踪.html"
STD_DEV_HTML = STD_DEV_DIR / "index.html"
STD_DEV_JS = STD_DEV_DIR / "data" / "spread_data.js"
INSTITUTION_FLOW_CACHE = institution_flow_config.RATE_CURVES_CACHE
MAPPING_FILE = CONFIG_DIR / "映射表.xlsx"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-before-deploy")

# 未显式配置敏感项时在启动日志中提醒，避免默认值被带到公网部署
for _warn_var, _warn_default in (
    ("SECRET_KEY", app.secret_key),
    ("SITE_PASSWORD", os.environ.get("SITE_PASSWORD")),
    ("ADMIN_PASSWORD", os.environ.get("ADMIN_PASSWORD")),
    ("INSTITUTION_FLOW_UPSTREAM", os.environ.get("INSTITUTION_FLOW_UPSTREAM")),
):
    if not _warn_var or _warn_default is None:
        print(f"[安全提醒] 环境变量 {_warn_var} 未设置，正在使用代码内置默认值；公网部署前请在 .env 或服务环境中显式配置。")

app.register_blueprint(pricing_bp, url_prefix="/primary-market-pricing")
app.register_blueprint(internal_knowledge_base_bp, url_prefix="/internal-knowledge-base")
app.register_blueprint(spread_bp)
app.register_blueprint(bond_switch_bp)
app.register_blueprint(issuance_bp)
app.register_blueprint(ipm_tracker_bp, url_prefix="/ipm-tracker")

BONDS_CACHE = []
DATA_TIMESTAMP = "尚未加载"
INSTITUTION_FLOW_UPSTREAM = os.environ.get("INSTITUTION_FLOW_UPSTREAM", "http://43.137.12.140:8000/bondflow/api").rstrip("/")
INSTITUTION_FLOW_UPSTREAM_PAGE = os.environ.get("INSTITUTION_FLOW_UPSTREAM_PAGE", "http://43.137.12.140:8000/jgxw/").rstrip("/")
INSTITUTION_FLOW_TIMEOUT = 120
INSTITUTION_FLOW_OPTIONS_TTL = 300
INSTITUTION_FLOW_QUERY_TTL = int(os.environ.get("INSTITUTION_FLOW_QUERY_TTL", "30"))
INSTITUTION_FLOW_CACHE_MAX = int(os.environ.get("INSTITUTION_FLOW_CACHE_MAX", "128"))
_institution_flow_options = {"timestamp": 0.0, "payload": None}
_institution_flow_cache_lock = threading.Lock()
_institution_flow_cache = {}
_institution_flow_http = requests.Session()
_institution_flow_http.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) credit-tools-portal/1.0",
    "Referer": INSTITUTION_FLOW_UPSTREAM_PAGE,
})

COMPRESS_MIN_SIZE = int(os.environ.get("COMPRESS_MIN_SIZE", "1024"))
COMPRESS_LEVEL = max(1, min(9, int(os.environ.get("COMPRESS_LEVEL", "5"))))
_compress_cache_lock = threading.Lock()
_compress_cache = {}
_compressible_mimetypes = {
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
    "text/css",
    "text/html",
    "text/javascript",
    "text/plain",
    "text/xml",
}


def site_password():
    return os.environ.get("SITE_PASSWORD", "Abcd123%")


def admin_password():
    return os.environ.get("ADMIN_PASSWORD", "123456")


def safe_next_path(value, fallback=None):
    fallback = fallback or url_for("home")
    if not value:
        return fallback
    parsed = urlparse(value)
    return value if not parsed.scheme and not parsed.netloc and value.startswith("/") else fallback


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=request.path))
        return func(*args, **kwargs)
    return wrapper


@app.before_request
def protect_blueprint_pages():
    protected_blueprints = {
        pricing_bp.name,
        spread_bp.name,
        bond_switch_bp.name,
        issuance_bp.name,
        ipm_tracker_bp.name,
    }
    if request.blueprint in protected_blueprints and not session.get("authenticated"):
        return redirect(url_for("login", next=request.path))


@app.context_processor
def portal_page_visibility_context():
    visibility = load_page_visibility()
    return {"page_is_visible": lambda key: visibility.get(key, True)}



def format_date_only(value):
    if not value:
        return ""
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%Y - %m - %d")
        except ValueError:
            pass
    if len(text) >= 10:
        head = text[:10].replace("/", "-")
        parts = head.split("-")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return f"{int(parts[0]):04d} - {int(parts[1]):02d} - {int(parts[2]):02d}"
    return text

def file_updated(path: Path):
    if not path.exists():
        return "文件不存在"
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y - %m - %d")


def file_size(path: Path):
    if not path.exists():
        return "-"
    size = path.stat().st_size
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def file_updated_time(path: Path):
    if not path.exists():
        return "文件不存在"
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y - %m - %d %H:%M")


def read_excel(_path: Path | None = None):
    static_bonds = get_bond_picker_bonds()
    if static_bonds:
        yield_cache = load_bond_picker_yields_cache()
        yields = yield_cache.get("yields") or {}
        bonds = []
        for bond in static_bonds:
            code = str(bond.get("code") or "").strip().upper()
            symbol = code.split(".", 1)[0]
            ytm = yields.get(code)
            if ytm is None:
                ytm = yields.get(symbol)
            if ytm is None:
                continue
            bonds.append([
                bond.get("code") or "",
                bond.get("name") or "",
                round(float(bond.get("term") or 0), 4),
                bond.get("implied_rating") or "",
                bond.get("issuer") or "",
                round(float(ytm), 4),
                bond.get("entity") or "",
                bond.get("ct") or "",
                bond.get("sub") or "",
                bond.get("tech") or "",
                bond.get("internal_rating") or "",
            ])
        data_date = yield_cache.get("trade_date") or load_bond_static().get("generated_at") or ""
        return bonds, data_date
    return [], load_bond_static().get("generated_at") or ""


def load_bond_data():
    global BONDS_CACHE, DATA_TIMESTAMP
    if not juyuan_config.BOND_STATIC_JSON.exists():
        BONDS_CACHE = []
        DATA_TIMESTAMP = "数据文件未找到"
        return
    try:
        bonds, data_date = read_excel()
        BONDS_CACHE = merge_bond_rows(bonds)
        limits = load_counterparty_limits().get("limits") or {}
        rating_cache = load_rating_facts_cache()
        compliance = rating_cache.get("compliance") or {}
        rating_facts = rating_cache.get("facts") or {}
        # 缓存仅每日更新时覆盖；跨日未刷新时按事实现算兜底（不回写缓存）
        cache_current = str(rating_cache.get("as_of_date") or "") == date.today().strftime("%Y-%m-%d")
        today = date.today()
        for row in BONDS_CACHE:
            value = limits.get(str(row[4] or "").strip())
            try:
                row.append(float(value) if value is not None else None)
            except (TypeError, ValueError):
                row.append(None)
            code = str(row[0] or "").strip().upper()
            bare = code.split(".", 1)[0]
            verdict = (compliance.get(code) or compliance.get(bare)) if cache_current else None
            if not verdict:
                fact = rating_facts.get(code) or rating_facts.get(bare) or {}
                result = evaluate_rating_compliance(today, fact)
                verdict = [result["status"], result["reason"]]
            row.append(verdict)
        DATA_TIMESTAMP = format_date_only(data_date) or file_updated(juyuan_config.BOND_STATIC_JSON)
    except Exception as exc:
        BONDS_CACHE = []
        DATA_TIMESTAMP = f"读取失败: {exc}"


def rating_compliance_status():
    cache = load_rating_facts_cache()
    counts = {"ok": 0, "fail": 0, "unknown": 0}
    for verdict in (cache.get("compliance") or {}).values():
        status = verdict[0] if isinstance(verdict, list) and verdict[0] in counts else "unknown"
        counts[status] += 1
    return {
        "missing": not bool(cache.get("facts")),
        "updated": file_updated_time(juyuan_config.RATING_FACTS_CACHE),
        "as_of": str(cache.get("as_of_date") or ""),
        "ok": f"{counts['ok']:,}",
        "fail": f"{counts['fail']:,}",
        "unknown": f"{counts['unknown']:,}",
    }


def portal_data_status():
    """信评门户同步的内评 / 对手限额 / 债项持仓缓存状态。"""
    payload = load_portal_data()
    if not payload:
        return {"missing": True, "updated": "-"}
    holdings = payload.get("holdings") or {}
    holding_count = sum(1 for b in holdings.values() if isinstance(b, dict) and b.get("is_holding"))
    return {
        "missing": False,
        "updated": payload.get("generated_at") or "-",
        "limits_date": payload.get("limits_date") or "-",
        "limit_issuers": len(payload.get("limits") or {}),
        "rating_issuers": len(payload.get("ratings") or {}),
        "holdings_date": payload.get("holdings_refresh_date") or "-",
        "holding_bonds": holding_count,
        "total_bonds": len(holdings),
    }


def status_info():
    load_bond_data()
    static_payload = load_bond_static()
    reconciliation = load_oracle_reconciliation()
    broker_snapshot = load_broker_snapshot()
    return {
        "bond_picker": {
            "updated": DATA_TIMESTAMP,
            "total": f"{len(BONDS_CACHE):,}",
        },
        "rating_compliance": rating_compliance_status(),
        "portal_data": portal_data_status(),
        "strategy_dashboard": {
            "updated": file_updated(STRATEGY_HTML),
            "size": file_size(STRATEGY_HTML),
        },
        "spread_monitor": {
            "updated": file_updated(SPREAD_JS),
            "size": file_size(SPREAD_JS),
        },
        "industry_prosperity": {
            "updated": file_updated(INDUSTRY_HTML),
            "size": file_size(INDUSTRY_HTML),
        },
        "ipm_tracker": {
            "updated": file_updated_time(Path(IPM_TRACKER_CACHE)),
            "size": file_size(Path(IPM_TRACKER_CACHE)),
        },
        "credit_std_dev": {
            "updated": file_updated(STD_DEV_HTML),
            "size": file_size(STD_DEV_HTML),
            "data_updated": file_updated(STD_DEV_JS),
            "data_size": file_size(STD_DEV_JS),
        },
        "primary_market_pricing": {
            "updated": file_updated_time(PRIMARY_PRICING_CACHE),
            "size": file_size(PRIMARY_PRICING_CACHE),
        },
        "institution_flow": {
            "updated": file_updated_time(INSTITUTION_FLOW_CACHE),
            "size": file_size(INSTITUTION_FLOW_CACHE),
        },
        "fund_index": {
            "updated": file_updated_time(juyuan_config.STRATEGY_FUND_PRICES_FROZEN),
            "size": file_size(juyuan_config.STRATEGY_FUND_PRICES_FROZEN),
        },
        "oracle_bonds": {
            "updated": static_payload.get("generated_at") or "-",
            "as_of": static_payload.get("as_of_date") or reconciliation.get("as_of_date") or "-",
            "total": static_payload.get("total_bonds", 0),
            "source": static_payload.get("source_file") or "-",
            "review_required": bool(reconciliation.get("review_required")),
            "comparison": reconciliation.get("comparison") or {},
        },
        "broker_market": broker_scheduler_status(),
        "broker_snapshot": {
            "updated": broker_snapshot.get("generated_at") or "-",
            "quotes": int(broker_snapshot.get("quote_count") or 0),
        },
        "rate_spread": {
            "updated": file_updated(interest_bond_settings.DATA_DIR / "spreads.db"),
            "size": file_size(interest_bond_settings.DATA_DIR / "spreads.db"),
        },
        "rate_bond_switch": {
            "updated": file_updated(interest_bond_settings.DATA_DIR / "bond_switch.db"),
            "size": file_size(interest_bond_settings.DATA_DIR / "bond_switch.db"),
        },
        "rate_issuance": {
            "updated": file_updated(interest_bond_settings.DATA_DIR / "tracker.db"),
            "size": file_size(interest_bond_settings.DATA_DIR / "tracker.db"),
        },
    }


def portal_nav(active_endpoint: str):
    sections = visible_sections()
    items = ['<style id="portal-nav-style">']
    items.append(
        ".portal-nav{position:sticky;top:0;z-index:10000;background:#fff;color:#1e293b;"
        "height:44px;display:flex;align-items:center;gap:4px;padding:0 18px;"
        "border-bottom:1px solid #dbe3ee;white-space:nowrap;font-family:-apple-system,BlinkMacSystemFont,"
        '"Segoe UI","Microsoft YaHei",sans-serif;box-sizing:border-box}'
        ".portal-nav *{box-sizing:border-box}"
        ".portal-nav .brand{font-weight:800;color:#1d4f91;font-size:14px;margin-right:12px;letter-spacing:.4px;text-decoration:none}"
        ".portal-nav a{color:#334155;text-decoration:none;font-size:12px;font-weight:600;"
        "padding:0 11px;height:30px;display:inline-flex;align-items:center;border-radius:5px;transition:all .15s}"
        ".portal-nav a:hover{color:#2563eb;background:#eff6ff}"
        ".portal-nav a.active{color:#2563eb;background:#eff6ff;font-weight:700}"
        ".portal-nav-groups{display:flex;align-items:center;gap:2px;min-width:0;flex:1}"
        ".portal-nav-knowledge{margin-right:8px!important;padding-right:16px!important;border-right:1px solid #cbd5e1;border-radius:5px 0 0 5px!important;color:#1d4f91!important}"
        ".portal-nav-group{position:relative;height:44px;display:flex;align-items:center}"
        ".portal-nav-group>button{height:30px;padding:0 11px;border:0;border-radius:5px;background:transparent;color:#334155;font-family:inherit;font-size:12px;font-weight:600;cursor:pointer}"
        ".portal-nav-group.active>button,.portal-nav-group:hover>button{color:#2563eb;background:#eff6ff}"
        ".portal-nav-menu{display:none;position:absolute;top:38px;left:0;min-width:230px;padding:7px;background:#fff;border:1px solid #dbe3ee;border-radius:8px;box-shadow:0 14px 35px rgba(15,23,42,.16)}"
        ".portal-nav-group:hover .portal-nav-menu,.portal-nav-group:focus-within .portal-nav-menu{display:grid}"
        ".portal-nav-menu a{height:auto;min-height:34px;padding:8px 10px;white-space:normal}"
        ".portal-nav-admin{margin-left:auto!important;color:#475569!important;border-left:1px solid #cbd5e1;border-radius:0 5px 5px 0!important;padding-left:16px!important}"
        "@media(max-width:900px){.portal-nav{overflow-x:auto}.portal-nav-groups{flex:none}.portal-nav-menu{position:fixed;top:42px;left:auto}.portal-nav-admin{margin-left:8px!important}}"
    )
    items.append("</style>")
    items.append('<!-- CREDIT_TOOLS_PORTAL_NAV -->')
    items.append('<div class="portal-nav" data-portal-nav="credit-tools">')
    items.append(f'<a class="brand" href="{url_for("home")}">内部研究工作台</a>')
    items.append('<div class="portal-nav-groups">')
    for section in sections:
        if section["key"] == "management":
            page = section["pages"][0]
            active_class = " active" if page["endpoint"] == active_endpoint else ""
            items.append(f'<a class="portal-nav-knowledge{active_class}" href="{url_for(page["endpoint"])}">内部知识库</a>')
            continue
        section_active = any(page["endpoint"] == active_endpoint for page in section["pages"])
        group_class = "portal-nav-group active" if section_active else "portal-nav-group"
        items.append(f'<div class="{group_class}"><button type="button">{section["title"]} ▾</button><div class="portal-nav-menu">')
        for page in section["pages"]:
            active_class = ' class="active"' if page["endpoint"] == active_endpoint else ""
            items.append(f'<a href="{url_for(page["endpoint"])}"{active_class}>{page["title"]}</a>')
        items.append('</div></div>')
    items.append('</div>')
    items.append(f'<a class="portal-nav-admin" href="{url_for("admin")}">后台上传</a>')
    items.append("</div>")
    return "".join(items)


def find_matching_div_end(html: str, start: int):
    depth = 0
    for match in re.finditer(r"</?div\b[^>]*>", html[start:], flags=re.I):
        token = match.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                return start + match.end()
        else:
            depth += 1
    return -1


def remove_portal_nav_divs(html: str):
    pattern = re.compile(
        r"<div\b(?=[^>]*(?:data-portal-nav=|class=[\"'][^\"']*\bportal-nav\b))[^>]*>",
        flags=re.I,
    )
    result = html
    search_from = 0
    while True:
        match = pattern.search(result, search_from)
        if not match:
            return result
        end = find_matching_div_end(result, match.start())
        if end == -1:
            search_from = match.end()
            continue
        result = result[:match.start()] + result[end:]
        search_from = match.start()


def remove_portal_nav_css(html: str):
    cleaned = re.sub(r"<style\b[^>]*id=[\"']portal-nav-style[\"'][^>]*>.*?</style>\s*", "", html, flags=re.I | re.S)
    cleaned = re.sub(r"\s*\.portal-nav[^{}]*\{[^{}]*\}", "", cleaned, flags=re.I | re.S)
    return cleaned


def insert_portal_nav(html: str, nav: str):
    head_match = re.search(r"</head\s*>", html, flags=re.I)
    body_search_start = head_match.end() if head_match else 0
    body_match = re.search(r"<body\b[^>]*>", html[body_search_start:], flags=re.I)
    if body_match:
        insert_at = body_search_start + body_match.end()
        return html[:insert_at] + nav + html[insert_at:]
    if head_match:
        return html[:head_match.end()] + nav + html[head_match.end():]
    return nav + html


def inject_portal_nav(html: str, active_endpoint: str):
    cleaned = re.sub(r"<!-- CREDIT_TOOLS_PORTAL_NAV -->\s*", "", html, flags=re.I)
    cleaned = remove_portal_nav_divs(cleaned)
    cleaned = remove_portal_nav_css(cleaned)
    return insert_portal_nav(cleaned, portal_nav(active_endpoint))


def render_portal_template(template_name: str, active_endpoint: str, **context):
    html = render_template(template_name, **context)
    return Response(inject_portal_nav(html, active_endpoint), content_type="text/html; charset=utf-8")


# 注入导航栏后的 HTML 缓存：仪表盘 HTML（如策略仪表盘 ~7MB）每次请求都全量读盘 +
# 全量正则注入极其昂贵，而文件仅在"一键更新"重写后才变化。按 mtime 缓存最终结果，
# 文件未变时直接复用，省去读盘与正则扫描。
_html_cache_lock = threading.Lock()
_html_cache = {}  # key -> {"mtime": float, "html": str}


def html_response(path: Path, active_endpoint: str, missing_message: str, replacements=None):
    if not path.exists():
        return Response(missing_message, status=404, content_type="text/plain; charset=utf-8")
    try:
        stat = path.stat()
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        mtime = None
        size = None

    cache_key = (
        str(path),
        active_endpoint,
        tuple(sorted(replacements.items())) if replacements else None,
        tuple(load_page_visibility().items()),
    )

    html = None
    with _html_cache_lock:
        ent = _html_cache.get(cache_key)
        if ent and ent["mtime"] == mtime:
            html = ent["html"]

    if html is None:
        html = path.read_text(encoding="utf-8", errors="replace")
        if replacements:
            for old, new in replacements.items():
                html = html.replace(old, new)
        html = inject_portal_nav(html, active_endpoint)
        with _html_cache_lock:
            _html_cache[cache_key] = {"mtime": mtime, "html": html}

    # HTML 每次先校验 ETag，命中时仅回 304；避免导航显隐或新增入口后仍显示旧菜单。
    etag = f'"{active_endpoint}-{int(mtime or 0)}-{size or 0}"'
    resp = Response(html, content_type="text/html; charset=utf-8")
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "private, no-cache"
    resp.make_conditional(request)
    return resp


@app.after_request
def compress_text_response(response):
    """Gzip sizeable text responses when the reverse proxy did not compress them."""
    if (
        request.method == "HEAD"
        or response.status_code < 200
        or response.status_code in {204, 205, 304}
        or response.headers.get("Content-Encoding")
        or "gzip" not in request.accept_encodings
        or request.accept_encodings["gzip"] <= 0
        or response.mimetype not in _compressible_mimetypes
    ):
        return response

    content_length = response.content_length
    if content_length is not None and content_length < COMPRESS_MIN_SIZE:
        return response

    cache_key = None
    etag = response.headers.get("ETag")
    if etag and content_length:
        cache_key = (etag, content_length, response.mimetype, COMPRESS_LEVEL)
        with _compress_cache_lock:
            compressed = _compress_cache.get(cache_key)
        if compressed is not None:
            response.direct_passthrough = False
            response.set_data(compressed)
            response.headers["Content-Encoding"] = "gzip"
            response.vary.add("Accept-Encoding")
            if etag and not etag.startswith("W/"):
                response.headers["ETag"] = f"W/{etag}"
            return response

    response.direct_passthrough = False
    payload = response.get_data()
    if len(payload) < COMPRESS_MIN_SIZE:
        return response
    compressed = gzip.compress(payload, compresslevel=COMPRESS_LEVEL)
    if len(compressed) >= len(payload):
        return response

    if cache_key is not None:
        with _compress_cache_lock:
            if len(_compress_cache) >= 32:
                _compress_cache.pop(next(iter(_compress_cache)))
            _compress_cache[cache_key] = compressed
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.vary.add("Accept-Encoding")
    if etag and not etag.startswith("W/"):
        response.headers["ETag"] = f"W/{etag}"
    return response


@app.after_request
def inject_blueprint_portal_nav(response):
    active_by_blueprint = {
        pricing_bp.name: "primary_market_pricing.index",
        spread_bp.name: "spread.index",
        bond_switch_bp.name: "bond_switch.index",
        issuance_bp.name: "issuance.index",
        ipm_tracker_bp.name: "ipm_tracker.whale_dashboard",
    }
    active_endpoint = active_by_blueprint.get(request.blueprint)
    if not active_endpoint or not response.content_type.startswith("text/html"):
        return response
    response.set_data(inject_portal_nav(response.get_data(as_text=True), active_endpoint))
    return response

BACKUP_RETENTION_COUNT = 3


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prune_upload_backups(backup_key: str, suffix: str) -> None:
    backups = sorted(
        UPLOADS_DIR.glob(f"{backup_key}_backup_*{suffix}"),
        key=lambda path: path.name,
        reverse=True,
    )
    for backup in backups[BACKUP_RETENTION_COUNT:]:
        backup.unlink(missing_ok=True)


def save_upload(file_storage, destination: Path, allowed_exts, backup_key: str) -> bool:
    """Replace an uploaded file atomically and retain recent distinct versions."""
    filename = file_storage.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in allowed_exts:
        raise ValueError(f"文件类型不正确，仅支持: {', '.join(sorted(allowed_exts))}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{backup_key}-", suffix=".upload", dir=destination.parent)
    os.close(fd)
    staged = Path(tmp_name)
    try:
        file_storage.save(staged)
        if destination.exists() and file_sha256(staged) == file_sha256(destination):
            return False
        backup_created = False
        if destination.exists():
            # 部分机器 datetime.now() 微秒粒度只有几毫秒，连续上传会产生同名备份
            # 互相覆盖；改用纳秒尾数避免碰撞，同时保持文件名按时间排序。
            timestamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000_000:09d}"
            backup_path = UPLOADS_DIR / f"{backup_key}_backup_{timestamp}{destination.suffix}"
            shutil.copy2(destination, backup_path)
            backup_created = True
        os.replace(staged, destination)
        if backup_created:
            prune_upload_backups(backup_key, destination.suffix)
        return True
    finally:
        staged.unlink(missing_ok=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        if request.form.get("password", "") == site_password():
            session["authenticated"] = True
            session["login_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return redirect(safe_next_path(request.args.get("next")))
        error = "访问密码错误"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/login", methods=["GET", "POST"])
@login_required
def admin_login():
    next_path = safe_next_path(request.args.get("next"), url_for("admin"))
    if session.get("admin_authenticated"):
        return redirect(next_path)
    error = None
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if hmac.compare_digest(submitted, admin_password()):
            session["admin_authenticated"] = True
            session["admin_login_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return redirect(next_path)
        error = "后台密码错误"
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
@login_required
def admin_logout():
    session.pop("admin_authenticated", None)
    session.pop("admin_login_time", None)
    return redirect(url_for("home"))


@app.route("/")
@login_required
def home():
    return render_portal_template(
        "home.html",
        "home",
        status=status_info(),
        sections=visible_sections(),
    )


@app.route("/bond-detail")
@login_required
def bond_detail():
    return render_portal_template(
        "bond_detail.html",
        "bond_detail",
        initial_code=str(request.args.get("code") or "").strip(),
    )


@app.route("/api/bond-detail/search")
@login_required
def api_bond_detail_search():
    query = str(request.args.get("q") or "").strip()
    if not query:
        return jsonify({"items": []})
    return jsonify({"items": search_bonds(query, request.args.get("limit", 20))})


@app.route("/api/bond-detail/<path:code>")
@login_required
def api_bond_detail(code):
    try:
        exclude_exchange_tech = str(request.args.get("exclude_exchange_tech", "1")).lower() not in {
            "0", "false", "no", "off",
        }
        horizon_months = int(request.args.get("horizon_months", 3))
        payload = build_bond_detail(
            code,
            exclude_exchange_tech=exclude_exchange_tech,
            horizon_months=horizon_months,
        )
    except KeyError as exc:
        return jsonify({"error": str(exc).strip("'")}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        return jsonify({"error": f"债券诊断加载失败: {str(exc)[:240]}"}), 500
    response = jsonify(payload)
    response.set_etag(payload["version"])
    response.headers["Cache-Control"] = "private, no-cache"
    return response


@app.route("/bond-picker")
@login_required
def bond_picker():
    load_bond_data()
    market_meta = bond_picker_market_meta(include_emotion=True)
    version = bond_picker_data_version()
    return render_portal_template(
        "bond_picker.html",
        "bond_picker",
        bond_data=json.dumps(BONDS_CACHE, ensure_ascii=False),
        market_meta=json.dumps(market_meta, ensure_ascii=False),
        data_version=version,
        timestamp=DATA_TIMESTAMP,
        total=len(BONDS_CACHE),
    )


@app.route("/secondary-bond-picker")
@login_required
def secondary_bond_picker():
    load_bond_data()
    market_meta = bond_picker_market_meta(include_emotion=True)
    version = bond_picker_data_version()
    return render_portal_template(
        "secondary_bond_picker.html",
        "secondary_bond_picker",
        bond_data=json.dumps(BONDS_CACHE, ensure_ascii=False),
        market_meta=json.dumps(market_meta, ensure_ascii=False),
        data_version=version,
        timestamp=DATA_TIMESTAMP,
        total=len(BONDS_CACHE),
    )


def _ensure_latest_emotion(snapshot):
    generated_at = str(snapshot.get("generated_at") or "")
    history = load_emotion_history()
    matching = next(
        (p for p in history.get("points") or [] if p.get("observed_at") == generated_at),
        None,
    )
    if not generated_at or (matching and "tier2_capital" in matching):
        return history
    try:
        scheduled_for = None
        if matching and matching.get("scheduled_for"):
            scheduled_for = datetime.strptime(matching["scheduled_for"], "%Y-%m-%d %H:%M:%S")
        record_market_emotion(snapshot, scheduled_for=scheduled_for)
    except Exception:
        return history
    return load_emotion_history()


def bond_picker_market_meta(include_emotion=True):
    snapshot = load_broker_snapshot()
    scheduler = broker_scheduler_status()
    broker_state = scheduler.get("broker", {})
    valuation_state = scheduler.get("bond_picker", {})
    payload = {
        "valuation_date": DATA_TIMESTAMP,
        "broker_snapshot_at": snapshot.get("generated_at") or "尚无经纪商快照",
        "broker_quote_count": int(snapshot.get("quote_count") or 0),
        "broker_state": broker_state.get("state") or "idle",
        "broker_stale": bool(broker_state.get("stale")),
        "broker_error": broker_state.get("last_error") or "",
        "broker_next_run": broker_state.get("next_run") or "",
        "broker_attempt": int(broker_state.get("attempt") or 0),
        "broker_scheduled_for": broker_state.get("scheduled_for") or "",
        "valuation_state": valuation_state.get("state") or "idle",
        "valuation_stale": bool(valuation_state.get("stale")),
        "valuation_error": valuation_state.get("last_error") or "",
        "valuation_attempt": int(valuation_state.get("attempt") or 0),
        "valuation_scheduled_for": valuation_state.get("scheduled_for") or "",
        "rating_compliance": rating_compliance_status(),
    }
    if include_emotion:
        history = _ensure_latest_emotion(snapshot)
        points = history.get("points") or []
        payload["emotion"] = points[-1] if points else {"value": None, "count": 0, "breakdown": {}}
        payload["emotion_history"] = points
        payload["emotion_history_version"] = history.get("version") or ""
    return payload


@app.route("/api/bond-picker/data")
@app.route("/api/secondary-bond-picker/data")
@login_required
def api_bond_picker_data():
    _ensure_latest_emotion(load_broker_snapshot())
    version = bond_picker_data_version()
    if request.if_none_match.contains(version):
        response = Response(status=304)
        response.set_etag(version)
        return response
    load_bond_data()
    response = jsonify({
        "version": version,
        "bonds": BONDS_CACHE,
        "meta": bond_picker_market_meta(),
    })
    response.set_etag(version)
    response.headers["Cache-Control"] = "private, no-cache"
    return response


@app.route("/api/bond-picker/preferences", methods=["GET", "PUT"])
@app.route("/api/secondary-bond-picker/preferences", methods=["GET", "PUT"])
@login_required
def api_bond_picker_preferences():
    if request.method == "GET":
        return jsonify(load_broker_preferences())
    try:
        return jsonify(save_broker_preferences(request.get_json(silent=False)))
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/strategy-dashboard")
@login_required
def strategy_dashboard():
    local_echarts = url_for("static", filename="vendor/echarts.min.js")
    return html_response(
        STRATEGY_HTML,
        "strategy_dashboard",
        "信用骑乘策略 HTML 文件不存在，请先执行后台一键更新。",
        replacements={
            "信用债策略仪表盘": "信用骑乘策略",
            "骑乘效应与配置策略仪表盘": "信用骑乘策略",
            "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js": local_echarts,
            "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js": local_echarts,
            "</head>": "<style id=\"portal-strategy-colors\">.header{background:#fff!important;border-bottom:1px solid #dfe6ef!important;box-shadow:0 2px 10px rgba(15,23,42,.06)!important}.header h1{background:none!important;color:#1d4f91!important;-webkit-text-fill-color:#1d4f91!important}</style></head>",
        },
    )


@app.route("/spread-monitor")
@login_required
def spread_monitor():
    return render_portal_template("spread_monitor.html", "spread_monitor")


@app.route("/data/spread_monitor/spread_data.js")
@login_required
def spread_data_js():
    if not SPREAD_JS.exists():
        return Response("var SPREAD_DATA = {data: [], total_bonds: 0, update_time: '数据文件未找到'};", mimetype="application/javascript; charset=utf-8")
    return send_file(SPREAD_JS, mimetype="application/javascript")


@app.route("/institution-flow")
@login_required
def institution_flow():
    return render_portal_template("institution_flow.html", "institution_flow")


def institution_flow_forward_args():
    return [
        (key, value)
        for key, values in request.args.lists()
        for value in values
    ]


@app.route("/institution-flow/bondflow/api/<path:subpath>")
@login_required
def institution_flow_proxy(subpath):
    import time

    is_options = subpath.rstrip("/") == "options" and not request.args
    if is_options:
        cached = _institution_flow_options
        if cached["payload"] is not None and time.time() - cached["timestamp"] < INSTITUTION_FLOW_OPTIONS_TTL:
            return jsonify(cached["payload"])
    cache_key = (subpath.rstrip("/"), tuple(sorted(institution_flow_forward_args())))
    if not is_options and INSTITUTION_FLOW_QUERY_TTL > 0:
        with _institution_flow_cache_lock:
            cached = _institution_flow_cache.get(cache_key)
        if cached and time.time() - cached["timestamp"] < INSTITUTION_FLOW_QUERY_TTL:
            return app.response_class(
                cached["content"], status=cached["status"], content_type=cached["content_type"],
            )
    try:
        response = _institution_flow_http.get(
            f"{INSTITUTION_FLOW_UPSTREAM}/{subpath}",
            params=institution_flow_forward_args(),
            timeout=INSTITUTION_FLOW_TIMEOUT,
        )
    except requests.RequestException as exc:
        return jsonify({"error": f"上游数据请求失败: {exc}"}), 502
    if is_options and response.ok:
        try:
            _institution_flow_options.update(timestamp=time.time(), payload=response.json())
        except ValueError:
            pass
    elif response.ok and INSTITUTION_FLOW_QUERY_TTL > 0:
        with _institution_flow_cache_lock:
            if len(_institution_flow_cache) >= INSTITUTION_FLOW_CACHE_MAX:
                _institution_flow_cache.pop(next(iter(_institution_flow_cache)))
            _institution_flow_cache[cache_key] = {
                "timestamp": time.time(),
                "content": response.content,
                "status": response.status_code,
                "content_type": response.headers.get("Content-Type", "application/json"),
            }
    return app.response_class(
        response.content,
        status=response.status_code,
        content_type=response.headers.get("Content-Type", "application/json"),
    )


@app.route("/institution-flow/api/overlay/meta")
@login_required
def institution_flow_overlay_meta():
    try:
        return jsonify(institution_flow_data.get_meta())
    except Exception as exc:
        return jsonify({"error": f"叠加曲线元数据加载失败: {exc}"}), 500


@app.route("/institution-flow/api/overlay/yield")
@login_required
def institution_flow_overlay_yield():
    curve = request.args.get("curve", "")
    tenor = request.args.get("tenor", "")
    if not curve or not tenor:
        return jsonify({"error": "缺少参数 curve / tenor"}), 400
    try:
        return jsonify(institution_flow_data.get_yield_series(curve, tenor))
    except Exception as exc:
        return jsonify({"error": f"收益率数据加载失败: {exc}"}), 500


@app.route("/institution-flow/api/overlay/spread")
@login_required
def institution_flow_overlay_spread():
    category = request.args.get("category", "")
    tenor = request.args.get("tenor", "")
    if not category or not tenor:
        return jsonify({"error": "缺少参数 category / tenor"}), 400
    try:
        return jsonify(institution_flow_data.get_spread_series(category, tenor))
    except Exception as exc:
        return jsonify({"error": f"利差数据加载失败: {exc}"}), 500


@app.route("/institution-flow/healthz")
@login_required
def institution_flow_healthz():
    return jsonify({"ok": True, "upstream": INSTITUTION_FLOW_UPSTREAM})



@app.route("/industry-prosperity")
@login_required
def industry_prosperity():
    return html_response(INDUSTRY_HTML, "industry_prosperity", "行业景气度跟踪 HTML 文件不存在，请先到后台上传。")


@app.route("/credit-std-dev")
@login_required
def credit_std_dev():
    local_echarts = url_for("static", filename="vendor/echarts.min.js")
    today_focus_css = url_for("static", filename="credit_std_dev/today_focus.css")
    today_focus_js = url_for("static", filename="credit_std_dev/today_focus.js")
    return html_response(
        STD_DEV_HTML,
        "credit_std_dev",
        "信用债两倍标准差 HTML 文件不存在，请先到后台上传。",
        replacements={
            'src="data/spread_data.js"': f'src="{url_for("credit_std_dev_data_js")}"',
            "src='data/spread_data.js'": f"src='{url_for('credit_std_dev_data_js')}'",
            "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js": local_echarts,
            "</head>": f'<link rel="stylesheet" href="{today_focus_css}">\n</head>',
            "</body>": f'<script src="{today_focus_js}"></script>\n</body>',
        },
    )


@app.route("/data/credit_std_dev/spread_data.js")
@login_required
def credit_std_dev_data_js():
    if not STD_DEV_JS.exists():
        return Response("var SPREAD_DATA = {categories: [], tenors_by_category: {}, data: {}, update_time: '数据文件未找到'};", mimetype="application/javascript; charset=utf-8")
    return send_file(STD_DEV_JS, mimetype="application/javascript")
@app.route("/admin", methods=["GET", "POST"])
@login_required
@admin_required
def admin():
    message = None
    error = None
    if request.method == "POST":
        target = request.form.get("target", "")
        if target == "page_visibility":
            save_page_visibility(request.form.getlist("visible_pages"))
            message = "页面显隐设置已保存并即时生效。"
        else:
            file = request.files.get("file")
        if target != "page_visibility" and (not file or not file.filename):
            error = "请选择要上传的文件"
        elif target != "page_visibility":
            try:
                if target == "unified_excel":
                    error = "基金指数已改为每日自动从量化数据库同步，不再支持 Excel 上传"
                elif target == "bond_picker":
                    error = "债券清单由 Oracle 自动更新，不再支持 Excel 上传"
                elif target == "strategy_dashboard":
                    error = "信用骑乘策略 HTML 上传入口已停用，请使用基金指数 Excel 和一键更新"
                elif target == "spread_monitor_js":
                    error = "利差监控 JS 上传入口已停用，请使用一键更新"
                elif target == "spread_monitor_bond_list":
                    error = "存量债券清单由 Oracle 自动更新，不再支持 Excel 上传"
                elif target == "industry_prosperity":
                    save_upload(file, INDUSTRY_HTML, {".html", ".htm"}, "industry_prosperity")
                    message = "行业景气度跟踪 HTML 上传成功。"
                elif target == "credit_std_dev_js":
                    error = "两倍标准差数据由数据库一键更新自动维护，不再支持 JS 上传"
                else:
                    error = "未知上传目标"
            except Exception as exc:
                error = f"上传失败: {exc}"
    return render_portal_template(
        "admin.html",
        "admin",
        status=status_info(),
        update_status=get_update_status(),
        message=message,
        error=error,
        page_sections=PAGE_SECTIONS,
        page_visibility=load_page_visibility(),
    )


@app.route("/admin/start-db-update", methods=["POST"])
@login_required
@admin_required
def admin_start_db_update():
    modules = request.form.getlist("modules")
    ok, message = start_update(modules or None)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"ok": ok, "message": message, "status": get_update_status()}
    return redirect(url_for("admin"))


@app.route("/admin/start-broker-update", methods=["POST"])
@login_required
@admin_required
def admin_start_broker_update():
    ok, message = trigger_broker_update("broker")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"ok": ok, "message": message, "status": broker_scheduler_status()}
    return redirect(url_for("admin"))


@app.route("/admin/refresh-rating-cache", methods=["POST"])
@login_required
@admin_required
def admin_refresh_rating_cache():
    """单独重建合规630评级事实缓存（Oracle 在线时可用，无需跑完整一键更新）。"""
    try:
        payload = refresh_rating_compliance_cache()
        return {"ok": True, "message": f"630评级缓存已重建：{len(payload['facts']):,} 条（基准日 {payload['as_of_date']}）"}
    except Exception as exc:
        return {"ok": False, "message": f"630评级缓存重建失败：{exc}"}, 500


@app.route("/admin/api/llm", methods=["GET"])
@login_required
@admin_required
def admin_llm_overview():
    return {"providers": llm_config.provider_overview(), "priority": llm_config.get_settings()["priority"]}


@app.route("/admin/api/llm/test", methods=["POST"])
@login_required
@admin_required
def admin_llm_test():
    return {"results": llm_config.test_all_providers()}


@app.route("/admin/api/llm/priority", methods=["POST"])
@login_required
@admin_required
def admin_llm_priority():
    data = request.get_json(silent=True) or {}
    priority = data.get("priority")
    if not isinstance(priority, list):
        return {"ok": False, "message": "请提供模型优先级列表"}, 400
    settings = llm_config.save_priority([str(p) for p in priority])
    return {"ok": True, "message": "大模型优先级已保存，即时生效", "priority": settings["priority"]}


@app.route("/api/status")
@login_required
@admin_required
def api_status():
    return status_info()


@app.route("/api/update-status")
@login_required
@admin_required
def api_update_status():
    return get_update_status()


for directory in [BOND_DIR, STRATEGY_DIR, SPREAD_DIR, INDUSTRY_DIR, STD_DEV_JS.parent, INSTITUTION_FLOW_CACHE.parent, PRIMARY_PRICING_CACHE.parent, CONFIG_DIR, UPLOADS_DIR, juyuan_config.PROJECT2_BOND_EXCEL.parent, juyuan_config.STRATEGY_FUND_PRICES_FROZEN.parent, juyuan_config.BOND_STATIC_JSON.parent, juyuan_config.BOND_PICKER_YIELDS_CACHE.parent, MARKET_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

ensure_broker_directories()
interest_spread_db.initialize()
interest_bond_switch_db.initialize()
interest_issuance_db.initialize()
init_ipm_tracker()
load_bond_data()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5011"))
    host = os.environ.get("HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if os.environ.get("BROKER_SCHEDULER_ENABLED", "1") == "1":
        start_broker_scheduler()
    if os.environ.get("BOND_MONITOR_SCHEDULERS_ENABLED", "1") == "1":
        init_spread()
        init_bond_switch()
        init_issuance()
    app.run(host=host, port=port, debug=debug, use_reloader=False)
