"""Separate post-hoc event hypothesis: recent offer deterioration, not stale level gap."""
from datetime import datetime
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .widening import qualifies
from .selective import metrics

ROOT=Path('outputs/fair_value_widening')


def apply(f,c):
    base=(f.offer_days>=3)&(f.offer_age==0)&(f.offer_freshness>=.8)&(f.offer_slope>=.25)
    gates={'any':True,'bilateral':(f.two_sided_days>=3)&(f.last_width_bp<=5),
        'single_offer':f.offer_only_days>=3,'peer':(f.peer_n>=2)&(f.peer_rising_share>=.5),
        'sell_size':(f.known_offer_size_days>=3)&(f.offer_size_growth>=1.5),
        'official_continuation':f.past5_official_delta>=1}
    # Normalize recent offer slope to a 4-day historical movement, independent of forecast horizon.
    delta=(f.offer_slope*4*c['scale']).clip(upper=10)
    return delta,base&gates[c['gate']]&delta.ge(1)


def main():
    out=ROOT/'events';out.mkdir(exist_ok=True)
    configs=[dict(id=f'{g}_{s}',gate=g,scale=s) for g in ['any','bilateral','single_offer','peer','sell_size','official_continuation'] for s in [.5,1.]]
    registry=out/'experiment_registry.json'
    if not registry.exists():registry.write_text(json.dumps({'created':datetime.now().isoformat(),'configs':configs,'post_hoc':True,'reason':'Persistent overpriced offers failed; test recent offer deterioration without requiring a positive level gap. No additional grids after this test.','minimum_signal_bp':1,'qualification':'same sample, issuer, date, direction and error thresholds as first widening experiment'},ensure_ascii=False,indent=2),encoding='utf8')
    else:assert json.loads(registry.read_text(encoding='utf8'))['configs']==configs
    f=pd.read_json(ROOT/'features.json',convert_dates=False,dtype={'date':str,'code':str})
    labels=pd.read_csv('outputs/fair_value_v1/backtest_detail.csv',dtype={'date':str,'code':str})
    labels=labels[labels.model=='joint'][['date','code','horizon','actual_delta_bp']]
    full=f.merge(labels,on=['date','code'],validate='one_to_many');five=full[full.horizon==5]
    rows=[]
    for c in configs:
        for split in ['development','issuer_check','all']:
            x=five if split=='all' else five[five.split==split];d,m=apply(x,c)
            rows.append({**c,'split':split,**metrics(x,d,m)})
    scores=pd.DataFrame(rows);scores['qualifies']=scores.apply(lambda r:qualifies(r,r['split']=='issuer_check'),axis=1)
    scores.to_csv(out/'all_candidates.csv',index=False,encoding='utf-8-sig')
    eligible=scores[(scores.split=='development')&scores.qualifies].sort_values(['date_equal_improvement_bp','n'],ascending=False)
    ranked=scores[(scores.split=='development')&(scores.n>=30)].sort_values('date_equal_improvement_bp',ascending=False)
    pick=eligible.iloc[0] if len(eligible) else ranked.iloc[0] if len(ranked) else scores.iloc[0]
    chosen={k:pick[k] for k in ['id','gate','scale']}
    check=scores[(scores.id==chosen['id'])&(scores.split=='issuer_check')].iloc[0]
    release=bool(len(eligible) and qualifies(check,True));tables=[];details=[]
    for h in [3,5]:
        x=full[full.horizon==h];d,m=apply(x,chosen);z=x.loc[m].copy();z['prediction']=d[m]
        z['error_bp']=(z.actual_delta_bp-z.prediction).abs();z['baseline_error_bp']=z.actual_delta_bp.abs()
        z['market_error_bp']=(z.actual_delta_bp-z.past5_market_delta*h/5).abs();z['own_momentum_error_bp']=(z.actual_delta_bp-z.past5_official_delta*h/5).abs();details.append(z)
        for split in ['development','issuer_check','all']:
            a=x if split=='all' else x[x.split==split];dd,mm=apply(a,chosen);tables.append({'horizon':h,'split':split,**metrics(a,dd,mm)})
    detail=pd.concat(details);detail.to_csv(out/'diagnostic_backtest.csv',index=False,encoding='utf-8-sig')
    c=detail[(detail.horizon==5)&(detail.split=='issuer_check')].dropna(subset=['market_error_bp','own_momentum_error_bp'])
    release=bool(release and len(c)>=50 and c.error_bp.mean()<c.market_error_bp.mean() and c.error_bp.mean()<c.own_momentum_error_bp.mean())
    latest=f[f.date==f.date.max()].copy();d,m=apply(latest,chosen)
    latest['diagnostic_delta_bp']=d.where(m);latest['fair_yield']=(latest.official_yield+d/100).where(m&release);latest['fair_spread']=(latest.base_spread+d).where(m&release)
    latest['status']='实验信号' if release else '仅诊断观察：未通过验收'
    latest.loc[m,['date','code','name','issuer','diagnostic_delta_bp','fair_yield','fair_spread','status','offer_slope','offer_days']].to_csv(out/'latest_diagnostic_watchlist.csv',index=False,encoding='utf-8-sig')
    summary={'chosen':chosen,'qualified_development':len(eligible),'release':release,'latest_diagnostic':int(m.sum()),'published':int(latest.fair_yield.notna().sum()),'configs':len(configs)}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    pd.DataFrame(tables).to_csv(out/'chosen_metrics.csv',index=False,encoding='utf-8-sig')
    print(json.dumps(summary,ensure_ascii=False));print(scores[scores.split=='all'][['id','n','issuers','direction_accuracy','improvement_bp']].to_string(index=False))


if __name__=='__main__':main()
