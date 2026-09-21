"""Freeze research inputs without modifying operational caches.

Run from project root: python -m fair_value_research.collect --output outputs/fair_value_v1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    def clean(v):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: clean(x) for k,x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(clean(data), ensure_ascii=False, default=str, allow_nan=False), encoding="utf-8")
    os.replace(temp,path)


def fetch_trade_task(task):
    from broker_market import dm_deals
    path_text,batch,start,end=task
    path=Path(path_text)
    if path.exists() and json.loads(path.read_text(encoding="utf-8"))["status"]=="ok":
        return True
    try:
        rows=dm_deals._post({"security_id_list":batch,"data_source_list":[1],
            "start_date":dm_deals._fmt_day(start),"end_date":dm_deals._fmt_day(end)},dm_deals._DATE_PATH)
        save(path,{"codes":batch,"status":"ok","rows":rows})
        return True
    except Exception as exc:
        save(path,{"codes":batch,"status":"failed","error_type":type(exc).__name__,"rows":[]})
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="outputs/fair_value_v1")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from paths import DATA_DIR
    from juyuan_update import config, db
    from broker_market import dm_deals
    out = Path(args.output) / "inputs"
    out.mkdir(parents=True, exist_ok=True)
    universe = json.loads((DATA_DIR / "bond_static.json").read_text(encoding="utf-8"))
    if (out / "universe_enriched.json").exists():
        universe = json.loads((out / "universe_enriched.json").read_text(encoding="utf-8"))
    save(out / "universe.json", universe)
    bonds = universe["bonds"]
    history = sorted((DATA_DIR / "broker_market/history").glob("????????_??????.json"))
    files = [p for p in history if "20260831" <= p.name[:8] <= "20260916"]
    previous = json.loads((out/"manifest.json").read_text(encoding="utf-8")) if (out/"manifest.json").exists() else {}
    manifest = {"created_at": datetime.now().isoformat(), "universe_count": len(bonds),
                "universe_generated_at": universe.get("generated_at"), "files": previous.get("files", []), "errors": []}
    wanted = {b["code"] for b in bonds}
    days = sorted({p.name[:8] for p in files})
    for day in days:
        dest = out / "quotes" / f"{day}.json"
        if dest.exists():
            continue
        result = []
        for p in files:
            if p.name[:8] != day:
                continue
            raw = p.read_bytes()
            payload = json.loads(raw)
            manifest["files"].append({"name": p.name, "sha256": hashlib.sha256(raw).hexdigest()})
            for q in payload["quotes"]:
                if q["code"] in wanted:
                    result.append({**q, "observed_at": payload["generated_at"]})
        save(dest, result)
    manifest["quote_days"] = days
    print("frozen", len(bonds), "bonds", len(files), "snapshots", len(days), "days", flush=True)
    start = (datetime.strptime(days[0], "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")
    end = days[-1]
    codes = [b["code"] for b in bonds]
    try:
        with db.connect() as conn:
            conn.call_timeout = 180000
            if not (out / "universe_enriched.json").exists():
                from juyuan_update import oracle_bonds as ob
                symbols = [b["code"].split(".")[0] for b in bonds]
                raw_rows = []
                cur = conn.cursor()
                for offset in range(0,len(symbols),400):
                    binds = {f"s{i}":s for i,s in enumerate(symbols[offset:offset+400])}
                    marks = ",".join(":"+k for k in binds)
                    cur.execute(ob._candidate_sql(f"AND n.SYMBOL IN ({marks}) AND n.ISVALID=1",restrict_types=False),binds)
                    raw_rows.extend(cur.fetchall())
                rows = ob._attach_option_dates(conn,raw_rows)
                by_code = {ob._wind_code(r[0],r[1]):r for r in rows}
                for b in bonds:
                    r = by_code.get(b["code"])
                    if r:
                        effective, source = ob.effective_maturity_date(as_of=datetime.strptime(days[0],"%Y%m%d").date(),
                            start_date=r[6],maturity_date=r[7],put_date=r[10],redeem_date=r[11],option_memo=str(r[8] or ""))
                        b.update(secode=str(r[2]),effective_maturity_date=effective.isoformat() if effective else None,
                                 term_source=source,issue_company_code=str(r[19] or ""))
                save(out / "universe_enriched.json",universe)
                save(out / "universe.json",universe)
                print("enriched maturity/security metadata",len(by_code),flush=True)
            curve_meta = {name: {"code": code, "type": "1"} for name, code in config.CURVE_CODE_OVERRIDES.items()}
            if not (out / "curves.json").exists():
                curves = db.fetch_curve_series(conn, curve_meta, start, end, config.SPREAD_MONITOR_TENORS)
                save(out / "curves.json", curves)
            curves = json.loads((out / "curves.json").read_text(encoding="utf-8"))
            calendar = sorted({d for points in curves["国开债"].values() for d in points})
            save(out / "calendar.json", calendar)
            for day in calendar:
                path = out / "valuations" / f"{day}.json"
                if not path.exists():
                    save(path, db.fetch_cnbd_yields_by_symbol(conn, codes, day))
                print("valuations", day, flush=True)
            # Historical rating events: date bounded and never substitute latest rating backwards.
            if not (out / "ratings.json").exists() or not json.loads((out / "ratings.json").read_text(encoding="utf-8")):
                events = {}
                secodes = list({b["secode"] for b in bonds if b.get("secode")})
                for offset in range(0, len(secodes), 400):
                    batch = secodes[offset:offset + 400]
                    binds = {f"s{i}": s for i, s in enumerate(batch)}
                    marks = ",".join(":" + k for k in binds)
                    binds["end_day"] = end
                    cur = conn.cursor()
                    cur.execute(f"""SELECT SECODE, STDCREDIT, HIDECREDITDATE
                        FROM TQ_BD_NEWHIDECREDIT WHERE SECODE IN ({marks})
                        AND CREDITSOURCE='1' AND ISVALID=1 AND STDCREDIT IS NOT NULL
                        AND HIDECREDITDATE <= :end_day""", binds)
                    for sec, rating, day in cur:
                        events.setdefault(str(sec), []).append([db.yyyymmdd(day), str(rating)])
                    cur.close()
                save(out / "ratings.json", events)
    except Exception as exc:
        manifest["errors"].append({"source": "oracle", "type": type(exc).__name__, "message": str(exc)[:300]})
        save(out / "manifest.json", manifest)
        raise
    # Batch endpoint supports security_id_list; keep raw responses for schema audit.
    trade_codes = [b["code"] for b in bonds]
    # Processes also avoid contention in the SDK's Python SM4 decoding.
    failures=0
    with ProcessPoolExecutor(max_workers=max(1,min(16,args.workers))) as pool:
        jobs={pool.submit(fetch_trade_task,(str(out/"trades"/f"chunk5_{i:06}.json"),trade_codes[i:i+5],days[0],end)):i
              for i in range(0,len(trade_codes),5)}
        for n,future in enumerate(as_completed(jobs),1):
            if not future.result():
                failures+=1
            if n % 100 == 0:
                print("trade batches",n,"/",len(jobs),"failed",failures,flush=True)
    if failures:
        print("retry failed trade batches",failures,flush=True)
        for i in range(0,len(trade_codes),5):
            fetch_trade_task((str(out/"trades"/f"chunk5_{i:06}.json"),trade_codes[i:i+5],days[0],end))
    manifest["trade_failed_batches_first_pass"]=failures
    manifest["trade_failed_batches"]=sum(json.loads(p.read_text(encoding="utf-8"))["status"]!="ok"
        for p in (out/"trades").glob("chunk5_*.json"))
    save(out / "manifest.json", manifest)
    print("collection complete", flush=True)


if __name__ == "__main__":
    main()
