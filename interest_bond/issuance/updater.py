from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config
from . import database
from .classification import classify_local_bond, classify_treasury, maturity_years
from .oracle_data import fetch_update_payload


class UpdateBusyError(RuntimeError):
    pass


@contextmanager
def update_lock(path: Path | None = None):
    lock_path = Path(path or config.UPDATE_LOCK_PATH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        age = datetime.now().timestamp() - lock_path.stat().st_mtime
        if age > 6 * 3600:
            lock_path.unlink(missing_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise UpdateBusyError("已有更新任务在运行") from exc
    try:
        os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _last_success_date(db_path=None) -> date | None:
    with database.connect(db_path) as conn:
        value = database.get_meta(conn, "last_success_date")
    return date.fromisoformat(value) if value else None


def _create_run(mode: str, db_path=None) -> int:
    with database.transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO update_runs(mode,status,started_at,message) VALUES(?,?,?,?)",
            (mode, "running", datetime.now().isoformat(timespec="seconds"), "正在连接Oracle并拉取增量数据"),
        )
        return int(cur.lastrowid)


def _finish_run(run_id: int, status: str, message: str, rows_changed: int = 0, as_of=None, db_path=None) -> None:
    with database.transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE update_runs
            SET status=?, finished_at=?, as_of_date=?, message=?, rows_changed=?
            WHERE id=?
            """,
            (
                status,
                datetime.now().isoformat(timespec="seconds"),
                as_of.isoformat() if as_of else None,
                message[:2000],
                rows_changed,
                run_id,
            ),
        )


def _upsert_payload(conn, payload: dict) -> int:
    current_year = payload["current_year"]
    if payload.get("include_progress_history"):
        conn.execute("DELETE FROM bond_cache WHERE year>=?", (config.HISTORY_START_YEAR,))
        conn.execute(
            "DELETE FROM policy_financial_bond_cache WHERE year>=?",
            (config.HISTORY_START_YEAR,),
        )
        conn.execute("DELETE FROM treasury_maturities WHERE year>=?", (config.HISTORY_START_YEAR,))
    elif payload["full_history"]:
        conn.execute("DELETE FROM bond_cache WHERE year=? OR term_years>=20", (current_year,))
        conn.execute(
            "DELETE FROM policy_financial_bond_cache WHERE year>=?",
            (config.HISTORY_START_YEAR,),
        )
    else:
        start_iso = payload["query_start"].isoformat()
        conn.execute("DELETE FROM bond_cache WHERE issue_date>=?", (start_iso,))
        conn.execute(
            "DELETE FROM policy_financial_bond_cache WHERE issue_date>=?",
            (start_iso,),
        )
    if not payload.get("include_progress_history"):
        conn.execute("DELETE FROM treasury_maturities WHERE year=?", (current_year,))

    rows_changed = 0
    for item in payload["treasury_issues"]:
        category = classify_treasury(item["name"])
        conn.execute(
            """
            INSERT INTO bond_cache(
              unique_key,scope,issue_date,year,name,amount,term_years,purpose,codes,
              classification,include_special_refi,reason,source_updated_at,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(unique_key) DO UPDATE SET
              issue_date=excluded.issue_date, year=excluded.year, name=excluded.name,
              amount=excluded.amount, term_years=excluded.term_years,
              classification=excluded.classification, reason=excluded.reason,
              raw_json=excluded.raw_json
            """,
            (
                f"T:{item['issue_id']}",
                "treasury",
                item["issue_date"].isoformat(),
                item["issue_date"].year,
                item["name"],
                item["amount"],
                item["term_years"],
                "",
                "",
                category,
                0,
                "全称含特别国债" if category == "特别国债" else "普通国债发行事件",
                None,
                database.dump_json(item),
            ),
        )
        rows_changed += 1

    for item in payload.get("policy_financial_issues", []):
        conn.execute(
            """
            INSERT INTO policy_financial_bond_cache(
              unique_key,issue_date,year,issuer,name,amount,source_updated_at,raw_json
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(unique_key) DO UPDATE SET
              issue_date=excluded.issue_date, year=excluded.year,
              issuer=excluded.issuer, name=excluded.name,
              amount=excluded.amount, source_updated_at=excluded.source_updated_at,
              raw_json=excluded.raw_json
            """,
            (
                f"P:{item['issue_id']}",
                item["issue_date"].isoformat(),
                item["issue_date"].year,
                item["issuer"],
                item["name"],
                item["amount"],
                None,
                database.dump_json(item),
            ),
        )
        rows_changed += 1

    for item in payload["local_issues"]:
        result = classify_local_bond(item["name"], item["purpose"])
        term = maturity_years(
            item["term_value"], item["term_unit"], item["issue_date"], item["maturity_date"]
        )
        unique = f"L:{item['issue_date'].year}:{_sha1(item['name'])}"
        conn.execute(
            """
            INSERT INTO bond_cache(
              unique_key,scope,issue_date,year,name,amount,term_years,purpose,codes,
              classification,include_special_refi,reason,source_updated_at,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(unique_key) DO UPDATE SET
              issue_date=excluded.issue_date, year=excluded.year, name=excluded.name,
              amount=excluded.amount, term_years=excluded.term_years,
              purpose=excluded.purpose, codes=excluded.codes,
              classification=excluded.classification,
              include_special_refi=excluded.include_special_refi,
              reason=excluded.reason, source_updated_at=excluded.source_updated_at,
              raw_json=excluded.raw_json
            """,
            (
                unique,
                "local",
                item["issue_date"].isoformat(),
                item["issue_date"].year,
                item["name"],
                item["amount"],
                term,
                item["purpose"],
                ", ".join(item["codes"]),
                result.category,
                1 if result.include_special_refi else 0,
                result.reason,
                item["update_time"].isoformat() if item["update_time"] else None,
                database.dump_json(item),
            ),
        )
        rows_changed += 1

    for item in payload["treasury_maturities"]:
        unique = f"M:{item['maturity_date'].isoformat()}:{_sha1(item['name'])}"
        conn.execute(
            """
            INSERT INTO treasury_maturities(unique_key,maturity_date,year,name,amount)
            VALUES(?,?,?,?,?)
            ON CONFLICT(unique_key) DO UPDATE SET amount=excluded.amount, name=excluded.name
            """,
            (unique, item["maturity_date"].isoformat(), item["maturity_date"].year, item["name"], item["amount"]),
        )
        rows_changed += 1

    return rows_changed


def recompute_year(conn, year: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    gross_ordinary = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM bond_cache WHERE year=? AND scope='treasury' AND classification='一般国债'",
        (year,),
    ).fetchone()[0]
    maturities = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM treasury_maturities WHERE year=?", (year,)
    ).fetchone()[0]
    amounts = {
        "一般国债": float(gross_ordinary) - float(maturities),
        "特别国债": conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bond_cache WHERE year=? AND scope='treasury' AND classification='特别国债'",
            (year,),
        ).fetchone()[0],
        "地方新增一般债": conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bond_cache WHERE year=? AND classification='地方新增一般债'",
            (year,),
        ).fetchone()[0],
        "地方新增专项债": conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bond_cache WHERE year=? AND classification='地方新增专项债'",
            (year,),
        ).fetchone()[0],
        "地方特殊再融资债": conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bond_cache WHERE year=? AND include_special_refi=1",
            (year,),
        ).fetchone()[0],
    }
    for category in config.CATEGORIES:
        conn.execute(
            """
            INSERT INTO annual_snapshots(year,category,amount,source,locked,updated_at)
            VALUES(?,?,?,?,0,?)
            ON CONFLICT(year,category) DO UPDATE SET
              amount=CASE WHEN annual_snapshots.locked=1 THEN annual_snapshots.amount ELSE excluded.amount END,
              source=CASE WHEN annual_snapshots.locked=1 THEN annual_snapshots.source ELSE excluded.source END,
              updated_at=CASE WHEN annual_snapshots.locked=1 THEN annual_snapshots.updated_at ELSE excluded.updated_at END
            """,
            (year, category, round(float(amounts[category]), 4), "Oracle动态汇总", now),
        )


def run_update(mode: str = "incremental", db_path=None, today: date | None = None) -> dict:
    if mode not in {"incremental", "full"}:
        raise ValueError("mode must be incremental or full")
    database.initialize(db_path)
    with update_lock(config.UPDATE_LOCK_PATH if db_path is None else Path(str(db_path) + ".lock")):
        run_id = _create_run(mode, db_path)
        try:
            with database.connect(db_path) as conn:
                include_progress_history = database.get_meta(
                    conn, "issuance_progress_history_loaded", "0"
                ) != "1"
            payload = fetch_update_payload(
                mode=mode,
                last_success_date=_last_success_date(db_path),
                include_progress_history=include_progress_history,
                today=today,
            )
            with database.transaction(db_path) as conn:
                rows = _upsert_payload(conn, payload)
                recompute_year(conn, payload["current_year"])
                now = datetime.now().isoformat(timespec="seconds")
                database.set_meta(conn, "last_success_date", payload["as_of_date"].isoformat())
                database.set_meta(conn, "last_success_at", now)
                database.set_meta(conn, "last_update_mode", mode)
                database.set_meta(conn, "last_update_message", f"成功更新 {rows} 条缓存记录")
                if payload.get("include_progress_history"):
                    database.set_meta(conn, "issuance_progress_history_loaded", "1")
            _finish_run(run_id, "success", f"成功更新 {rows} 条缓存记录", rows, payload["as_of_date"], db_path)
            return {"run_id": run_id, "status": "success", "rows_changed": rows}
        except Exception as exc:
            _finish_run(run_id, "failed", str(exc), db_path=db_path)
            with database.transaction(db_path) as conn:
                database.set_meta(conn, "last_update_message", f"更新失败：{exc}")
            raise


def start_background_update(mode: str = "incremental", db_path=None) -> int:
    database.initialize(db_path)
    lock_path = config.UPDATE_LOCK_PATH if db_path is None else Path(str(db_path) + ".lock")
    if lock_path.exists():
        raise UpdateBusyError("已有更新任务在运行")

    marker = {"run_id": 0}

    def worker():
        try:
            result = run_update(mode=mode, db_path=db_path)
            marker["run_id"] = result["run_id"]
        except Exception:
            pass

    thread = threading.Thread(target=worker, name="bond-tracker-update", daemon=True)
    thread.start()
    return marker["run_id"]
