"""Post-hoc, finite widening-only investigation. Trades are gates, never anchors."""
import json
from datetime import datetime
from pathlib import Path
import html
import numpy as np
import pandas as pd
from .run import Inputs
from .model import evidence, Parameters, realization
from .selective import metrics

OUT=Path('outputs/fair_value_widening')
GATES=['persistent_offer','offer_only','rising_offer','rising_offer_only','known_sell_size',
       'growing_sell_size','peer_confirmed','trend_peer','trade_confirmed','no_trade_conflict']


def quote_features(quotes,base):
    qs=sorted(quotes,key=lambda q:q['age'],reverse=True)
    offers=[q for q in qs if q.get('ofr') is not None]
    last=qs[-1] if qs else {}
    slope=(offers[-1]['ofr']-offers[0]['ofr'])/(offers[0]['age']-offers[-1]['age']) if len(offers)>=2 and offers[0]['age']>offers[-1]['age'] else np.nan
    sizes=[q['ofr_volume'] for q in offers if q.get('ofr_volume') is not None and q['ofr_volume']>0]
    return dict(offer_days=len(offers),offer_above_days=sum(q['ofr']-base>=1 for q in offers),
        offer_only_above_days=sum(q['ofr']-base>=1 and q.get('bid') is None for q in offers),
        offer_age=last.get('age',np.nan),offer_freshness=last.get('freshness',np.nan),
        last_offer_gap=last.get('ofr',np.nan)-base if last.get('ofr') is not None else np.nan,
        offer_slope=slope,known_offer_size_days=len(sizes),
        offer_size_growth=sizes[-1]/sizes[0] if len(sizes)>=2 else np.nan,
        sell_imbalance=np.mean([q['imbalance'] for q in qs if q.get('imbalance') is not None]) if any(q.get('imbalance') is not None for q in qs) else np.nan)


def peer_features(f):
    result=pd.DataFrame(index=f.index,columns=['peer_n','peer_gap','peer_rising_share'],dtype=float)
    for _,g in f.groupby(['date','issuer','sub','guarantor'],dropna=False):
        g=g.drop_duplicates('family')
        # Representatives are fixed by code order, never by future returns.
        for idx,r in g.iterrows():
            peers=g[(g.family!=r.family)&((g.term-r.term).abs()<=2)&(g.offer_days>=3)&(g.offer_age==0)&(g.offer_freshness>=.8)]
            mask=(f.date==r.date)&(f.family==r.family)
            result.loc[mask,:]=[len(peers),peers.last_offer_gap.median(),(peers.offer_slope>0).mean() if len(peers) else np.nan]
    return result


def features():
    cache=OUT/'features.json'
    if cache.exists():return pd.read_json(cache,convert_dates=False,dtype={'date':str,'code':str})
    f=pd.read_json('outputs/fair_value_v2/persistent_followup/features.json',convert_dates=False,dtype={'date':str,'code':str})
    data=Inputs(Path('outputs/fair_value_v1/inputs'));bonds={b['code']:b for b in data.bonds};rows=[]
    for day,g in f.groupby('date'):
        for r in g.to_dict('records'):
            b=bonds[r['code']];fs=data.features(b,day,5)
            r.update(quote_features(fs['quotes'],r['base_spread']))
            r['quote_level_only']=evidence(fs,r['base_spread'],Parameters(),mode='quote')['components'].get('quote_level',np.nan)
            r['guarantor']=b.get('guarantor') or ''
            rows.append(r)
        print('features',day,len(g),flush=True)
    f=pd.DataFrame(rows).sort_values(['date','code']).reset_index(drop=True)
    # Efficient group-local peer calculation; exclude entire alias family.
    blocks=[]
    for _,g in f.groupby(['date','issuer','sub','guarantor'],dropna=False):
        reps=g.drop_duplicates('family');values={}
        for _,r in reps.iterrows():
            peers=reps[(reps.family!=r.family)&((reps.term-r.term).abs()<=2)&(reps.offer_days>=3)&(reps.offer_age==0)&(reps.offer_freshness>=.8)]
            values[r.family]=(len(peers),peers.last_offer_gap.median(),(peers.offer_slope>0).mean() if len(peers) else np.nan)
        for idx,r in g.iterrows():blocks.append((idx,*values[r.family]))
    peers=pd.DataFrame(blocks,columns=['idx','peer_n','peer_gap','peer_rising_share']).set_index('idx')
    f=f.join(peers);f.to_json(cache,orient='records',force_ascii=False,double_precision=12)
    return f


def apply(f,c):
    common=(f.offer_days>=4)&(f.offer_age==0)&(f.offer_freshness>=.8)&(f.offer_above_days>=3)&(f.last_offer_gap>=1)
    peer=(f.peer_n>=2)&(f.peer_gap>=1)&(f.peer_rising_share>=.5)
    gates={'persistent_offer':True,'offer_only':f.offer_only_above_days>=3,
        'rising_offer':f.offer_slope>=.25,'rising_offer_only':(f.offer_slope>=.25)&(f.offer_only_above_days>=3),
        'known_sell_size':(f.known_offer_size_days>=3)&(f.sell_imbalance>0),
        'growing_sell_size':(f.known_offer_size_days>=3)&(f.offer_size_growth>=1.5),
        'peer_confirmed':peer,'trend_peer':peer&(f.offer_slope>=.25),
        'trade_confirmed':(f.trade_days>=1)&(f.last_trade_age<=2)&(f.t_delta>=1),
        'no_trade_conflict':~((f.trade_days>=1)&(f.last_trade_age<=2)&(f.t_delta<0))}
    delta=(f.quote_level_only*c['scale']).clip(upper=10)
    return delta,common&gates[c['gate']]&delta.ge(1)


def qualifies(r,check=False):
    return (r.get('n',0)>=(50 if check else 100) and r.get('issuers',0)>=(15 if check else 30)
        and r.get('origins',0)==4 and r.get('min_date_n',0)>=10 and r.get('positive_dates',0)>=3
        and r.get('improvement_bp',-1)>.05 and r.get('family_equal_improvement_bp',-1)>0
        and r.get('issuer_equal_improvement_bp',-1)>0 and r.get('direction_accuracy',0)>.55)


def main():
    OUT.mkdir(exist_ok=True,parents=True)
    configs=[dict(id=f'{g}_{s}',gate=g,scale=s) for g in GATES for s in [.25,.5,1.]]
    spec={'created':datetime.now().isoformat(),'post_hoc':True,'horizon':5,'configs':configs,
          'trade_role':'confirmation/veto only; no trade contribution to fair value',
          'minimum_signal_bp':1,'development_min_rows':100,'development_min_issuers':30,
          'check_min_rows':50,'check_min_issuers':15,'min_direction_accuracy':.55,
          'selection':'development equal-date MAE gain; issuer check veto only, no replacement',
          'warning':'Prior outcomes already seen; not independent out-of-sample.'}
    registry=OUT/'experiment_registry.json'
    if registry.exists():
        assert json.loads(registry.read_text(encoding='utf8'))['configs']==configs
    else:registry.write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf8')
    f=features()
    labels=pd.read_csv('outputs/fair_value_v1/backtest_detail.csv',dtype={'date':str,'code':str})
    labels=labels[labels.model=='joint'][['date','code','horizon','target_date','actual_delta_bp']]
    full=f.merge(labels,on=['date','code'],validate='one_to_many');five=full[full.horizon==5].copy()
    records=[]
    for c in configs:
        for split in ['development','issuer_check','all']:
            x=five if split=='all' else five[five.split==split]
            d,m=apply(x,c);records.append({**c,'split':split,**metrics(x,d,m)})
    scores=pd.DataFrame(records);scores['qualifies']=scores.apply(lambda r:qualifies(r,r['split']=='issuer_check'),axis=1)
    scores.to_csv(OUT/'all_candidates.csv',index=False,encoding='utf-8-sig')
    eligible=scores[(scores.split=='development')&scores.qualifies].sort_values(['date_equal_improvement_bp','n'],ascending=False)
    # If nothing qualifies, retain the highest development score only as diagnostic, never publish it.
    ranked=scores[(scores.split=='development')&(scores.n>=30)].sort_values('date_equal_improvement_bp',ascending=False)
    pick=eligible.iloc[0] if len(eligible) else ranked.iloc[0] if len(ranked) else scores.iloc[0]
    chosen={k:pick[k] for k in ['id','gate','scale']}
    check=scores[(scores.id==chosen['id'])&(scores.split=='issuer_check')].iloc[0]
    release=bool(len(eligible) and qualifies(check,True))
    tables=[];detail=[]
    for h in [3,5]:
        x=full[full.horizon==h];d,m=apply(x,chosen);z=x.loc[m].copy();z['prediction']=d[m]
        z['error_bp']=(z.actual_delta_bp-z.prediction).abs();z['baseline_error_bp']=z.actual_delta_bp.abs()
        z['market_error_bp']=(z.actual_delta_bp-z.past5_market_delta*h/5).abs()
        z['own_momentum_error_bp']=(z.actual_delta_bp-z.past5_official_delta*h/5).abs()
        z['target_reached_1bp']=(z.actual_delta_bp>=z.prediction-1)
        detail.append(z)
        for split in ['development','issuer_check','all']:
            xx=x if split=='all' else x[x.split==split];dd,mm=apply(xx,chosen)
            tables.append({'horizon':h,'split':split,**metrics(xx,dd,mm)})
    detail=pd.concat(detail);detail.to_csv(OUT/'diagnostic_backtest.csv',index=False,encoding='utf-8-sig')
    final=pd.DataFrame(tables);final.to_csv(OUT/'chosen_metrics.csv',index=False,encoding='utf-8-sig')
    daily=detail.groupby(['horizon','date']).agg(n=('code','size'),mae=('error_bp','mean'),baseline=('baseline_error_bp','mean'),actual_mean=('actual_delta_bp','mean'))
    daily.to_csv(OUT/'daily_metrics.csv',encoding='utf-8-sig')
    # Comparisons use the intersection of all baseline data, also for the veto.
    complete=detail.dropna(subset=['market_error_bp','own_momentum_error_bp'])
    baselines=complete.groupby(['horizon','split'])[['error_bp','baseline_error_bp','market_error_bp','own_momentum_error_bp']].mean()
    baselines.to_csv(OUT/'matched_baselines.csv',encoding='utf-8-sig')
    c=complete[(complete.horizon==5)&(complete.split=='issuer_check')]
    release=bool(release and len(c)>=50 and c.error_bp.mean()<c.market_error_bp.mean() and c.error_bp.mean()<c.own_momentum_error_bp.mean())
    last=f[f.date==f.date.max()].copy();d,m=apply(last,chosen)
    last['diagnostic_selected']=m;last['diagnostic_delta_bp']=d.where(m)
    last['fair_yield_widening']=(last.official_yield+d/100).where(m&release)
    last['fair_spread_widening']=(last.base_spread+d).where(m&release)
    last['status']=np.where(m,'实验信号' if release else '仅诊断观察：规则未通过验收','不预测')
    cols=['date','code','name','issuer','official_yield','fair_yield_widening','fair_spread_widening','diagnostic_delta_bp','status','offer_days','offer_above_days','offer_only_above_days','offer_slope','last_offer_gap','peer_n','peer_gap','trade_days']
    last[cols].to_csv(OUT/'latest_all_evidence.csv',index=False,encoding='utf-8-sig')
    last.loc[m,cols].to_csv(OUT/'latest_diagnostic_watchlist.csv',index=False,encoding='utf-8-sig')
    summary={'chosen':chosen,'qualified_development':len(eligible),'release':release,'diagnostic_latest':int(m.sum()),'published':int(last.fair_yield_widening.notna().sum()),'configs':len(configs),'data_date':str(f.date.max())}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    msg=f'''走阔专项研究（截至2026-09-16）
本轮仅探索30个预先登记的事后研究组合；成交仅作为确认或否决条件，不贡献公允值幅度。报价为收益率口径，正信号表示评级曲线利差走阔。
开发组合格数：{len(eligible)}；开发组选择：{chosen}；最终允许输出实验公允值：{release}。
最新诊断名单{int(m.sum())}个代码；正式实验输出{summary['published']}个。未通过验收的诊断调整不是公允值，禁止当作已验证信号使用。
每组均要求至少4日报价、3日卖价高于起点官方利差1BP、当日新鲜卖价继续确认、报价水平修正至少1BP。增量条件分别检查纯单边卖盘、卖价上移、已知卖量压力、卖量增长、同主体其他券确认和成交确认/否决。主体参考排除本券整个近似同券组，匹配层级、担保及2年内期限；没有对应报价的债不会仅凭主体信息入选。
幅度只使用报价模型的quote_level贡献，消除成交对报价权重的间接影响。报价历史均在评级曲线利差空间比较。持续性相对同一个起点参考，非每日官方值；卖量仅比较有效日度状态，不累加重复快照。日度成交无法证明某挂单因成交而消失，也未作此推断。
开发组要求100条、30主体、4日起点、每日至少10条、至少3日误差改善、主体和券组等权改善、方向胜率超过55%；发行人检查组至少50条、15主体，再比较可得的市场及本券动量。开发组不合格也会展示最好的诊断项，但不降低门槛、不改选检查组最优项。
所有实验沿用已被研究过的13日数据：发行人分组只是稳定性检查，不是独立样本外；5日只有4个重叠起点，10日未成熟。结果不能归因于单个因子，不能用于断言长期有效。
'''
    (OUT/'研究结论.txt').write_text(msg,encoding='utf8')
    body='<h1>走阔专项研究</h1>'+''.join('<p>'+html.escape(p)+'</p>' for p in msg.splitlines())
    for title,table in [('选中方案各期限结果',final),('同样本基准',baselines.reset_index()),('逐日起点',daily.reset_index()),('全部30组探索',scores),('最新诊断观察',last.loc[m,cols])]:
        body+='<h2>'+title+'</h2>'+table.to_html(index=False,float_format=lambda x:f'{x:.3f}')
    (OUT/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px/1.7 sans-serif;margin:32px;color:#203348}table{border-collapse:collapse;font-size:12px;display:block;overflow:auto}td,th{border:1px solid #ccc;padding:6px;white-space:nowrap}th{background:#edf3f6}p{max-width:1100px}</style>'+body,encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False),flush=True);print(final.to_string(index=False),flush=True)


if __name__=='__main__':main()
