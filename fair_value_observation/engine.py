"""Immutable predictions, historical initialization and first-seen outcomes."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from . import rules
from .storage import ROOT,archive_json,connect,dumps,initialize,now


def install(root=ROOT):
    initialize(root);active,archived=rules.registry()
    manifest=dumps(dict(active=active,archived=archived,minimum_signal=1,window=5,cutoff='16:05',horizons=[3,5,10]))
    with connect(root,True) as db:
        old=db.execute('SELECT manifest FROM versions WHERE version=?',(rules.VERSION,)).fetchone()
        if old and old['manifest']!=manifest:raise RuntimeError('规则已变化，必须新建版本')
        db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,NULL,?)',(rules.VERSION,now(),manifest))
        for r in active:
            for direction in ['tighten','widen']:
                db.execute('INSERT OR IGNORE INTO rules VALUES (?,?,?,?,?,?,?,0)',(rules.VERSION,r['id'],direction,r['family'],r['name'],dumps(r['spec']),dumps(r['sources'])))
    return active


def record(frame,kind,batch,root=ROOT):
    active=install(root)
    if frame.empty:return 0
    if kind not in {'historical','prospective','backfill'}:raise ValueError('unknown cohort')
    created=now();count=0
    with connect(root,True) as db:
        selection={(r['id'],r['direction']):r['selected'] for r in db.execute('SELECT * FROM rules WHERE version=?',(rules.VERSION,))}
        for day,g in frame.groupby('date'):
            if db.execute('SELECT 1 FROM checkpoints WHERE version=? AND origin=?',(rules.VERSION,day)).fetchone():continue
            db.execute('INSERT OR IGNORE INTO batches VALUES (?,?,?,?,?)',(batch,str(day),kind,created,dumps({'features':len(g)})))
            cols=['date','code','name','issuer','family','sub','ct','entity','internal_rating','guarantor','effective_maturity_date','official_yield','base_spread','curve_yield','curve_name','valuation_date','term','rating','quote_days','trade_days','trade_status']
            db.executemany('INSERT OR IGNORE INTO bonds VALUES (?,?,?,?,?,?)',[(rules.VERSION,str(day),r['code'],kind,batch,dumps({k:r.get(k) for k in cols})) for r in g.to_dict('records')])
            for r in active:
                delta,mask=rules.apply(g,r['spec']);selected=g.loc[mask]
                rows=[]
                for idx,x in selected[['code']].iterrows():
                    value=float(delta.loc[idx]);direction='widen' if value>0 else 'tighten'
                    rows.append((rules.VERSION,str(day),x.code,r['id'],direction,kind,batch,created,value,selection[(r['id'],direction)],0,'[]'))
                db.executemany('INSERT OR IGNORE INTO predictions(version,origin,code,rule,direction,kind,batch,created,delta,selected,conflict,members) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',rows)
                count+=len(rows)
            db.execute('INSERT INTO checkpoints VALUES (?,?,?,?)',(rules.VERSION,str(day),kind,batch))
    return count


def combine_values(members):
    groups={}
    for m in members:groups.setdefault(m['family'],[]).append(m['delta'])
    value=float(np.median([np.median(v) for v in groups.values()]))
    conflict=any(m['delta']>0 for m in members) and any(m['delta']<0 for m in members)
    return value,conflict


def combine(root=ROOT):
    with connect(root,True) as db:
        rows=db.execute('''SELECT p.*,r.family FROM predictions p JOIN rules r ON r.version=p.version AND r.id=p.rule AND r.direction=p.direction
            WHERE p.version=? AND r.selected=1 ORDER BY p.origin,p.code,p.rule''',(rules.VERSION,))
        current=None;group=[];out=[]
        def flush(g):
            if not g:return
            r=g[0];members=[dict(rule=x['rule'],family=x['family'],delta=x['delta'],direction=x['direction']) for x in g]
            value,conflict=combine_values(members)
            direction=('widen' if value>0 else 'tighten') if abs(value)>=1 else 'neutral'
            out.append((rules.VERSION,r['origin'],r['code'],'merged',direction,r['kind'],r['batch'],r['created'],value,1,int(conflict),dumps(members)))
        for row in rows:
            key=(row['origin'],row['code'])
            if current!=key:flush(group);group=[];current=key
            group.append(dict(row))
        flush(group)
        db.executemany('INSERT OR IGNORE INTO predictions(version,origin,code,rule,direction,kind,batch,created,delta,selected,conflict,members) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',out)
        from .presentation import freeze_condition_stats
        freeze_condition_stats(db)
        return len(out)


def evaluate(data,root=ROOT):
    bonds={b['code']:b for b in data.bonds};index={d:i for i,d in enumerate(data.calendar)};cache={};timestamp=now();added=0
    with connect(root,True) as db:
        rows=db.execute('''SELECT p.id,p.origin,p.code,p.delta,b.payload FROM predictions p JOIN bonds b
        ON b.version=p.version AND b.origin=p.origin AND b.code=p.code WHERE p.version=? AND p.direction!='neutral' ''',(rules.VERSION,)).fetchall()
        old={(r['prediction'],r['horizon']):r['payload'] for r in db.execute('SELECT prediction,horizon,payload FROM outcomes')}
        inserts=[];revisions=[]
        for r in rows:
            origin=r['origin'];idx=index.get(origin);bond=bonds.get(r['code'],{'code':r['code']})
            if idx is None:continue
            base=json.loads(r['payload'])
            # Historical bond metadata is frozen with the prediction, never today's option dates.
            frozen_bond=dict(bond)
            if base.get('effective_maturity_date'):frozen_bond['effective_maturity_date']=base['effective_maturity_date']
            for horizon in [3,5,10]:
                if idx+horizon>=len(data.calendar):continue
                key=(origin,r['code'],base['curve_name'],base['base_spread'],horizon)
                if key not in cache:
                    from fair_value_research.run import date
                    path=[]
                    for day in data.calendar[idx+1:idx+horizon+1]:
                        term=(date(frozen_bond['effective_maturity_date'])-date(day)).days/365 if frozen_bond.get('effective_maturity_date') else None
                        y=data.values.get(day,{}).get(r['code']);curve=data.curve(base['curve_name'],day,term)
                        path.append(((y-curve)*100,y,curve) if y is not None and curve is not None else None)
                    cache[key]=path
                path=cache[key]
                if path[-1] is None:continue
                actual=path[-1][0]-base['base_spread'];delta=r['delta'];sign=1 if delta>0 else -1
                complete=all(x is not None for x in path)
                moves=[x[0]-base['base_spread'] for x in path if x is not None]
                payload=dict(actual_delta=actual,error=abs(actual-delta),baseline_error=abs(actual),correct=int(actual*delta>0),
                    reached=int(sign*(actual-delta)>=-1),touched=int(any(sign*(v-delta)>=-1 for v in moves)) if complete else None,
                    adverse=max([0.]+[-sign*v for v in moves]) if complete else None,complete=int(complete),
                    future_yield=path[-1][1],future_curve=path[-1][2])
                encoded=dumps(payload);prior=old.get((r['id'],horizon))
                if prior:
                    if prior!=encoded:revisions.append((r['id'],horizon,timestamp,encoded))
                else:
                    inserts.append((r['id'],horizon,data.calendar[idx+horizon],timestamp,actual,payload['error'],payload['baseline_error'],payload['correct'],payload['reached'],payload['touched'],payload['adverse'],int(complete),encoded));added+=1
        db.executemany('INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',inserts)
        db.executemany('INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)',revisions)
        from .presentation import sync_points
        sync_points(db,data)
    return added


def freeze_selection(root=ROOT):
    with connect(root,True) as db:
        version=db.execute('SELECT selected_at FROM versions WHERE version=?',(rules.VERSION,)).fetchone()
        if version['selected_at']:return
        result=db.execute('''SELECT p.rule,p.direction,AVG(o.correct) AS win FROM predictions p JOIN outcomes o ON o.prediction=p.id
            WHERE p.version=? AND p.kind='historical' AND o.horizon=5 GROUP BY p.rule,p.direction''',(rules.VERSION,)).fetchall()
        if not result:raise RuntimeError('没有历史成熟5日结果，不能冻结默认条件')
        for r in result:
            db.execute('UPDATE rules SET selected=? WHERE version=? AND id=? AND direction=?',(int(r['win']>.5),rules.VERSION,r['rule'],r['direction']))
        db.execute('UPDATE predictions SET selected=COALESCE((SELECT selected FROM rules WHERE rules.version=predictions.version AND rules.id=predictions.rule AND rules.direction=predictions.direction),0) WHERE version=?',(rules.VERSION,))
        db.execute('UPDATE versions SET selected_at=? WHERE version=?',(now(),rules.VERSION))


def refresh_metrics(root=ROOT):
    # Precompute the default all-date cards; filtered views use the same query service.
    from .query import aggregate
    with connect(root,True) as db:
        for kind in ['historical','prospective','backfill']:
            for horizon in [3,5,10]:
                items=aggregate(db,rules.VERSION,kind,horizon)
                for item in items:
                    db.execute('INSERT OR REPLACE INTO metric_cache VALUES (?,?,?,?,?,?)',(rules.VERSION,kind,horizon,item['rule'],item['direction'],dumps(item)))


def initialize_history(source,root=ROOT):
    source=Path(source).resolve();install(root)
    path=source/'outputs/fair_value_widening/features.json'
    f=pd.read_json(path,convert_dates=False,dtype={'date':str,'code':str})
    universe=json.loads((source/'outputs/fair_value_v1/inputs/universe.json').read_text(encoding='utf8'))['bonds']
    f['effective_maturity_date']=f.code.map({b['code']:b.get('effective_maturity_date') for b in universe})
    manifest=dict(source=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),kind='historical')
    batch=archive_json(root,Path('initialization'),manifest)['sha256']
    print('importing frozen historical quote-only candidates',flush=True)
    record(f,'historical',batch,root)
    from fair_value_research.widening_report import ValuationInputs
    data=ValuationInputs(source/'outputs/fair_value_v1/inputs')
    evaluate(data,root);freeze_selection(root);combine(root);evaluate(data,root);refresh_metrics(root)
    print('historical initialization complete',flush=True)
