"""新老券利差跟踪Blueprint，挂载于 /bond-switch。"""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, render_template, request

from . import config, db, scheduler, services, updater

log = logging.getLogger("bond_switch")
bp = Blueprint("bond_switch", __name__, url_prefix="/bond-switch")


def _db_path():
    return current_app.config.get("BOND_SWITCH_DB")


@bp.get("/")
def index():
    return render_template("interest_bond_switch.html")


@bp.get("/api/dashboard")
def api_dashboard():
    return jsonify(services.dashboard(_db_path(), updater.status()))


def _pairs_from_request() -> list[tuple[str, str]]:
    pairs = []
    for text in request.args.getlist("pair"):
        if ":" not in text:
            continue
        left, right = text.split(":", 1)
        pairs.append((left.strip(), right.strip()))
    return pairs


@bp.get("/api/series")
def api_series():
    return jsonify(services.series(_pairs_from_request(), request.args.get("range", "1y"), _db_path()))


@bp.get("/api/tax-spread")
def api_tax_spread():
    return jsonify(services.tax_spread(request.args.get("range", "1y"), _db_path()))


def init_app() -> None:
    db.initialize()
    scheduler.start()
    status = updater.status()
    if config.AUTO_UPDATE_ENABLED and updater.is_stale() and not status["running"]:
        log.info("新老券缓存为空或已过期，启动增量更新")
        updater.run_async("incremental")
