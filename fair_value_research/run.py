"""Run frozen offline fair-value models and multi-horizon realization checks."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

from .collect import save
from .model import Parameters, evidence, finite, predict, realization, volume_imbalance


RATING_CURVES = {"AAA+":"中短票AAA", "AAA":"中短票AAA", "AAA-":"中短票AAA",
    "AA+":"中短票AA+", "AA":"中短票AA", "AA(2)":"中短票AA", "AA-":"中短票AA",
    "A+":"中短票AA", "A":"中短票AA", "A-":"中短票AA"}


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def date(day):
    return datetime.strptime(day.replace("-", "")[:8], "%Y%m%d").date()


class Inputs:
    def __init__(self, root):
        self.root = root
        self.bonds = read(root / "universe.json")["bonds"]
        self.calendar = read(root / "calendar.json")
        self.curves = read(root / "curves.json")
        self.ratings = {k: sorted(v) for k,v in read(root / "ratings.json").items()}
        self.values = {p.stem: read(p) for p in (root / "valuations").glob("*.json")}
        self.days = read(root / "manifest.json")["quote_days"]
        self.quotes = {}
        self.trades = defaultdict(dict)
        self.trade_status = {}
        self.audit = defaultdict(int)
        self._curves_cache = {}
        self._term_cache = {}
        self._actual_cache = {}
        self._quote_daily()
        self._trades()

    def rating(self, bond, cutoff):
        values = [r for d,r in self.ratings.get(bond.get("secode"), []) if d < cutoff]
        return values[-1] if values else None

    def curve_name(self, bond, cutoff):
        # Exact legacy project mapping, documented in the report.
        if "二级" in bond["name"] or "资本" in bond["name"]:
            return "股份行二级资本债"
        return RATING_CURVES.get(self.rating(bond, cutoff))

    def term(self, bond, day):
        key=(bond["code"],day)
        if key in self._term_cache:
            return self._term_cache[key]
        maturity = bond.get("effective_maturity_date")
        if not maturity:
            return None
        value=(date(maturity)-date(day)).days / 365.0
        self._term_cache[key]=value
        return value

    def curve(self, name, day, term):
        if name is None or term is None or term < 0:
            return None
        key = (name, day)
        if key not in self._curves_cache:
            pts = sorted((float(t), finite(v.get(day))) for t,v in self.curves.get(name, {}).items() if finite(v.get(day)) is not None)
            self._curves_cache[key] = pts
        pts = self._curves_cache[key]
        if not pts or term < pts[0][0] or term > pts[-1][0]:
            return None  # no unsupported long-end extrapolation
        return float(np.interp(term, [t for t,v in pts], [v for t,v in pts]))

    def prior_day(self, day):
        i = bisect_left(self.calendar, day)
        return self.calendar[i-1] if i else None

    def _quote_daily(self):
        cached=self.root/"quote_daily_v2.json"
        if cached.exists():
            cache=read(cached)
            self.quotes=cache["quotes"]
            self.audit.update(cache["audit"])
            return
        for day in self.days:
            groups = defaultdict(list)
            seen = set()
            for q in read(self.root / "quotes" / f"{day}.json"):
                observed = datetime.fromisoformat(q["observed_at"])
                if observed.time() > datetime.strptime("16:05", "%H:%M").time():
                    continue
                clean = {"day":day}
                for side in ("bid", "ofr"):
                    y = finite(q.get(side+"_yield"))
                    stamp = q.get(side+"_time") or q.get("quote_time")
                    fresh = 0.5
                    if stamp:
                        try:
                            ts = datetime.fromisoformat(stamp)
                            age_hours = (observed-ts).total_seconds()/3600
                            if age_hours < 0 or age_hours > 24:
                                self.audit["stale_or_future_quote_sides"] += 1
                                continue
                            fresh = 2 ** (-age_hours/6)
                        except ValueError:
                            self.audit["unknown_quote_timestamp"] += 1
                    if y is None or y <= 0 or y > 30:
                        continue
                    clean[side] = y
                    clean[side+"_fresh"] = fresh
                    vol = finite(q.get(side+"_volume_value"))
                    txt = str(q.get(side+"_volume_text") or "").strip()
                    clean[side+"_volume"] = vol if vol is not None and vol > 0 and txt not in {"--", "-", ""} else None
                if "bid" not in clean and "ofr" not in clean:
                    continue
                if "bid" in clean and "ofr" in clean and clean["bid"] < clean["ofr"]:
                    self.audit["crossed_snapshots"] += 1
                    continue
                # Repeated polling is not new size. Keep latest observation of each state.
                sig = (q["code"], clean.get("bid"), clean.get("ofr"), clean.get("bid_volume"), clean.get("ofr_volume"),
                       q.get("bid_time"), q.get("ofr_time"), q.get("bid_broker"), q.get("ofr_broker"))
                if sig in seen:
                    self.audit["duplicate_quote_states"] += 1
                    continue
                seen.add(sig)
                clean["imbalance"] = volume_imbalance(clean.get("bid_volume"), clean.get("ofr_volume"))
                groups[q["code"]].append(clean)
            daily = {}
            for code, rows in groups.items():
                item = {"day":day, "states":len(rows)}
                for side in ("bid", "ofr"):
                    vals = [r[side] for r in rows if side in r]
                    item[side] = median(vals) if vals else None
                    volumes=[r[side+"_volume"] for r in rows if r.get(side+"_volume") is not None]
                    item[side+"_volume"]=median(volumes) if volumes else None
                double=[r for r in rows if "bid" in r and "ofr" in r]
                item["last_mid"]=(double[-1]["bid"]+double[-1]["ofr"])/2 if double else None
                im = [r["imbalance"] for r in rows if r["imbalance"] is not None]
                item["imbalance"] = median(im) if im else None
                item["freshness"] = median([r.get(s+"_fresh", 0.5) for r in rows for s in ("bid", "ofr") if s in r])
                daily[code] = item
            self.quotes[day] = daily
            print("quote features", day, len(daily), flush=True)
        save(cached,{"quotes":self.quotes,"audit":dict(self.audit)})

    def _trades(self):
        for p in sorted((self.root / "trades").glob("chunk5_*.json")):
            batch = read(p)
            batch_codes = {code.split(".")[0] for code in batch["codes"]}
            for code in batch_codes:
                self.trade_status[code] = batch["status"]
            for row in batch["rows"]:
                code = str(row.get("security_id") or row.get("bond_code") or "").split(".")[0]
                day = str(row.get("issue_date") or "").replace("-", "")[:8]
                y = finite(row.get("yield"))
                num = finite(row.get("trading_num"))
                if code in batch_codes and num is not None and num<=0:
                    self.audit["no_trade_day_rows"]+=1
                    continue
                if code not in batch_codes or not day or y is None or num is None or num <= 0 or not 0 < y < 30:
                    self.audit["unusable_trade_rows"] += 1
                    continue
                key = self.trades[code]
                if day in key and key[day] != row:
                    self.audit["duplicate_trade_day_rows"] += 1
                    continue
                key[day] = row
                if finite(row.get("trading_volume")) is not None:
                    self.audit["trade_rows_with_volume_field"]+=1
                self.audit["usable_trade_day_rows"]+=1

    def base(self, bond, day):
        prev = self.prior_day(day)
        if prev is None:
            return None
        y = self.values.get(prev, {}).get(bond["code"])
        name = self.curve_name(bond, day)
        term = self.term(bond, day)
        c = self.curve(name, prev, term)
        if y is None or c is None or term is None or term <= 0:
            return None
        return {"valuation_date":prev, "official_yield":y, "curve_yield":c,
                "base_spread":(y-c)*100, "curve_name":name, "term":term,
                "rating":self.rating(bond, day)}

    def features(self, bond, day, window):
        idx = self.calendar.index(day)
        past = self.calendar[max(0, idx-window+1):idx+1]
        quotes, trades = [], []
        code = bond["code"]
        origin_curve=self.curve_name(bond,day)
        for d in past:
            age = idx-self.calendar.index(d)
            prev = self.prior_day(d)
            curve = self.curve(origin_curve, prev, self.term(bond, d))
            q = self.quotes.get(d, {}).get(code)
            if q and curve is not None:
                quotes.append({**q, "age":age,
                    "bid":(q["bid"]-curve)*100 if q["bid"] is not None else None,
                    "ofr":(q["ofr"]-curve)*100 if q["ofr"] is not None else None,
                    "last_mid":(q["last_mid"]-curve)*100 if q.get("last_mid") is not None else None})
            # Daily trade aggregates become available the following day. Never use origin-day aggregate.
            t = self.trades.get(code.split(".")[0], {}).get(d) if d < day else None
            tc = self.curve(origin_curve, d, self.term(bond, d))
            if t and tc is not None:
                trades.append({"day":d, "age":age, "spread":(float(t["yield"])-tc)*100,
                               "num":float(t["trading_num"]), "gvn":finite(t.get("gvn_trade_num")),
                               "tkn":finite(t.get("tkn_trade_num"))})
        return {"quotes":quotes, "trades":trades}

    def actual(self, bond, day, fixed_curve):
        key=(bond["code"],day,fixed_curve)
        if key in self._actual_cache:
            return self._actual_cache[key]
        y = self.values.get(day, {}).get(bond["code"])
        c = self.curve(fixed_curve, day, self.term(bond, day))
        result=((y-c)*100, y, c) if y is not None and c is not None else None
        self._actual_cache[key]=result
        return result


def peer_average(bond, own, records, max_term_gap=2.0):
    def family(b):
        if b.get("issue_date") and b.get("effective_maturity_date"):
            return (b.get("issue_company_code") or b.get("issuer"),b["issue_date"],b["effective_maturity_date"],b.get("sub"),b.get("guarantor",""))
        return (b["code"],)
    values, weights = [], []
    seen=set()
    # Conservative family exclusion, not a claimed official cross-market identity.
    for other, base, ev in sorted(records,key=lambda r:r[2]["quality"],reverse=True):
        key=family(other)
        if key==family(bond) or key in seen or other.get("sub") != bond.get("sub") or other.get("guarantor", "") != bond.get("guarantor", ""):
            continue
        gap = abs(base["term"]-own["term"])
        if gap > max_term_gap or not ev["has_evidence"]:
            continue
        seen.add(key)
        weights.append(ev["quality"] / (1+gap))
        values.append(max(-10, min(10, ev["signal"])))
    return (sum(v*w for v,w in zip(values,weights))/sum(weights), len(values)) if sum(weights) else (None,0)


def run_models(data, params):
    rows = []
    for day in data.days[params.window-1:]:
        features = {}
        for b in data.bonds:
            if b.get("issue_date") and date(b["issue_date"]) > date(day):
                continue
            base = data.base(b, day)
            if base:
                features[b["code"]] = (b, base, data.features(b, day, params.window))
        for mode in ("quote", "trade", "joint"):
            issuers = defaultdict(list)
            evaluated = []
            for code, (b, base, f) in features.items():
                ev = evidence(f, base["base_spread"], params, mode)
                evaluated.append((b,base,ev))
                issuers[b.get("issue_company_code") or b.get("issuer") or code].append((b,base,ev))
            for b, base, ev in evaluated:
                if not ev["has_evidence"]:
                    continue
                peer, peers = peer_average(b, base, issuers[b.get("issue_company_code") or b.get("issuer") or b["code"]])
                fair, parts = predict(base["base_spread"], ev, peer, params)
                rows.append({"date":day,"code":b["code"],"name":b["name"],"issuer":b.get("issuer"),
                    "sub":b.get("sub"),"mode":mode,**base,"fair_spread_bp":fair,
                    "fair_yield":base["curve_yield"]+fair/100 if fair is not None else None,
                    "delta_bp":fair-base["base_spread"] if fair is not None else None,
                    "quote_days":ev["quote_days"],"trade_days":ev["trade_days"],"quality":ev["quality"],
                    "offer_only_days":ev["offer_only_days"],
                    "peer_count":peers,"quote_center":ev["quote_center"],"latest_mid":ev["latest_mid"],
                    "latest_trade":ev["latest_trade"],"trade_center":ev["trade_center"],
                    "trade_status":data.trade_status.get(b["code"].split(".")[0], "not_fetched"),
                    "components":json.dumps(parts,ensure_ascii=False),
                    "reason":"" if fair is not None else "本券该版本无有效市场证据"})
        print("model date", day, flush=True)
    return pd.DataFrame(rows)


def backtest(data, predictions):
    bonds = {b["code"]:b for b in data.bonds}
    results, coverage = [], []
    for row in predictions.to_dict("records"):
        if finite(row["fair_spread_bp"]) is None:
            continue
        idx = data.calendar.index(row["date"])
        for horizon in (3,5,10):
            target_idx = idx+horizon
            if target_idx >= len(data.calendar):
                coverage.append({"date":row["date"],"code":row["code"],"mode":row["mode"],"horizon":horizon,"status":"未到期"})
                continue
            target_day = data.calendar[target_idx]
            b = bonds[row["code"]]
            # Freeze origin curve identity to isolate spread move; flag rating migration separately.
            actual = data.actual(b, target_day, row["curve_name"])
            if actual is None:
                coverage.append({"date":row["date"],"code":row["code"],"mode":row["mode"],"horizon":horizon,"status":"未来标签缺失"})
                continue
            future, fy, fc = actual
            path = [data.actual(b,d,row["curve_name"]) for d in data.calendar[idx+1:target_idx+1]]
            complete_path = all(x is not None for x in path)
            values = [x[0] for x in path if x is not None]
            targets = {row["mode"]:row["fair_spread_bp"]}
            if row["mode"] == "joint":
                targets.update({"official":row["base_spread"],"latest_mid":row["latest_mid"],
                                "quote_center":row["quote_center"],"latest_trade":row["latest_trade"]})
            for model, target in targets.items():
                if finite(target) is None:
                    continue
                stats = realization(row["base_spread"],target,future,values if complete_path else [])
                results.append({"date":row["date"],"code":row["code"],"name":row["name"],"model":model,
                    "horizon":horizon,"target_date":target_day,"base_spread":row["base_spread"],
                    "fair_spread_bp":target,"future_spread_bp":future,
                    "yield_error_bp":abs((row["curve_yield"]+target/100-fy)*100),
                    "curve_roll_change_bp":(fc-row["curve_yield"])*100,
                    "rating_changed":data.curve_name(b,target_day)!=row["curve_name"],
                    "complete_path":complete_path,"quote_days":row["quote_days"],"trade_days":row["trade_days"],
                    **stats})
    return pd.DataFrame(results),pd.DataFrame(coverage)


def summarize(bt):
    out=[]
    if bt.empty:
        return pd.DataFrame()
    for (model,h),g in bt.groupby(["model","horizon"]):
        daily=[]
        for d,z in g.groupby("date"):
            ic=z["predicted_delta_bp"].rank().corr(z["actual_delta_bp"].rank()) if z["predicted_delta_bp"].nunique()>1 and z["actual_delta_bp"].nunique()>1 else None
            daily.append({"date":d,"mae":z.error_bp.mean(),"ic":ic})
        daily=pd.DataFrame(daily)
        active=g[g.predicted_delta_bp.abs()>=1]
        out.append({"model":model,"horizon":h,"origins":g.date.nunique(),"observations":len(g),"bonds":g.code.nunique(),
            "mae_bp":g.error_bp.mean(),"daily_equal_mae_bp":daily.mae.mean(),"baseline_mae_bp":g.baseline_error_bp.mean(),
            "median_error_bp":g.error_bp.median(),"p90_error_bp":g.error_bp.quantile(.9),
            "direction_accuracy":active.direction_correct.mean(),"target_reached":active.target_reached.mean(),
            "touched":active.touched.mean(),"max_adverse_mean_bp":active.max_adverse_bp.mean(),
            "mean_convergence_bp":g.convergence_bp.mean(),"rank_ic":daily.ic.mean(),"active_observations":len(active)})
    return pd.DataFrame(out)


def export_report(out, data, pred, bt, summary, sensitivity, common):
    latest=pred[(pred.date==data.days[-1]) & (pred["mode"]=="joint")].copy()
    # Left join ensures every bond in the frozen pool appears, including missing base/curve.
    all_bonds=pd.DataFrame(data.bonds)[["code","name","issuer"]]
    latest=all_bonds.merge(latest.drop(columns=["name","issuer"]),on="code",how="left")
    latest["reason"]=latest["reason"].fillna("官方估值、历史评级、期限或曲线缺失")
    latest["date"]=latest["date"].fillna(data.days[-1])
    bond_map={b["code"]:b for b in data.bonds}
    for index,row in latest[latest.fair_yield.isna()].iterrows():
        base=data.base(bond_map[row.code],data.days[-1])
        latest.at[index,"trade_status"]=data.trade_status.get(row.code.split(".")[0],"not_fetched")
        if base:
            for k,v in base.items():
                latest.at[index,k]=v
            latest.at[index,"reason"]="本券最近5日无有效报价或已发布成交"
            latest.at[index,"quote_days"]=0
            latest.at[index,"trade_days"]=0
            latest.at[index,"quality"]=0
    lo=(latest.delta_bp*.7).clip(-20,20)
    hi=(latest.delta_bp*1.3).clip(-20,20)
    latest["sensitivity_low_yield"]=latest.official_yield+np.minimum(lo,hi)/100
    latest["sensitivity_high_yield"]=latest.official_yield+np.maximum(lo,hi)/100
    latest["abs_delta"]=latest.delta_bp.abs()
    latest=latest.sort_values("abs_delta",ascending=False,na_position="last").drop(columns="abs_delta")
    for name,df in [("latest_all_bonds",latest),("historical_predictions",pred),("backtest_detail",bt),
                    ("metrics",summary),("common_sample_metrics",common),("sensitivity",sensitivity)]:
        df.to_csv(out/f"{name}.csv",index=False,encoding="utf-8-sig",float_format="%.6f")
    latest.to_json(out/"latest_all_bonds.json",orient="records",force_ascii=False)
    summary.to_json(out/"metrics.json",orient="records",force_ascii=False)
    valid=int(latest.fair_yield.notna().sum())
    trade_valid=int((latest.trade_days.fillna(0)>0).sum())
    def table(df):
        columns={"model":"模型","horizon":"兑现交易日","origins":"起点数","observations":"样本数","bonds":"债券数",
            "mae_bp":"MAE(BP)","daily_equal_mae_bp":"日等权MAE","baseline_mae_bp":"不变基准MAE","median_error_bp":"误差中位数",
            "p90_error_bp":"P90误差","direction_accuracy":"方向正确率","target_reached":"终点到达率","touched":"期间触及率",
            "max_adverse_mean_bp":"反向变动均值","mean_convergence_bp":"距离收敛BP","rank_ic":"日均Rank IC","active_observations":"非中性信号数",
            "adjustment_scale":"修正倍率","code":"债券代码","name":"债券简称","official_yield":"官方收益率%","fair_yield":"公允估值%",
            "fair_spread_bp":"公允利差BP","delta_bp":"偏离BP","quote_days":"报价天数","trade_days":"成交天数","quality":"证据质量",
            "scenario":"场景","ablation":"移除因素"}
        display=df.copy()
        if "model" in display:
            display["model"]=display["model"].replace({"joint":"联合模型","quote":"报价模型","trade":"成交模型","official":"官方利差不变",
                "latest_mid":"最新双边中心","quote_center":"衰减报价中心","latest_trade":"最近成交"})
        return display.rename(columns=columns).to_html(index=False,float_format=lambda v:f"{v:.3f}",escape=True,na_rep="—")
    conclusion="可用样本不足，无法比较。"
    five=summary[(summary.model=="joint") & (summary.horizon==5)] if not summary.empty else pd.DataFrame()
    if not five.empty:
        r=five.iloc[0]
        conclusion=f"5日联合模型MAE为{r.mae_bp:.2f}BP，同样本官方利差不变为{r.baseline_mae_bp:.2f}BP；本轮{'优于' if r.mae_bp<r.baseline_mae_bp else '未优于'}不变基准。仅{int(r.origins)}个起点，不据此作稳定有效性判断。"
    paired=common[common.horizon==5] if not common.empty else pd.DataFrame()
    if set(paired.model if not paired.empty else []) >= {"joint","quote"}:
        qm=paired.loc[paired.model=="quote","mae_bp"].iloc[0]
        jm=paired.loc[paired.model=="joint","mae_bp"].iloc[0]
        bm=paired.loc[paired.model=="joint","baseline_mae_bp"].iloc[0]
        conclusion+=f" 共同样本上报价模型MAE为{qm:.2f}BP，联合模型为{jm:.2f}BP，加入成交{'改善' if jm<qm else '未改善'}本轮误差；该共同样本的不变基准为{bm:.2f}BP，联合模型仍需与它比较。"
    scenario_path=out/"scenario_metrics.csv"
    ablation_path=out/"ablation_metrics.csv"
    extra=""
    for title,path in [("业务场景检验",scenario_path),("因素移除检验",ablation_path)]:
        if path.exists():
            frame=pd.read_csv(path)
            extra+=f'<h2>{title}</h2><div class="card scroll">{table(frame)}</div>'
    limitation="历史仅13个交易日，5日兑现最多4个起点；不存在独立训练后的样本外证据。研究使用2026-08-27门户固定债券池，不是逐日重建全市场；债券静态条款来自本次Oracle查询。历史评级使用事件日期并保守滞后一天，缺少原始发布日期及修订版本，不能视为完全点时数据。数量按上市代码统计，跨市场代码不等于独立债券；主体参考保守排除同主体同发行日同有效到期日的整个券组，其他券组最多取一只，不声称取得官方同券映射。"
    text=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>公允估值研究</title>
<style>body{{font:15px/1.7 "Microsoft YaHei",sans-serif;background:#f5f7fa;color:#172b43;margin:36px}}main{{max-width:1450px;margin:auto}}h1,h2{{color:#123750}}.card{{background:white;padding:22px;margin:20px 0;border-radius:10px}}table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}td,th{{padding:8px;border-bottom:1px solid #dde3e9;text-align:right}}th{{background:#edf3f7}}.scroll{{overflow:auto}}.warn{{border-left:5px solid #ba7520;padding:15px;background:#fff6e6}}a{{color:#145b96}}</style><main>
<h1>公允估值与多期限兑现研究</h1><p>观察截止 {data.days[-1]} 16:05 · 5交易日窗口 · 预设参数探索版</p>
<div class="card"><b>全池 {len(latest):,} 只 · 有公允值 {valid:,} 只 · 最近窗口有可用成交 {trade_valid:,} 只</b>
<p>公允利差相对项目既有评级曲线；公允收益率用起点最新可得曲线还原。输入为截止16:05的报价与上一交易日及更早已发布的日度成交统计、估值和曲线。</p></div>
<p class="warn">{limitation}</p>
<div class="card"><b>首轮结论</b><p>{conclusion}</p></div>
<h2>兑现表现</h2><p>MAE单位BP；重点看5日。方向与到达率只统计预测偏离至少1BP的信号。官方不变基准也在同一联合模型样本上测算。10日未成熟，留空而非按0计算。</p><div class="card scroll">{table(summary)}</div>
<h2>报价与成交的共同样本比较</h2><p>同一起点、同一债券、同一期限同时有报价版、成交版和联合版结果，才进入下表。</p><div class="card scroll">{table(common)}</div>
<h2>参数敏感性</h2><p>固定改变证据权重，不以最优结果替换预设主模型。不是调优后样本外收益。</p><div class="card scroll">{table(sensitivity)}</div>
{extra}
<h2>最新测算示例</h2><p>按偏离绝对值排序，非投资推荐。完整债券池在下载文件中。</p><div class="card scroll">{table(latest[["code","name","official_yield","fair_yield","fair_spread_bp","delta_bp","quote_days","trade_days","quality"]].head(40))}</div>
<h2>数据和模型解释</h2><div class="card"><p>报价先去除重复状态、交叉和超过24小时未更新的单边，再按交易日稳健聚合。挂量采用对数不平衡，未知不填零。单边Ofr高于估值向上修正，持续多日提高证据权重；新鲜成交与报价冲突时保留分项贡献并降低单源主导程度。主体信号剔除本券，要求同层级、同担保人及期限差不超过2年。</p>
<p>成交首版使用日度收盘收益率、成交笔数、成交趋势与主体成交。日度高低价不是成交中位数；未提供真实成交量，不构造量加权成交价。GVN/TKN字段虽保留，但接口方向语义尚未独立核验，首版不赋予方向系数；盘中报价成交匹配需要分钟数据，本轮不虚构。</p>
<p>评级曲线完全沿用现有映射：AAA档使用中短票AAA，AA+使用AA+，其他既有低评级映射使用AA；名称含二级或资本按既有股份行二级资本债曲线。未来兑现冻结起点曲线身份并按未来期限取曲线，评级迁移单独标记。</p>
<p>曲线没有覆盖的期限不外推。质量分数仅衡量证据量和新鲜度，不是概率或统计置信区间。输出不代表可保证成交的价格。</p>
<p>清洗审计：{html.escape(json.dumps(dict(data.audit),ensure_ascii=False))}</p></div>
<h2>下载</h2><p><a href="latest_all_bonds.csv">最新全量测算</a> · <a href="historical_predictions.csv">历史测算</a> · <a href="backtest_detail.csv">兑现明细</a> · <a href="metrics.csv">指标</a> · <a href="parameters.json">预设参数</a></p></main></html>'''
    (out/"report.html").write_text(text,encoding="utf-8")
    save(out/"run_summary.json",{"universe":len(latest),"fair_values":valid,"trade_covered":trade_valid,
                                 "audit":dict(data.audit),"limitation":limitation,"conclusion":conclusion})


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="outputs/fair_value_v1")
    args=ap.parse_args()
    out=Path(args.output)
    params=Parameters()
    registered=out/"preregistered_parameters.json"
    if registered.exists() and read(registered)["parameters"]!=asdict(params):
        raise ValueError("参数与本次预登记不同；新实验请使用独立输出目录，不能覆盖原研究结果。")
    save(out/"parameters.json",{"main":asdict(params),"registered_before_evaluation":True,"registered_at":datetime.now().isoformat(),
         "sensitivity_scales":[0.7,1.3],"asof_time":"16:05","valuation_release_lag":"previous curve trading day",
         "trade_release_lag":"strictly before origin day","trained":False})
    data=Inputs(out/"inputs")
    pred=run_models(data,params)
    bt,coverage=backtest(data,pred)
    coverage.to_csv(out/"label_coverage.csv",index=False,encoding="utf-8-sig")
    summary=summarize(bt)
    common=pd.DataFrame()
    if not bt.empty:
        subset=bt[bt.model.isin(["quote","trade","joint"])]
        counts=subset.groupby(["date","code","horizon"]).model.nunique()
        keys=counts[counts==3].reset_index()[["date","code","horizon"]]
        common=summarize(subset.merge(keys,on=["date","code","horizon"]))
    sens=[]
    # Scale only pre-registered model adjustment, keeping evidence eligibility fixed.
    for scale in (0.7,1.3):
        adjusted=pred.copy()
        adjusted["fair_spread_bp"]=adjusted.base_spread+(adjusted.delta_bp*scale).clip(-params.total_cap_bp,params.total_cap_bp)
        adjusted["fair_yield"]=adjusted.curve_yield+adjusted.fair_spread_bp/100
        sb,_=backtest(data,adjusted[adjusted["mode"]=="joint"])
        ss=summarize(sb[sb.model=="joint"] if not sb.empty else sb)
        ss["adjustment_scale"]=scale
        sens.append(ss)
    sensitivity=pd.concat(sens,ignore_index=True)
    ablations=[]
    joint=pred[pred["mode"]=="joint"].copy()
    parts=joint.components.map(json.loads)
    for name,keys in [("数量",["volume","size_trend"]),("趋势",["quote_trend","trade_trend"]),("主体",["issuer"])]:
        adjusted=joint.copy()
        remaining=parts.map(lambda p:sum(v for k,v in p.items() if k not in keys and k!="cap_adjustment"))
        adjusted["fair_spread_bp"]=adjusted.base_spread+remaining.clip(-params.total_cap_bp,params.total_cap_bp)
        ab,_=backtest(data,adjusted)
        tab=summarize(ab[ab.model=="joint"])
        tab["ablation"]=name
        ablations.append(tab)
    pd.concat(ablations,ignore_index=True).to_csv(out/"ablation_metrics.csv",index=False,encoding="utf-8-sig")
    scenes=[]
    masks={"持续单边Ofr偏高":(joint.offer_only_days>=3)&parts.map(lambda p:p.get("quote_level",0)>=1),
           "买量占优且报价偏低":parts.map(lambda p:p.get("volume",0)<0 and p.get("quote_level",0)<0),
           "报价收益率下移":parts.map(lambda p:p.get("quote_trend",0)<-.25),
           "主体卖压向上":parts.map(lambda p:p.get("issuer",0)>.25),
           "报价成交同向":parts.map(lambda p:p.get("quote_level",0)*p.get("trade_level",0)>0),
           "报价成交背离":parts.map(lambda p:p.get("quote_level",0)*p.get("trade_level",0)<0)}
    for name,mask in masks.items():
        selected=joint.loc[mask,["date","code"]]
        tab=summarize(bt[bt.model=="joint"].merge(selected,on=["date","code"]))
        tab["scenario"]=name
        scenes.append(tab)
    pd.concat(scenes,ignore_index=True).to_csv(out/"scenario_metrics.csv",index=False,encoding="utf-8-sig")
    export_report(out,data,pred,bt,summary,sensitivity,common)
    print(summary.to_string(index=False),flush=True)


if __name__=="__main__":
    main()
