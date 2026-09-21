import json
from .rules import VERSION
from .storage import ROOT,connect


def filters(version,kind,origin=None,direction=None,search=None):
    where=['p.version=?','p.kind=?'];args=[version,kind]
    if origin:where.append('p.origin=?');args.append(origin)
    if direction in {'widen','tighten','neutral'}:where.append('p.direction=?');args.append(direction)
    if search:
        where.append("(p.code LIKE ? OR json_extract(b.payload,'$.name') LIKE ? OR json_extract(b.payload,'$.issuer') LIKE ?)");args.extend(['%'+search+'%']*3)
    return ' AND '.join(where),args


JOIN=' FROM predictions p JOIN bonds b ON b.version=p.version AND b.origin=p.origin AND b.code=p.code '


def aggregate(db,version=VERSION,kind='prospective',horizon=5,origin=None,direction=None,search=None):
    where,args=filters(version,kind,origin,direction,search)
    sql='''SELECT p.rule,p.direction,COUNT(*) AS total,COUNT(o.prediction) AS mature,
      SUM(o.correct) AS wins,AVG(o.correct) AS win_rate,AVG(o.error) AS mae_bp,AVG(o.baseline_error) AS baseline_mae_bp,
      AVG(o.reached) AS reached,AVG(o.touched) AS touched,AVG(o.adverse) AS adverse,
      COUNT(DISTINCT CASE WHEN o.prediction IS NOT NULL THEN p.origin END) AS origins,
      COUNT(DISTINCT CASE WHEN o.prediction IS NOT NULL THEN json_extract(b.payload,'$.issuer') END) AS issuers
    '''+JOIN+'''LEFT JOIN outcomes o ON o.prediction=p.id AND o.horizon=? WHERE '''+where+' GROUP BY p.rule,p.direction'
    rows=[dict(r) for r in db.execute(sql,[horizon,*args])]
    eligible=db.execute("SELECT COUNT(*) FROM bonds b WHERE b.version=? AND b.kind=?"+(' AND b.origin=?' if origin else ''),[version,kind]+([origin] if origin else [])).fetchone()[0]
    for r in rows:
        r['pending']=r['total']-r['mature'];r['insufficient']=r['mature']<100 or r['origins']<20
        r['coverage']=r['total']/eligible if eligible else None
    return rows


def statistics(db,options):
    if not options.get('origin') and not options.get('search'):
        cached=[json.loads(r[0]) for r in db.execute('SELECT payload FROM metric_cache WHERE version=? AND kind=? AND horizon=?',(options['version'],options['kind'],options['horizon']))]
        if cached:return [r for r in cached if not options.get('direction') or r['direction']==options['direction']]
    args={k:options.get(k) for k in ['version','kind','horizon','origin','direction','search']}
    return aggregate(db,**args)


def overview(options,root=ROOT):
    with connect(root) as db:
        v=options['version']
        versions=[dict(r) for r in db.execute('SELECT version,created,selected_at FROM versions ORDER BY created DESC')]
        dates=[r[0] for r in db.execute('SELECT DISTINCT origin FROM checkpoints WHERE version=? AND kind=? ORDER BY origin DESC',(v,options['kind']))]
        jobs=[dict(r) for r in db.execute('SELECT * FROM jobs')]
        metrics=statistics(db,options)
        comparison={}
        for kind in ['historical','prospective','backfill']:
            opts=dict(options,kind=kind)
            comparison[kind]=[r for r in statistics(db,opts) if r['rule']=='merged']
        latest=db.execute('SELECT origin,kind,batch FROM checkpoints WHERE version=? ORDER BY origin DESC LIMIT 1',(v,)).fetchone()
        return dict(versions=versions,dates=dates,jobs=jobs,latest=dict(latest) if latest else None,metrics=[r for r in metrics if r['rule']=='merged'],comparison=comparison,
            selection_frozen=True,warning='历史探索胜率用于筛选，不代表独立验证；实际不变计未胜，未成熟不进分母。',timezone='Asia/Shanghai')


def conditions(options,root=ROOT):
    with connect(root) as db:
        stats={(r['rule'],r['direction']):r for r in statistics(db,options)}
        historical={(r['rule'],r['direction']):r for r in statistics(db,dict(options,kind='historical'))}
        out=[]
        for r in db.execute('SELECT * FROM rules WHERE version=? ORDER BY selected DESC,family,name,direction',(options['version'],)):
            r=dict(r)
            if options.get('direction') and r['direction']!=options['direction']:continue
            key=(r['id'],r['direction']);r['spec']=json.loads(r['spec']);r['sources']=json.loads(r['sources'])
            r['metrics']=stats.get(key,dict(total=0,mature=0,pending=0,win_rate=None,insufficient=True))
            r['historical']=historical.get(key,dict(total=0,mature=0,win_rate=None,insufficient=True))
            r['fallen_below']=bool(r['selected'] and r['metrics'].get('win_rate') is not None and r['metrics']['win_rate']<=.5)
            out.append(r)
        version=db.execute('SELECT manifest FROM versions WHERE version=?',(options['version'],)).fetchone()
        return dict(items=out,archived=json.loads(version['manifest'])['archived'] if version else [])


def bond_list(options,root=ROOT,export=False):
    with connect(root) as db:
        origin=options.get('origin')
        if not origin:
            row=db.execute('SELECT MAX(origin) FROM checkpoints WHERE version=? AND kind=?',(options['version'],options['kind'])).fetchone();origin=row[0]
        opts=dict(options,origin=origin);where,args=filters(opts['version'],opts['kind'],origin,opts.get('direction'),opts.get('search'))
        rule=opts.get('rule') or 'merged';where+=' AND p.rule=?';args.append(rule)
        total=db.execute('SELECT COUNT(*)'+JOIN+'WHERE '+where,args).fetchone()[0]
        sql='SELECT p.*,b.payload,o.actual_delta,o.correct,o.target_date,o.error,o.reached,o.touched,o.adverse'+JOIN+' LEFT JOIN outcomes o ON o.prediction=p.id AND o.horizon=? WHERE '+where+' ORDER BY ABS(p.delta) DESC,p.code'
        values=[opts['horizon'],*args]
        if not export:sql+=' LIMIT ? OFFSET ?';values.extend([opts['page_size'],(opts['page']-1)*opts['page_size']])
        rows=[]
        for raw in db.execute(sql,values):
            r=dict(raw);payload=json.loads(r.pop('payload'));r.update(payload);r['members']=json.loads(r['members'])
            active=r['direction']!='neutral';r['fair_yield']=r['official_yield']+r['delta']/100 if active else None;r['fair_spread']=r['base_spread']+r['delta'] if active else None
            r['signal_status']='条件分歧' if r['conflict'] else '信号不足' if not active else '观察信号'
            rows.append(r)
        return dict(items=rows,total=total,origin=origin,page=opts['page'],page_size=opts['page_size'])


def bond_history(code,options,root=ROOT):
    with connect(root) as db:
        where,args=filters(options['version'],options['kind'],options.get('origin'),options.get('direction'))
        rows=[dict(r) for r in db.execute('''SELECT p.origin,p.rule,p.delta,p.direction,p.created,p.kind,p.batch,p.conflict,
          o.horizon,o.target_date,o.actual_delta,o.correct,o.error,o.baseline_error,o.reached,o.touched,o.adverse
          FROM predictions p LEFT JOIN outcomes o ON o.prediction=p.id AND o.horizon=? WHERE '''+where+' AND p.code=? ORDER BY p.origin DESC,p.rule',[options['horizon'],*args,code])]
        daily=[dict(r) for r in db.execute('''SELECT p.origin,COUNT(o.prediction) AS mature,AVG(o.correct) AS win_rate,
          AVG(o.error) AS mae,AVG(o.baseline_error) AS baseline_mae FROM predictions p LEFT JOIN outcomes o ON o.prediction=p.id AND o.horizon=?
          WHERE p.version=? AND p.kind=? AND p.code=? AND p.rule='merged' GROUP BY p.origin ORDER BY p.origin''',(options['horizon'],options['version'],options['kind'],code))]
        return dict(code=code,items=rows,daily=daily)


def daily_metrics(rule,options,root=ROOT):
    with connect(root) as db:
        where,args=filters(options['version'],options['kind'],options.get('origin'),options.get('direction'),options.get('search'))
        rows=[dict(r) for r in db.execute('''SELECT p.origin,p.direction,COUNT(*) AS total,COUNT(o.prediction) AS mature,AVG(o.correct) AS win_rate,
           AVG(o.error) AS mae_bp,AVG(o.baseline_error) AS baseline_mae_bp '''+JOIN+'''LEFT JOIN outcomes o ON o.prediction=p.id AND o.horizon=? WHERE '''+where+' AND p.rule=? GROUP BY p.origin,p.direction ORDER BY p.origin',[options['horizon'],*args,rule])]
        # Equal group diagnostics on identical mature samples.
        equal={}
        for key in ['issuer','family']:
            sql='SELECT AVG(win) AS win_rate,AVG(gain) AS improvement_bp FROM (SELECT AVG(o.correct) win,AVG(o.baseline_error-o.error) gain'+JOIN+' JOIN outcomes o ON o.prediction=p.id AND o.horizon=? WHERE '+where+f" AND p.rule=? GROUP BY p.origin,json_extract(b.payload,'$.{key}'))"
            equal[key]=dict(db.execute(sql,[options['horizon'],*args,rule]).fetchone())
        return dict(items=rows,equal_weight=equal)
