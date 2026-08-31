"""新老券模块更新任务。"""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import date, datetime, timedelta

from . import config, db, oracle_source, services

log = logging.getLogger("bond_switch")

_lock = threading.Lock()
_state = {"running": False, "mode": "", "stage": "", "started_at": None, "finished_at": None, "message": "", "rows": 0}


def status() -> dict:
    with _lock:
        return dict(_state)


def _progress(text: str) -> None:
    _state["stage"] = text


def _run(mode: str, db_path=None) -> dict:
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "已有更新任务在进行中"}
    run_id = None
    try:
        _state.update(running=True, mode=mode, stage="开始", started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None, message="", rows=0)
        db.initialize(db_path)
        run_id = db.start_update_run(mode, db_path)
        valuation_date, quote_date = oracle_source.latest_available_dates()
        if not valuation_date or not quote_date:
            raise RuntimeError("Oracle中未找到近期中债估值或银行间成交数据")
        candidates = oracle_source.fetch_candidates(valuation_date, quote_date, _progress)
        if not candidates:
            raise RuntimeError("未筛选到剩余期限26Y-30Y的存量国债")
        roles = services.select_roles(candidates)
        selected_codes = sorted({code for codes in roles.values() for code in codes})
        if config.TAX_EXEMPT_CODE not in selected_codes:
            selected_codes.append(config.TAX_EXEMPT_CODE)
        if mode == "full":
            start_ymd = config.HISTORY_START
        else:
            starts = []
            for code in selected_codes:
                latest = db.max_valuation_date(code, db_path)
                if not latest:
                    starts.append(config.HISTORY_START)
                else:
                    d = date(int(latest[:4]), int(latest[4:6]), int(latest[6:8])) - timedelta(days=config.INCREMENTAL_OVERLAP_DAYS)
                    starts.append(max(config.HISTORY_START, d.strftime("%Y%m%d")))
            start_ymd = min(starts) if starts else config.HISTORY_START
        valuation_rows = oracle_source.fetch_valuation_history(selected_codes, start_ymd, valuation_date, _progress)
        # 历史候选全集：窗口期内曾在26-30Y带内的所有券都补齐估值与逐日成交，
        # 否则历史每日候选池过浅，次活跃券/次次活跃券会大面积判不出来。
        try:
            universe_codes = set(oracle_source.fetch_candidate_universe(start_ymd, valuation_date, _progress))
        except Exception as exc:
            log.warning("获取历史候选全集失败，仅使用角色券回补：%s", exc)
            universe_codes = set()
        universe_codes |= set(db.distinct_valuation_codes(db_path)) | set(selected_codes)
        codes_to_pull = sorted(universe_codes)
        if len(codes_to_pull) > len(selected_codes):
            valuation_rows.extend(oracle_source.fetch_valuation_history(
                [c for c in codes_to_pull if c not in selected_codes], start_ymd, valuation_date, _progress
            ))
        rows = db.upsert_valuations(valuation_rows, db_path)
        # 逐日成交历史：用于每日按成交量判定活跃券/次活跃券/次次活跃券
        tracked_codes = sorted(set(codes_to_pull) | {b["code"] for b in candidates})
        quote_rows = oracle_source.fetch_quote_history(tracked_codes, start_ymd, quote_date, _progress)
        db.upsert_daily_quotes(quote_rows, db_path)
        db.replace_snapshot(valuation_date, candidates, roles, db_path)
        db.set_meta("quote_date", quote_date, db_path)
        db.finish_update_run(run_id, "success", "ok", valuation_date, rows, db_path)
        _state.update(running=False, stage="完成", message=f"更新{len(candidates)}只候选券，写入{rows}条估值、{len(quote_rows)}条逐日成交", rows=rows, finished_at=datetime.now().isoformat(timespec="seconds"))
        return {"ok": True, "as_of": valuation_date, "quote_date": quote_date, "rows": rows, "quote_rows": len(quote_rows), "candidates": len(candidates), "roles": roles}
    except Exception as exc:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        if run_id is not None:
            db.finish_update_run(run_id, "failed", detail[:800], None, _state["rows"], db_path)
        _state.update(running=False, stage="失败", message=detail[:800], finished_at=datetime.now().isoformat(timespec="seconds"))
        return {"ok": False, "error": detail}
    finally:
        _lock.release()


def run_update(mode: str = "incremental", db_path=None) -> dict:
    """同步执行一次更新，供门户后台统一任务调用。"""
    return _run(mode, db_path)


def run_async(mode: str = "incremental") -> dict:
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "已有更新任务在进行中"}

    def worker():
        _lock.release()
        _run(mode)

    threading.Thread(target=worker, daemon=True, name="bond-switch-update").start()
    return {"ok": True, "started": True, "mode": mode}


def is_stale(days: int = 2) -> bool:
    latest = db.latest_snapshot_date()
    if not latest:
        return True
    last = date(int(latest[:4]), int(latest[4:6]), int(latest[6:8]))
    return (date.today() - last).days >= days
