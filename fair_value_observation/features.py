import json
import numpy as np
import pandas as pd
from fair_value_research.model import evidence,Parameters
from fair_value_research.selective import snapshot_features
from fair_value_research.widening import quote_features


def build(data,day):
    if day not in data.calendar:return pd.DataFrame()
    idx=data.calendar.index(day)
    if idx<4:return pd.DataFrame()
    rows=[]
    for b in data.bonds:
        base=data.base(b,day)
        if not base:continue
        fs=data.features(b,day,5);qs=fs['quotes'];ts=fs['trades']
        if not qs:continue
        q=evidence(fs,base['base_spread'],Parameters(),mode='quote')
        t=evidence(fs,base['base_spread'],Parameters(),mode='trade')
        def bound(x):
            if x.get('bid') is not None and base['base_spread']>x['bid']:return x['bid']-base['base_spread']
            if x.get('ofr') is not None and base['base_spread']<x['ofr']:return x['ofr']-base['base_spread']
            return 0.
        qc=q['quote_center'];last=min(qs,key=lambda x:x['age']);qd=qc-base['base_spread'] if qc is not None else np.nan;side=np.sign(qd)
        previous=data.actual(b,data.calendar[idx-1],base['curve_name']) if idx>=1 else None
        older=data.actual(b,data.calendar[idx-6],base['curve_name']) if idx>=6 else None
        family=str((b.get('issuer'),b.get('issue_date'),b.get('effective_maturity_date'),b.get('sub'),b.get('guarantor')))
        r=dict(b,**base,date=day,family=family,quote_days=q['quote_days'],trade_days=t['trade_days'],offer_only_days=q['offer_only_days'],
            quote_level_only=q['components'].get('quote_level',np.nan),q_delta=qd,
            t_delta=t['trade_center']-base['base_spread'] if t['trade_center'] is not None else np.nan,
            quote_center=qc,trade_center=t['trade_center'],any_quote_age=last['age'],any_freshness=last['freshness'],any_boundary=bound(last),
            boundary_consistent_days=sum(np.sign(bound(x))==side and abs(bound(x))>=.5 for x in qs) if side else 0,
            known_relevant_volume_days=sum(x.get('bid_volume' if side<0 else 'ofr_volume') is not None and x.get('bid_volume' if side<0 else 'ofr_volume')>0 for x in qs),
            past5_official_delta=previous[0]-older[0] if previous and older else np.nan,
            trade_status=data.trade_status.get(b['code'].split('.')[0],'unknown'))
        r.update(snapshot_features(fs,base['base_spread']));r.update(quote_features(qs,base['base_spread']))
        r.update(level_delta=r['quote_level_only'],delta_bp=r['quote_level_only'],guarantor=b.get('guarantor') or '')
        rows.append(r)
    f=pd.DataFrame(rows)
    if f.empty:return f
    f=f.sort_values('code').reset_index(drop=True);blocks=[]
    for _,g in f.groupby(['issuer','sub','guarantor'],dropna=False):
        reps=g.drop_duplicates('family');values={}
        for _,r in reps.iterrows():
            peers=reps[(reps.family!=r.family)&((reps.term-r.term).abs()<=2)&(reps.offer_days>=3)&(reps.offer_age==0)&(reps.offer_freshness>=.8)]
            values[r.family]=(len(peers),peers.last_offer_gap.median(),(peers.offer_slope>0).mean() if len(peers) else np.nan)
        for i,r in g.iterrows():blocks.append((i,*values[r.family]))
    f=f.join(pd.DataFrame(blocks,columns=['i','peer_n','peer_gap','peer_rising_share']).set_index('i'))
    f['past5_market_delta']=f.drop_duplicates('family').past5_official_delta.median()
    return f
