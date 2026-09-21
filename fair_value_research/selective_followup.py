"""Explicit post-hoc follow-up: persistent unilateral evidence and ex-ante drift controls."""
import argparse
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .collect import save
from .run import Inputs
from .selective import metrics, qualify


def extended_features(source,parent,out):
    dest=out/"features.json"
    if dest.exists():return pd.read_json(dest,convert_dates=False,dtype={"date":str,"code":str})
    f=pd.read_json(parent/"features.json",convert_dates=False,dtype={"date":str,"code":str})
    data=Inputs(source/"inputs");bonds={b["code"]:b for b in data.bonds}
    rows=[]
    for r in f.to_dict("records"):
        b=bonds[r["code"]];day=r["date"];fs=data.features(b,day,5);qs=fs["quotes"]
        side=np.sign(r["q_delta"])
        def bound(q):
            base=r["base_spread"];bid=q.get("bid");ofr=q.get("ofr")
            if bid is not None and base>bid:return bid-base
            if ofr is not None and base<ofr:return ofr-base
            return 0.
        last=min(qs,key=lambda q:q["age"]) if qs else None
        r["any_quote_age"]=last["age"] if last else np.nan
        r["any_freshness"]=last["freshness"] if last else np.nan
        r["any_boundary"]=bound(last) if last else np.nan
        r["boundary_consistent_days"]=sum(np.sign(bound(q))==side and abs(bound(q))>=.5 for q in qs) if side else 0
        r["known_relevant_volume_days"]=sum(q.get("bid_volume" if side<0 else "ofr_volume") is not None and q.get("bid_volume" if side<0 else "ofr_volume")>0 for q in qs)
        idx=data.calendar.index(day)
        # Each origin only uses values strictly before it, with fixed origin curve family.
        now=data.actual(b,data.calendar[idx-1],r["curve_name"]) if idx>=1 else None
        old=data.actual(b,data.calendar[idx-6],r["curve_name"]) if idx>=6 else None
        r["past5_official_delta"]=now[0]-old[0] if now and old else np.nan
        rows.append(r)
    result=pd.DataFrame(rows)
    # Cross-sectional aggregate observable at origin; no future outcomes used.
    grouped=result.drop_duplicates(["date","family"])
    market=grouped.groupby("date").past5_official_delta.median()
    result["past5_market_delta"]=result.date.map(market)
    result.to_json(dest,orient="records",force_ascii=False,double_precision=12)
    return result


def apply(f,c):
    persistent=(f.quote_days>=4)&(f.any_quote_age==0)&(f.any_freshness>=.8)&(f.boundary_consistent_days>=3)&(f.any_boundary*f.q_delta>0)
    if c["gate"]=="known_size":persistent &= f.known_relevant_volume_days>=3
    elif c["gate"]=="trade_echo":persistent &= (f.trade_days>=1)&(f.last_trade_age<=2)&(f.q_delta*f.t_delta>0)
    elif c["gate"]=="large_evidence":persistent &= f.q_delta.abs()>=4
    value={"level":f.level_delta,"quote":f.q_delta,"boundary":f.any_boundary}[c["anchor"]]
    d=(value*c["scale"]).clip(-10,10)
    return d,persistent&d.notna()&(d.abs()>=c.get("minimum",1.))


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--source",default="outputs/fair_value_v1");ap.add_argument("--parent",default="outputs/fair_value_v2")
    args=ap.parse_args();source=Path(args.source);parent=Path(args.parent);out=parent/"persistent_followup";out.mkdir(parents=True,exist_ok=True)
    configs=[dict(id=f"{a}_{g}_{s}",anchor=a,gate=g,scale=s,minimum=1.) for a in ("level","quote","boundary") for g in ("persistent","known_size","trade_echo","large_evidence") for s in (.25,.5)]
    registry=out/"experiment_registry.json"
    if not registry.exists():save(registry,{"at":datetime.now().isoformat(),"post_hoc":True,"configs":configs,"reason":"Strict two-sided/trade gates too sparse; examine persistent one-sided boundary evidence without requiring trade."})
    f=extended_features(source,parent,out)
    labels=pd.read_csv(source/"backtest_detail.csv",dtype={"date":str,"code":str,"target_date":str})
    labels=labels[(labels.model=="joint")&labels.horizon.isin([3,5])][["date","code","horizon","target_date","actual_delta_bp"]]
    full=f.merge(labels,on=["date","code"],how="inner",validate="one_to_many")
    five=full[full.horizon==5].reset_index(drop=True);dev=five[five.split=="development"].reset_index(drop=True);chk=five[five.split=="issuer_check"].reset_index(drop=True)
    ds=[]
    for c in configs:
        d,m=apply(dev,c);r={**c,**metrics(dev,d,m)};r["qualifies"]=qualify(r);ds.append(r)
    ds=pd.DataFrame(ds);ds.to_csv(out/"development_candidates.csv",index=False,encoding="utf-8-sig")
    eligible=ds[ds.qualifies].sort_values(["date_equal_improvement_bp","n"],ascending=False)
    chosen={k:eligible.iloc[0][k] for k in ("id","anchor","gate","scale","minimum")} if len(eligible) else configs[0]
    d,m=apply(chk,chosen);cm=metrics(chk,d,m)
    records=[];chosen_back=[]
    for c in configs:
        d,m=apply(five,c)
        records.append({**c,**metrics(five,d,m)})
    pd.DataFrame(records).to_csv(out/"all_candidates.csv",index=False,encoding="utf-8-sig")
    comparisons=[]
    for h in (3,5):
        z=full[full.horizon==h].reset_index(drop=True);d,m=apply(z,chosen)
        for split in ("all","development","issuer_check"):
            a=z if split=="all" else z[z.split==split]
            dd,mm=apply(a,chosen);comparisons.append({"horizon":h,"split":split,**metrics(a,dd,mm)})
        x=z.loc[m].copy();x["prediction"]=d[m]
        x["v2_error"]=(x.actual_delta_bp-x.prediction).abs();x["v1_error"]=(x.actual_delta_bp-x.delta_bp).abs();x["zero_error"]=x.actual_delta_bp.abs()
        x["market_momentum_error"]=(x.actual_delta_bp-x.past5_market_delta*h/5).abs()
        x["own_momentum_error"]=(x.actual_delta_bp-x.past5_official_delta*h/5).abs()
        x["half_market_momentum_error"]=(x.actual_delta_bp-x.past5_market_delta*h/10).abs()
        chosen_back.append(x)
    back=pd.concat(chosen_back,ignore_index=True)
    back.to_csv(out/"selected_backtest.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(comparisons).to_csv(out/"chosen_metrics.csv",index=False,encoding="utf-8-sig")
    errors=["v2_error","v1_error","zero_error","market_momentum_error","half_market_momentum_error","own_momentum_error"]
    matched=back.dropna(subset=errors).groupby("horizon")[errors].mean().reset_index()
    matched.to_csv(out/"matched_baselines.csv",index=False,encoding="utf-8-sig")
    daily=back.groupby(["horizon","date"]).agg(n=("code","size"),v2_mae=("v2_error","mean"),zero_mae=("zero_error","mean"),market_mae=("market_momentum_error","mean")).reset_index()
    daily.to_csv(out/"daily_stability.csv",index=False,encoding="utf-8-sig")
    # Veto if issuer stability fails or a simple ex-ante drift baseline wins.
    check=back[(back.horizon==5)&(back.split=="issuer_check")].dropna(subset=errors)
    release=bool(len(eligible) and cm.get("n",0)>=50 and cm.get("issuers",0)>=15 and cm.get("positive_dates",0)>=3
        and cm.get("improvement_bp",-1)>.05 and len(check)>=50
        and check.v2_error.mean()<check.market_momentum_error.mean() and check.v2_error.mean()<check.half_market_momentum_error.mean())
    latest=f[f.date==f.date.max()].copy();delta,mask=apply(latest,chosen)
    latest["watch_delta_bp"]=delta.where(mask);latest["data_signal_pass"]=mask
    latest["fair_yield_v2"]=(latest.official_yield+delta/100).where(mask&release)
    latest["fair_spread_v2"]=(latest.base_spread+delta).where(mask&release)
    latest["status"]=np.where(mask,"探索信号" if release else "观察名单：尚未通过全部基准","不预测")
    latest["reason"]=np.where(mask,"" if release else "整体模型尚未通过跨主体及市场动量基准",np.where(latest.quote_days<4,"有效报价不足4日",np.where(latest.any_quote_age!=0,"当日无有效报价",np.where(latest.any_freshness<.8,"报价新鲜度不足",np.where(latest.boundary_consistent_days<3,"偏离买卖区间的方向不持续",np.where(latest.any_boundary*latest.q_delta<=0,"最新报价约束不再确认偏离方向","调整小于1BP或附加证据门槛未满足"))))))
    universe=pd.DataFrame(json.loads((source/"inputs/universe.json").read_text(encoding="utf8"))["bonds"])[["code","name","issuer"]]
    keep=["code","date","official_yield","fair_yield_v2","fair_spread_v2","watch_delta_bp","status","reason","data_signal_pass","quote_days","trade_days","two_sided_days","any_boundary","boundary_consistent_days","known_relevant_volume_days","past5_official_delta","past5_market_delta"]
    latest=universe.merge(latest[keep],on="code",how="left");latest.status=latest.status.fillna("不预测");latest.reason=latest.reason.fillna("无可用本券证据或曲线基准")
    latest.to_csv(out/"latest_all_bonds.csv",index=False,encoding="utf-8-sig");latest.to_json(out/"latest_all_bonds.json",orient="records",force_ascii=False)
    latest[latest.data_signal_pass==True].to_csv(out/"latest_watchlist.csv",index=False,encoding="utf-8-sig")
    assert latest.loc[latest.status=="不预测",["fair_yield_v2","fair_spread_v2"]].isna().all().all()
    summary={"chosen":chosen,"qualifying_development":len(eligible),"issuer_check":cm,"release_experimental":release,
        "watchlist":int((latest.data_signal_pass==True).sum()),"published":int(latest.fair_yield_v2.notna().sum()),
        "post_hoc":True,"configs":len(configs),"pristine_oos":False}
    save(out/"summary.json",summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True);print(matched.to_string(index=False),flush=True)


if __name__=="__main__":main()
