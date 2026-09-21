"""Independent output reconciliations and input coverage inventory."""
import hashlib
import json
from pathlib import Path
import argparse

import pandas as pd

from .collect import save


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="outputs/fair_value_v1")
    out=Path(ap.parse_args().output)
    latest=pd.read_csv(out/"latest_all_bonds.csv",dtype={"code":str})
    pred=pd.read_csv(out/"historical_predictions.csv",dtype={"date":str,"code":str})
    bt=pd.read_csv(out/"backtest_detail.csv",dtype={"date":str,"target_date":str,"code":str})
    calendar=json.loads((out/"inputs/calendar.json").read_text(encoding="utf8"))
    assert not latest.code.duplicated().any()
    assert len(latest)==len(json.loads((out/"inputs/universe.json").read_text(encoding="utf8"))["bonds"])
    valid=latest[latest.fair_yield.notna()]
    assert ((valid.quote_days.fillna(0)+valid.trade_days.fillna(0))>0).all()
    assert ((valid.fair_yield-valid.curve_yield)*100-valid.fair_spread_bp).abs().max()<.0002
    assert ((valid.fair_yield-valid.official_yield)*100-valid.delta_bp).abs().max()<.0002
    assert valid.delta_bp.abs().max()<=20.00001
    assert (pred.components.map(lambda s:sum(json.loads(s).values()))-pred.delta_bp).abs().max()<.00001
    assert not pred.duplicated(["date","code","mode"]).any()
    assert not bt.duplicated(["date","code","model","horizon"]).any()
    for r in bt[["date","target_date","horizon"]].drop_duplicates().itertuples():
        assert calendar.index(r.target_date)-calendar.index(r.date)==r.horizon
    assert set(bt.horizon)<=set([3,5,10])
    assert (bt.error_bp-(bt.future_spread_bp-bt.fair_spread_bp).abs()).abs().max()<.00001
    failures=[]
    success_codes=set()
    trade_rows=0
    trade_positive_rows=0
    input_hashes={}
    for p in sorted((out/"inputs/trades").glob("chunk5_*.json")):
        raw=p.read_bytes()
        batch=json.loads(raw)
        input_hashes[str(p.relative_to(out))]=hashlib.sha256(raw).hexdigest()
        if batch["status"]!="ok":
            failures.append(p.name)
        else:
            success_codes.update(batch["codes"])
            trade_rows+=len(batch["rows"])
            trade_positive_rows+=sum((r.get("trading_num") or 0)>0 for r in batch["rows"])
    for p in sorted((out/"inputs").rglob("*.json")):
        if p.parent.name=="trades" or p.name.startswith("quote_daily"):
            continue
        input_hashes[str(p.relative_to(out))]=hashlib.sha256(p.read_bytes()).hexdigest()
    save(out/"input_hashes.json",input_hashes)
    five=bt[(bt.horizon==5)&(bt.model=="joint")].copy()
    five["converged"]=five.convergence_bp>0
    five.sort_values(["date","convergence_bp"],ascending=[False,False]).to_csv(out/"five_day_cases.csv",index=False,encoding="utf-8-sig")
    result={"checks":"passed","universe":len(latest),"valid_fair_values":len(valid),
            "prediction_rows":len(pred),"backtest_rows":len(bt),"trade_requested_codes":len(success_codes),
            "trade_response_rows":trade_rows,"trade_positive_rows":trade_positive_rows,
            "failed_trade_batches":failures,"input_files_hashed":len(input_hashes),
            "source_hashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}}
    save(out/"verification.json",result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
