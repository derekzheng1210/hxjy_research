"""V2: indicator pruning, evidence-based abstention and matched-sample research.

Retrospective research only. Issuer split is a stability check, NOT unseen temporal OOS.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .collect import save
from .run import Inputs
from .model import Parameters, weighted_median


ANCHORS=("v1", "levels_only", "quote_center", "trade_center", "consensus", "outside_interval")
GATES=("all", "liquid_quote", "trade_confirmed", "interval_confirmed", "signal_to_noise")
SCALES=(.25,.5,.75)
MIN_SIGNALS=(.5,1.0)
MIN_DEV_ROWS=100
MIN_DEV_ISSUERS=30


def split_issuer(issuer):
    return "development" if int(hashlib.sha256(str(issuer).encode()).hexdigest()[:8],16)%5<3 else "issuer_check"


def snapshot_features(f,base):
    qs=f["quotes"];ts=f["trades"]
    both=[q for q in qs if q.get("bid") is not None and q.get("ofr") is not None and q["bid"]>=q["ofr"]]
    mids=[(q["bid"]+q["ofr"])/2 for q in both]
    widths=[q["bid"]-q["ofr"] for q in both]
    qlast=min(both,key=lambda x:x["age"]) if both else None
    tlast=min(ts,key=lambda x:x["age"]) if ts else None
    def outside(q):
        return q["ofr"]-base if base<q["ofr"] else q["bid"]-base if base>q["bid"] else 0.0
    outside_delta=outside(qlast) if qlast else np.nan
    sign=np.sign(outside_delta)
    persistent=sum(np.sign(outside(q))==sign and abs(outside(q))>=.5 for q in both) if sign else 0
    mad=lambda values:float(np.median(np.abs(np.array(values)-np.median(values)))) if values else np.nan
    return {"two_sided_days":len(both),"median_width_bp":float(np.median(widths)) if widths else np.nan,
        "last_width_bp":qlast["bid"]-qlast["ofr"] if qlast else np.nan,
        "last_quote_age":qlast["age"] if qlast else np.nan,
        "mean_freshness":float(np.mean([q["freshness"] for q in both])) if both else np.nan,
        "mid_mad_bp":mad(mids),"trade_mad_bp":mad([t["spread"] for t in ts]),
        "trade_count":sum(t["num"] for t in ts),"last_trade_age":tlast["age"] if tlast else np.nan,
        "outside_delta":outside_delta,"outside_days":persistent,
        "last_bid_spread":qlast["bid"] if qlast else np.nan,"last_ofr_spread":qlast["ofr"] if qlast else np.nan}


def enrich(source,out):
    cache=out/"features.json"
    if cache.exists():
        return pd.read_json(cache,dtype={"date":str,"code":str},convert_dates=False)
    data=Inputs(source/"inputs")
    pred=pd.read_csv(source/"historical_predictions.csv",dtype={"date":str,"code":str})
    pred=pred[pred["mode"]=="joint"].copy()
    bonds={b["code"]:b for b in data.bonds}
    rows=[]
    for r in pred.to_dict("records"):
        f=data.features(bonds[r["code"]],r["date"],5)
        parts=json.loads(r["components"])
        r.update(snapshot_features(f,r["base_spread"]))
        r["level_delta"]=parts.get("quote_level",0)+parts.get("trade_level",0)
        r["q_delta"]=r["quote_center"]-r["base_spread"]
        r["t_delta"]=r["trade_center"]-r["base_spread"]
        r["split"]=split_issuer(r.get("issuer") or r["code"])
        b=bonds[r["code"]]
        r["family"]=str((b.get("issuer"),b.get("issue_date"),b.get("effective_maturity_date"),b.get("sub"),b.get("guarantor")))
        rows.append(r)
    frame=pd.DataFrame(rows)
    frame.to_json(cache,orient="records",force_ascii=False,double_precision=12)
    return frame


def masks_and_anchors(f):
    q=f.q_delta;t=f.t_delta
    same=(q*t>0)
    # Trade sign is price evidence, not an unverified GVN/TKN interpretation.
    strong_trade=(f.trade_days>=2)&(f.trade_count>=3)&(f.last_trade_age<=1)&(f.trade_mad_bp<=3)
    liquid=(f.two_sided_days>=3)&(f.last_quote_age==0)&(f.median_width_bp<=5)&(f.last_width_bp<=5)&(f.mean_freshness>=.5)
    agreement=same&((q-t).abs()<=3)
    outside=(f.outside_days>=3)&(f.outside_delta.abs()>=1)&(f.outside_delta*t>0)
    noise=pd.concat([f.median_width_bp/2,f.mid_mad_bp,f.trade_mad_bp,pd.Series(.5,index=f.index)],axis=1).max(axis=1)
    gates={"all":pd.Series(True,index=f.index),"liquid_quote":liquid,
        "trade_confirmed":liquid&strong_trade&agreement,
        "interval_confirmed":liquid&strong_trade&outside,
        "signal_to_noise":liquid&strong_trade&agreement&(pd.concat([q.abs(),t.abs()],axis=1).min(axis=1)>=1.5*noise)}
    anchors={"v1":f.delta_bp,"levels_only":f.level_delta,"quote_center":q,"trade_center":t,
        "consensus":((q+t)/2).where(same),"outside_interval":f.outside_delta.where(f.outside_days>=3)}
    return gates,anchors


def configs():
    yield {"id":"v1_full","anchor":"v1","gate":"all","scale":1.,"minimum":0.}
    yield {"id":"levels_full","anchor":"levels_only","gate":"all","scale":1.,"minimum":0.}
    for a in ANCHORS:
        for g in GATES:
            for s in SCALES:
                for m in MIN_SIGNALS:
                    yield {"id":f"{a}__{g}__{s}__{m}","anchor":a,"gate":g,"scale":s,"minimum":m}


def apply_config(frame,cfg):
    gates,anchors=masks_and_anchors(frame)
    delta=(anchors[cfg["anchor"]]*cfg["scale"]).clip(-10,10)
    if cfg["id"]=="v1_full":
        delta=frame.delta_bp
    selected=gates[cfg["gate"]]&delta.notna()&(delta.abs()>=cfg["minimum"])
    # A zero adjustment is not an actionable signal, except explicit baseline controls.
    return delta,selected


def metrics(frame,delta,mask):
    x=frame.loc[mask].copy()
    if x.empty:
        return {"n":0,"issuers":0,"origins":0,"coverage":0.}
    x["prediction"]=delta.loc[mask]
    x["err"]=(x.actual_delta_bp-x.prediction).abs()
    x["base_err"]=x.actual_delta_bp.abs()
    x["gain"]=x.base_err-x.err
    x["correct"]=(x.prediction*x.actual_delta_bp>0).astype(float)
    d=x.groupby("date").agg(gain=("gain","mean"),n=("code","size"))
    # Listing aliases cannot make an issuer dominate this diagnostic.
    family=x.groupby(["date","family"]).agg(gain=("gain","mean"))
    issuer=x.groupby(["date","issuer"]).agg(gain=("gain","mean"))
    raw_sign=(x.prediction*x.actual_delta_bp>0).mean()
    always_down=(x.actual_delta_bp<0).mean()
    return {"n":len(x),"issuers":x.issuer.nunique(),"families":x.family.nunique(),"origins":len(d),
        "coverage":len(x)/len(frame),"mae_bp":x.err.mean(),"baseline_mae_bp":x.base_err.mean(),
        "improvement_bp":x.gain.mean(),"improvement_pct":x.gain.mean()/x.base_err.mean() if x.base_err.mean() else 0,
        "date_equal_improvement_bp":d.gain.mean(),"worst_date_improvement_bp":d.gain.min(),
        "positive_dates":int((d.gain>0).sum()),"min_date_n":int(d.n.min()),
        "family_equal_improvement_bp":family.gain.mean(),"issuer_equal_improvement_bp":issuer.gain.mean(),
        "direction_accuracy":raw_sign,"always_down_accuracy":always_down,
        "mean_abs_signal_bp":x.prediction.abs().mean(),"p90_error_bp":x.err.quantile(.9)}


def qualify(r):
    return (r.get("n",0)>=MIN_DEV_ROWS and r.get("issuers",0)>=MIN_DEV_ISSUERS
        and r.get("origins",0)==4 and r.get("min_date_n",0)>=10 and r.get("minimum",0)>=1
        and r.get("gate")!="all" and r.get("improvement_bp",-1)>.05
        and r.get("date_equal_improvement_bp",-1)>.05 and r.get("positive_dates",0)>=3
        and r.get("family_equal_improvement_bp",-1)>0 and r.get("issuer_equal_improvement_bp",-1)>0)


def refusal(row,cfg):
    if pd.isna(row.get("base_spread")):
        return "缺少可用官方估值、评级曲线或本券市场证据"
    if cfg["gate"] in {"liquid_quote","trade_confirmed","interval_confirmed","signal_to_noise"}:
        if row.two_sided_days<3 or row.last_quote_age!=0:
            return "双边报价不足3日或当日没有有效双边报价"
        if row.median_width_bp>5 or row.last_width_bp>5 or row.mean_freshness<.5:
            return "价差过宽或报价新鲜度不足"
    if cfg["gate"] in {"trade_confirmed","interval_confirmed","signal_to_noise"}:
        if row.trade_days<2 or row.trade_count<3 or row.last_trade_age>1 or row.trade_mad_bp>3:
            return "近期成交数量或稳定性不足"
    if cfg["gate"] in {"trade_confirmed","signal_to_noise"} and not (row.q_delta*row.t_delta>0 and abs(row.q_delta-row.t_delta)<=3):
        return "报价与成交方向冲突或幅度差异过大"
    if cfg["gate"]=="interval_confirmed" and not (row.outside_days>=3 and abs(row.outside_delta)>=1 and row.outside_delta*row.t_delta>0):
        return "估值没有持续偏离双边区间或缺少成交确认"
    return "调整不足门槛或信号未明显超过报价噪声"


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",default="outputs/fair_value_v1")
    ap.add_argument("--output",default="outputs/fair_value_v2")
    args=ap.parse_args();source=Path(args.source);out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    registered=out/"experiment_registry.json"
    spec={"registered_at":datetime.now().isoformat(),"configs":list(configs()),"primary_horizon":5,
        "issuer_split":"sha256 issuer modulo5: 0/1/2 development; 3/4 issuer stability check",
        "warning":"v1 outcomes already reviewed; issuer check is not pristine OOS. No usable chronological training/test split at 5-day horizon.",
        "qualification":{"min_rows":100,"min_issuers":30,"min_per_date":10,"min_signal_bp":1,"min_gain_bp":.05,"min_positive_dates":3}}
    if not registered.exists():save(registered,spec)
    else:spec=json.loads(registered.read_text(encoding="utf8"))
    f=enrich(source,out)
    labels=pd.read_csv(source/"backtest_detail.csv",dtype={"date":str,"code":str,"target_date":str})
    labels=labels[labels.model=="joint"][["date","code","horizon","target_date","actual_delta_bp","future_spread_bp"]]
    data=f.merge(labels,on=["date","code"],how="inner",validate="one_to_many")
    five=data[data.horizon==5].reset_index(drop=True)
    dev=five[five.split=="development"].reset_index(drop=True)
    chk=five[five.split=="issuer_check"].reset_index(drop=True)
    candidates=[]
    for cfg in spec["configs"]:
        delta,mask=apply_config(dev,cfg)
        candidates.append({**cfg,**metrics(dev,delta,mask)})
    scores=pd.DataFrame(candidates)
    scores["qualifies"]=scores.apply(qualify,axis=1)
    scores.to_csv(out/"development_candidates.csv",index=False,encoding="utf-8-sig")
    eligible=scores[scores.qualifies].sort_values(["date_equal_improvement_bp","n"],ascending=False)
    fallback={"id":"watch_only_consensus","anchor":"consensus","gate":"trade_confirmed","scale":.5,"minimum":1.}
    chosen={k:eligible.iloc[0][k] for k in ("id","anchor","gate","scale","minimum")} if len(eligible) else fallback
    # The check result can veto release, but may not choose a replacement candidate.
    cd,cm=apply_config(chk,chosen);check_metrics=metrics(chk,cd,cm)
    release=bool(len(eligible) and check_metrics.get("n",0)>=50 and check_metrics.get("issuers",0)>=15
        and check_metrics.get("improvement_bp",-1)>0 and check_metrics.get("date_equal_improvement_bp",-1)>0
        and check_metrics.get("family_equal_improvement_bp",-1)>0 and check_metrics.get("positive_dates",0)>=3)
    result={"chosen":chosen,"development_qualifying_configs":len(eligible),"issuer_check":check_metrics,
        "release_experimental_signals":release,"not_independent_oos":True}
    save(out/"selection.json",result)
    print("SELECTION",json.dumps(result,ensure_ascii=False),flush=True)
    # Show all candidates on complete samples for transparency, not further selection.
    all_scores=[]
    for cfg in spec["configs"]:
        delta,mask=apply_config(five,cfg)
        all_scores.append({**cfg,**metrics(five,delta,mask)})
    pd.DataFrame(all_scores).to_csv(out/"all_candidates_diagnostic.csv",index=False,encoding="utf-8-sig")
    comparison=[]
    chosen_frames=[]
    for h in (3,5):
        base=data[data.horizon==h].reset_index(drop=True)
        for split in ("all","development","issuer_check"):
            subset=base if split=="all" else base[base.split==split]
            delta,mask=apply_config(subset,chosen)
            comparison.append({"horizon":h,"split":split,**metrics(subset,delta,mask)})
        delta,mask=apply_config(base,chosen)
        selected=base.loc[mask].copy();selected["v2_delta_bp"]=delta.loc[mask]
        selected["v2_error_bp"]=(selected.actual_delta_bp-selected.v2_delta_bp).abs()
        selected["v1_error_bp"]=(selected.actual_delta_bp-selected.delta_bp).abs()
        selected["baseline_error_bp"]=selected.actual_delta_bp.abs()
        chosen_frames.append(selected)
    comparison=pd.DataFrame(comparison)
    comparison.to_csv(out/"chosen_metrics.csv",index=False,encoding="utf-8-sig")
    selected=pd.concat(chosen_frames,ignore_index=True)
    selected.to_csv(out/"selected_backtest.csv",index=False,encoding="utf-8-sig")
    matched=selected.groupby("horizon").agg(n=("code","size"),v2_mae=("v2_error_bp","mean"),v1_mae=("v1_error_bp","mean"),baseline_mae=("baseline_error_bp","mean")).reset_index()
    matched.to_csv(out/"matched_comparison.csv",index=False,encoding="utf-8-sig")
    daily=selected.groupby(["horizon","date"]).agg(n=("code","size"),v2_mae=("v2_error_bp","mean"),baseline_mae=("baseline_error_bp","mean")).reset_index()
    daily.to_csv(out/"daily_stability.csv",index=False,encoding="utf-8-sig")
    last=f[f.date==f.date.max()].copy()
    d,m=apply_config(last,chosen)
    last["data_signal_pass"]=m
    last["watch_delta_bp"]=d.where(m)
    last["v2_fair_yield"]=(last.official_yield+d/100).where(m&release)
    last["v2_fair_spread_bp"]=(last.base_spread+d).where(m&release)
    last["status"]=np.where(m,"探索信号" if release else "观察名单：模型未通过稳定性门槛","不预测")
    last["refusal_reason"]=["" if m.loc[i] and release else "模型整体未通过稳定性门槛" if m.loc[i] else refusal(r,chosen) for i,r in last.iterrows()]
    universe=pd.DataFrame(json.loads((source/"inputs/universe.json").read_text(encoding="utf8"))["bonds"])[["code","name","issuer"]]
    columns=["code","date","official_yield","base_spread","v2_fair_yield","v2_fair_spread_bp","watch_delta_bp","status","refusal_reason",
        "quote_days","two_sided_days","trade_days","trade_count","median_width_bp","last_quote_age","last_trade_age","q_delta","t_delta","data_signal_pass"]
    all_latest=universe.merge(last[columns],on="code",how="left")
    all_latest["status"]=all_latest.status.fillna("不预测")
    all_latest["refusal_reason"]=all_latest.refusal_reason.fillna("无可用本券证据或估值曲线基准")
    all_latest.to_csv(out/"latest_all_bonds_v2.csv",index=False,encoding="utf-8-sig")
    watch=all_latest[all_latest.data_signal_pass==True].copy()
    watch.to_csv(out/"latest_watchlist.csv",index=False,encoding="utf-8-sig")
    all_latest.to_json(out/"latest_all_bonds_v2.json",orient="records",force_ascii=False)
    # Every non-selected security must have empty forecast, not a zero prediction.
    assert all_latest.loc[all_latest.status=="不预测",["v2_fair_yield","v2_fair_spread_bp"]].isna().all().all()
    if not release:assert all_latest.v2_fair_yield.isna().all()
    reasons=all_latest.refusal_reason.value_counts().rename_axis("原因").reset_index(name="代码数")
    reasons.to_csv(out/"abstention_reasons.csv",index=False,encoding="utf-8-sig")
    # Fail-safe: a bad model is allowed to publish no estimates.
    summary={**result,"universe_codes":len(all_latest),"v1_available_codes":len(last),"watchlist_codes":len(watch),
             "published_codes":int(all_latest.v2_fair_yield.notna().sum()),"configs_tested":len(spec["configs"])}
    save(out/"summary.json",summary)
    report(out,summary,scores,comparison,matched,daily,reasons,watch)
    print("FINAL",json.dumps(summary,ensure_ascii=False),flush=True)


def report(out,s,dev,comparison,matched,daily,reasons,watch):
    def table(f):
        return f.to_html(index=False,float_format=lambda x:f"{x:.3f}",na_rep="—",escape=True)
    ranking=dev.sort_values("date_equal_improvement_bp",ascending=False).head(15)
    columns=["id","n","issuers","coverage","mae_bp","baseline_mae_bp","improvement_bp","positive_dates","mean_abs_signal_bp","qualifies"]
    status="可发布探索性信号" if s["release_experimental_signals"] else "不发布公允值，仅保留观察名单"
    doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>第二轮：有证据才预测</title><style>body{{font:15px/1.7 'Microsoft YaHei',sans-serif;background:#f5f7fb;color:#17334b;margin:32px}}main{{max-width:1400px;margin:auto}}.box{{background:white;padding:20px;margin:18px 0;border-radius:9px;overflow:auto}}table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}td,th{{padding:8px;border-bottom:1px solid #d9e2ea}}th{{background:#eaf1f7}}.warn{{background:#fff1d8;padding:16px}}a{{color:#17638e}}</style><main>
<h1>第二轮研究：先判断是否值得预测</h1><p>保留首版；同一冻结数据，5日窗口，重点看未来5日兑现。</p>
<div class="box"><b>{status}</b><p>固定池 {s['universe_codes']:,} 个上市代码；符合所选数据与信号规则 {s['watchlist_codes']:,} 个；发布探索公允值 {s['published_codes']:,} 个。</p><p>所选规则：{html.escape(str(s['chosen']))}</p></div>
<p class="warn">本轮比较{s['configs_tested']}个预先列明候选。已看过首版全样本结果，因此所有改善均属事后探索；主体分组只检查跨主体稳定性，不是独立未见测试。5日仅4个成熟起点，无法构造标签先成熟再预测的时间样本外验证。禁止将主体筛选或覆盖率收缩包装成已证实预测力。</p>
<h2>核心变化</h2><div class="box"><p>比较删除趋势、数量和主体修正，仅保留价格水平；比较报价、成交、两者同向共识以及官方估值落在双边区间之外的边界信号。</p><p>双边报价至少3日、当日仍有有效报价、价差不超过5BP、新鲜度至少0.5。严格成交确认还要求至少2个成交日、合计至少3笔、最近成交距起点不超过1交易日、成交离散度不超过3BP，且报价成交同向、偏离差不超过3BP。</p><p>不把0.1BP之类极小预测当作有效信号。候选诊断同时观察0.5与1BP，正式筛选只接受至少1BP；不符合条件公允值为空并逐券写原因。</p></div>
<h2>所选规则：各范围结果</h2><div class="box">{table(comparison)}</div>
<h2>严格共同样本：第二版、第一版和不变基准</h2><div class="box">{table(matched)}</div>
<h2>逐日起点稳定性</h2><div class="box">{table(daily)}</div>
<h2>开发主体候选诊断（前15项不是正式推荐）</h2><div class="box">{table(ranking[columns])}</div>
<h2>为什么不预测</h2><div class="box">{table(reasons)}</div>
<h2>最新观察名单（前30项）</h2><div class="box">{table(watch[['code','name','official_yield','v2_fair_yield','v2_fair_spread_bp','watch_delta_bp','quote_days','trade_days','median_width_bp']].head(30))}</div>
<p>10日仍未成熟。所有精度均以同一批债券的实际未来官方利差变化为标签；另外报告同券组等权、主体等权、每日等权改善及“始终看收窄”的方向比例，防止市场单边行情制造虚假胜率。</p>
<p><a href="latest_all_bonds_v2.csv">全池预测与拒绝原因</a> · <a href="latest_watchlist.csv">观察名单</a> · <a href="development_candidates.csv">开发候选完整结果</a> · <a href="all_candidates_diagnostic.csv">全样本诊断</a> · <a href="selected_backtest.csv">所选规则逐券兑现</a></p></main></html>'''
    (out/"report.html").write_text(doc,encoding="utf8")


if __name__=="__main__":main()
