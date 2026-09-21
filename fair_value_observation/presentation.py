"""User-facing date, bond, condition-rate and chart queries; no upstream reads."""
import json
import math
from pathlib import Path
from .storage import ROOT,connect,dumps,now,initialize
from .rules import GATE_NAMES


def brief(rule):
    spec=json.loads(rule['spec']);gate=GATE_NAMES.get(spec.get('gate'),spec.get('gate','报价偏离'))
    strength='温和' if spec.get('scale',1)<=.25 else '适中' if spec.get('scale',1)<=.5 else '较强'
    return gate+'（'+strength+'调整）'


def freeze_condition_stats(db):
    names={(r['version'],r['id'],r['direction']):brief(r) for r in db.execute('SELECT * FROM rules')}
    pending=db.execute("SELECT p.* FROM predictions p WHERE p.rule='merged' AND (SELECT COUNT(*) FROM condition_snapshots s WHERE s.prediction=p.id)<3").fetchall()
    cache={}
    for p in pending:
        historic=p['kind']=='historical';mode='retrospective' if historic else 'as_of_prediction'
        for horizon in [3,5,10]:
            key=(p['version'],horizon,'historical' if historic else p['created'],None if historic else p['origin'])
            if key not in cache:
                sql='''SELECT q.rule,q.direction,COUNT(*) n,SUM(o.correct) wins FROM predictions q JOIN outcomes o ON o.prediction=q.id
                     WHERE q.version=? AND q.rule!='merged' AND o.horizon=?''';args=[p['version'],horizon]
                if historic:sql+=" AND q.kind='historical'"
                else:sql+=" AND q.kind!='backfill' AND o.observed<=? AND q.created<=? AND o.target_date<?";args.extend([p['created'],p['created'],p['origin']])
                sql+=' GROUP BY q.rule,q.direction'
                cache[key]={(r['rule'],r['direction']):(r['n'],r['wins']) for r in db.execute(sql,args)}
            rows=[]
            for m in json.loads(p['members']):
                n,wins=cache[key].get((m['rule'],m['direction']),(0,0))
                rows.append(dict(name=names.get((p['version'],m['rule'],m['direction']),'报价判断'),direction=m['direction'],delta=m['delta'],n=n,wins=wins,rate=wins/n if n else None))
            db.execute('INSERT OR IGNORE INTO condition_snapshots VALUES (?,?,?,?,?)',(p['id'],horizon,now(),mode,dumps(rows)))


def sync_points(db,data):
    from fair_value_research.run import date
    indices={d:i for i,d in enumerate(data.calendar)};timestamp=now()
    existing={(r['prediction'],r['step']):dict(r) for r in db.execute('SELECT * FROM daily_points')}
    for p in db.execute("SELECT p.id,p.origin,b.payload FROM predictions p JOIN bonds b ON b.version=p.version AND b.origin=p.origin AND b.code=p.code WHERE p.rule='merged'").fetchall():
        idx=indices.get(p['origin']);b=json.loads(p['payload'])
        if idx is None:continue
        for step,day in enumerate(data.calendar[idx+1:idx+11],1):
            y=data.values.get(day,{}).get(b['code']);maturity=b.get('effective_maturity_date')
            term=(date(maturity)-date(day)).days/365 if maturity else None;curve=data.curve(b.get('curve_name'),day,term)
            spread=(y-curve)*100 if y is not None and curve is not None else None
            key=(p['id'],step);old=existing.get(key)
            if old:
                if old['yield']!=y or old['spread']!=spread:
                    db.execute('INSERT OR IGNORE INTO daily_revisions VALUES (?,?,?,?)',(p['id'],step,timestamp,dumps(dict(day=day,yield_value=y,spread=spread))))
                db.execute('UPDATE daily_points SET yield=COALESCE(yield,?),spread=COALESCE(spread,?) WHERE prediction=? AND step=?',(y,spread,p['id'],step))
            else:db.execute('INSERT INTO daily_points VALUES (?,?,?,?,?,?)',(p['id'],step,day,y,spread,timestamp))


def migrate(root=ROOT):
    initialize(root)
    source=Path(root)/'historical_inputs/universe.json'
    if not source.exists():raise RuntimeError('缺少原始历史债券属性，不能使用当前属性倒填')
    universe={b['code']:b for b in json.loads(source.read_text(encoding='utf8'))['bonds']}
    with connect(root,True) as db:
        for r in db.execute("SELECT rowid,code,payload FROM bonds WHERE kind='historical'").fetchall():
            b=json.loads(r['payload']);old=universe.get(r['code'],{})
            for field in ['ct','entity','internal_rating']:b.setdefault(field,old.get(field))
            b['attribute_source']='原始研究债券池快照'
            db.execute('UPDATE bonds SET payload=? WHERE rowid=?',(dumps(b),r['rowid']))
        freeze_condition_stats(db)
        from fair_value_research.widening_report import ValuationInputs
        sync_points(db,ValuationInputs(Path(root)/'historical_inputs'))


def dates(options,root=ROOT):
    with connect(root) as db:
        rows=[dict(r) for r in db.execute("SELECT p.origin,p.version,p.kind,COUNT(*) count FROM predictions p WHERE p.rule='merged' AND p.direction!='neutral' GROUP BY p.origin,p.version,p.kind ORDER BY p.origin DESC,p.created DESC")]
    return dict(items=rows,latest=rows[0] if rows else None)


def selected_date(db,options):
    origin=options.get('origin');version=options.get('version')
    sql="SELECT origin,version,kind FROM predictions WHERE rule='merged' AND direction!='neutral'";args=[]
    if origin:sql+=' AND origin=?';args.append(origin)
    if version and options.get('explicit_version'):sql+=' AND version=?';args.append(version)
    row=db.execute(sql+' ORDER BY origin DESC,created DESC LIMIT 1',args).fetchone()
    return dict(row) if row else None


def where_for(meta,options):
    clauses=["p.rule='merged'","p.direction!='neutral'",'p.origin=?','p.version=?','p.kind=?'];args=[meta['origin'],meta['version'],meta['kind']]
    if options.get('direction'):clauses.append('p.direction=?');args.append(options['direction'])
    if options.get('search'):
        clauses.append("(p.code LIKE ? OR json_extract(b.payload,'$.name') LIKE ? OR json_extract(b.payload,'$.issuer') LIKE ?)");args.extend(['%'+options['search']+'%']*3)
    for field in ['rating','internal_rating','entity','ct','sub']:
        values=options.get(field,[])
        if values:
            clauses.append(f"COALESCE(NULLIF(json_extract(b.payload,'$.{field}'),''),'未知') IN ({','.join('?' for _ in values)})");args.extend(values)
    market=options.get('market')
    if market=='银行间':clauses.append("p.code LIKE '%.IB'")
    if market=='交易所':clauses.append("p.code NOT LIKE '%.IB'")
    for param,field,op in [('min_term','term','>='),('max_term','term','<='),('min_yield','official_yield','>='),('max_yield','official_yield','<=')]:
        if options.get(param) is not None:clauses.append(f"json_extract(b.payload,'$.{field}') {op} ?");args.append(options[param])
    return ' AND '.join(clauses),args


def facets(options,root=ROOT):
    with connect(root) as db:
        meta=selected_date(db,options)
        if not meta:return dict(meta=None,fields={})
        fields={}
        for field in ['rating','internal_rating','entity','ct','sub']:
            fields[field]=[r[0] for r in db.execute(f"SELECT DISTINCT COALESCE(NULLIF(json_extract(payload,'$.{field}'),''),'未知') FROM bonds WHERE version=? AND origin=? ORDER BY 1",(meta['version'],meta['origin']))]
        return dict(meta=meta,fields=fields)


JOIN=' FROM predictions p JOIN bonds b ON b.version=p.version AND b.origin=p.origin AND b.code=p.code '


def decorate(r):
    r=dict(r);r.update(json.loads(r.pop('payload')));r['fair_yield']=r['official_yield']+r['delta']/100;r['fair_spread']=r['base_spread']+r['delta']
    r['judgment']='估值收益率偏高' if r['delta']<0 else '估值收益率偏低'
    r['conditions']=json.loads(r.pop('condition_payload') or '[]');rates=[x['rate'] for x in r['conditions'] if x['rate'] is not None]
    r['rates']=dict(min=min(rates) if rates else None,max=max(rates) if rates else None,count=len(r['conditions']),known=len(rates))
    r['performance']='待兑现' if r['actual_delta'] is None else '无变化' if abs(r['actual_delta'])<1e-9 else '方向正确' if r['actual_delta']*r['delta']>0 else '方向相反'
    return r


def listings(options,root=ROOT,export=False):
    with connect(root) as db:
        meta=selected_date(db,options)
        if not meta:return dict(meta=None,items=[],total=0)
        where,args=where_for(meta,options)
        total=db.execute('SELECT COUNT(*)'+JOIN+'WHERE '+where,args).fetchone()[0]
        sql='SELECT p.*,b.payload,o.actual_delta,o.correct,o.target_date,s.payload condition_payload,s.mode stats_mode'+JOIN+'LEFT JOIN outcomes o ON o.prediction=p.id AND o.horizon=? LEFT JOIN condition_snapshots s ON s.prediction=p.id AND s.horizon=? WHERE '+where
        sort={'adjustment':'ABS(p.delta)','term':"json_extract(b.payload,'$.term')",'yield':"json_extract(b.payload,'$.official_yield')"}[options.get('sort','adjustment')]
        order='ASC' if options.get('order')=='asc' else 'DESC';sql+=' ORDER BY '+sort+' '+order+',p.code'
        values=[options['horizon'],options['horizon'],*args]
        if not export:sql+=' LIMIT ? OFFSET ?';values.extend([options['page_size'],(options['page']-1)*options['page_size']])
        return dict(meta=meta,total=total,items=[decorate(r) for r in db.execute(sql,values)])


def chart(prediction,options,root=ROOT):
    with connect(root) as db:
        r=db.execute('SELECT p.*,b.payload,o.actual_delta,o.target_date,s.payload condition_payload,s.mode stats_mode'+JOIN+"LEFT JOIN outcomes o ON o.prediction=p.id AND o.horizon=? LEFT JOIN condition_snapshots s ON s.prediction=p.id AND s.horizon=? WHERE p.id=? AND p.rule='merged'",(options['horizon'],options['horizon'],prediction)).fetchone()
        if not r:raise ValueError('找不到预测')
        item=decorate(r);points=[dict(step=0,day=item['origin'],yield_value=item['official_yield'],spread=item['base_spread'])]
        points.extend(dict(step=r['step'],day=r['day'],yield_value=r['yield'],spread=r['spread']) for r in db.execute('SELECT * FROM daily_points WHERE prediction=? AND step<=? ORDER BY step',(prediction,options['horizon'])))
        # The date of an unconfirmed future trading session is intentionally unknown.
        last_step=max(p['step'] for p in points)
        points.extend(dict(step=i,day=None,yield_value=None,spread=None) for i in range(last_step+1,options['horizon']+1))
        return dict(item=item,points=points,horizon=options['horizon'])
