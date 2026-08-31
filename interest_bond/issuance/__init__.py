"""国债·地方债发行跟踪 - Blueprint,挂载于 /issuance。"""
from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, current_app, jsonify, render_template, request, session

from .. import settings
from . import config, database, services
from .scheduler import start_scheduler

bp = Blueprint("issuance", __name__, url_prefix="/issuance")


def _db_path():
    return current_app.config.get("ISSUANCE_DB") or config.DB_PATH


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return jsonify({"error": "需要管理员验证"}), 403
        return fn(*args, **kwargs)

    return wrapped


@bp.get("/")
def index():
    years = services.available_years(_db_path())
    return render_template(
        "interest_bond_issuance.html",
        years=years,
        current_year=max(years) if years else None,
        categories=config.CATEGORIES,
    )


@bp.get("/api/dashboard")
def api_dashboard():
    years = services.available_years(_db_path())
    default_year = max(years) if years else __import__("datetime").date.today().year
    try:
        year = int(request.args.get("year", default_year))
    except ValueError:
        return jsonify({"error": "年份格式不正确"}), 400
    return jsonify(services.dashboard(year, _db_path()))


@bp.get("/api/long-term")
def api_long_term():
    scope = request.args.get("scope", "total")
    try:
        return jsonify(services.long_term_series(scope, _db_path()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/policy-financial")
def api_policy_financial():
    return jsonify(services.policy_financial_series(_db_path()))


@bp.get("/api/issuance-progress")
def api_issuance_progress():
    try:
        year = int(request.args.get("year", 0))
    except ValueError:
        return jsonify({"error": "年份格式不正确"}), 400
    return jsonify(services.issuance_progress_series(year, _db_path()))


@bp.get("/api/issuance-progress-compare")
def api_issuance_progress_compare():
    categories = [
        value.strip()
        for value in request.args.get("category", "").split(",")
        if value.strip()
    ] or [config.CATEGORIES[0]]
    try:
        years = [
            int(value)
            for value in request.args.get("years", "").split(",")
            if value.strip()
        ]
        return jsonify(
            services.issuance_progress_compare(
                categories, years, _db_path()
            )
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/special-refi-details")
def api_special_refi_details():
    try:
        year = int(request.args.get("year", 0))
    except ValueError:
        return jsonify({"error": "年份格式不正确"}), 400
    locked = year in config.HISTORICAL_SNAPSHOTS
    details = [] if locked else services.special_refi_details(year, _db_path())
    return jsonify(
        {
            "year": year,
            "locked_history": locked,
            "message": "该年为历史固化结果，不展示可能不完整的Oracle动态明细。" if locked else "",
            "details": details,
        }
    )


@bp.get("/api/limits")
def api_limits():
    return jsonify({"categories": config.CATEGORIES, "years": services.list_limits(_db_path())})


@bp.put("/api/limits")
@admin_required
def api_save_limits():
    body = request.get_json(silent=True) or {}
    try:
        services.save_limits(body.get("years") or [], "admin", _db_path())
    except (ValueError, TypeError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.post("/api/admin/login")
def api_admin_login():
    body = request.get_json(silent=True) or {}
    if body.get("password", "") != settings.ADMIN_PASSWORD:
        return jsonify({"error": "管理员密码不正确"}), 403
    session["admin_authenticated"] = True
    return jsonify({"ok": True})


def init_app() -> None:
    """初始化本地库并启动定时调度(测试模式跳过)。"""
    database.initialize()
    if os.environ.get("TRACKER_DISABLE_SCHEDULER") != "1":
        start_scheduler(config.DB_PATH)
