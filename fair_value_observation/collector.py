"""Incremental input refresh. Source writes stay in this module's data directory."""
import hashlib
import json
import shutil
from datetime import datetime,timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fair_value_research.collect import save,fetch_trade_task
from .storage import ROOT,archive_json,now,connect


def seed(source,root=ROOT):
    source=Path(source)/'outputs/fair_value_v1/inputs';dest=Path(root)/'inputs'
    if not (dest/'manifest.json').exists():
        dest.mkdir(parents=True,exist_ok=True)
        for name in ['universe.json','ratings.json','curves.json','calendar.json','quote_daily_v2.json']:
            if (source/name).exists():shutil.copy2(source/name,dest/name)
        for name in ['valuations','trades','quotes']:
            shutil.copytree(source/name,dest/name,dirs_exist_ok=True)
        shutil.copy2(source/'manifest.json',dest/'manifest.json')
    # Keep seed inputs separately from mutable incremental cache.
    original=Path(root)/'historical_inputs'
    if not (original/'manifest.json').exists():shutil.copytree(source,original,dirs_exist_ok=True)
    return dest


def enrich(conn,universe,day):
    from juyuan_update import oracle_bonds as ob
    bonds=universe['bonds'];symbols=[b['code'].split('.')[0] for b in bonds];raw=[]
    with conn.cursor() as cur:
        for offset in range(0,len(symbols),400):
            binds={f's{i}':s for i,s in enumerate(symbols[offset:offset+400])};marks=','.join(':'+k for k in binds)
            cur.execute(ob._candidate_sql(f'AND n.SYMBOL IN ({marks}) AND n.ISVALID=1',restrict_types=False),binds);raw.extend(cur.fetchall())
    by={ob._wind_code(r[0],r[1]):r for r in ob._attach_option_dates(conn,raw)}
    for b in bonds:
        r=by.get(b['code'])
        if not r:continue
        maturity,source=ob.effective_maturity_date(as_of=datetime.strptime(day,'%Y%m%d').date(),start_date=r[6],maturity_date=r[7],put_date=r[10],redeem_date=r[11],option_memo=str(r[8] or ''))
        b.update(secode=str(r[2]),effective_maturity_date=maturity.isoformat() if maturity else None,term_source=source,issue_company_code=str(r[19] or ''))
    return universe


def refresh(root=ROOT,origin=None,collect_trades=True):
    from juyuan_update import db,config
    from juyuan_update.unified_excel import load_bond_static
    from broker_market.storage import HISTORY_DIR
    root=Path(root);dest=root/'inputs';dest.mkdir(parents=True,exist_ok=True)
    today=datetime.fromisoformat(now()).strftime('%Y%m%d');origin=origin or today
    def load(name,default):
        p=dest/name;return json.loads(p.read_text(encoding='utf8')) if p.exists() else default
    references=[]
    def keep(name,payload):
        references.append(dict(name=name,**archive_json(root,Path('raw')/today/name.replace('/','_'),payload)));save(dest/name,payload)
    universe=load_bond_static()
    if not universe.get('bonds'):raise RuntimeError('门户债券池为空')
    manifest=load('manifest.json',{'quote_days':[]});curves=load('curves.json',{});calendar=load('calendar.json',[])
    # Re-query a short overlapping interval to detect late publications/revisions.
    start=(datetime.strptime(calendar[-12],'%Y%m%d') if len(calendar)>=12 else datetime.strptime(origin,'%Y%m%d')-timedelta(days=35)).strftime('%Y%m%d')
    if origin<start:start=(datetime.strptime(origin,'%Y%m%d')-timedelta(days=14)).strftime('%Y%m%d')
    with db.connect() as conn:
        conn.call_timeout=180000
        universe=enrich(conn,universe,origin);keep('universe.json',universe)
        meta={n:{'code':code,'type':'1'} for n,code in config.CURVE_CODE_OVERRIDES.items()}
        series=db.fetch_curve_series(conn,meta,start,today,config.SPREAD_MONITOR_TENORS)
        if not series.get('国开债'):raise RuntimeError('评级曲线/交易日数据尚不可得')
        for name,tenors in series.items():
            for term,points in tenors.items():curves.setdefault(name,{}).setdefault(term,{}).update(points)
        keep('curves.json',curves)
        calendar=sorted({d for points in curves['国开债'].values() for d in points});keep('calendar.json',calendar)
        codes={b['code'] for b in universe['bonds']}
        if (root/'observations.sqlite3').exists():
            with connect(root) as observed:codes.update(r[0] for r in observed.execute('SELECT DISTINCT code FROM predictions'))
        codes=sorted(codes)
        for day in calendar:
            if day>=start or not (dest/'valuations'/f'{day}.json').exists():
                vals=db.fetch_cnbd_yields_by_symbol(conn,codes,day)
                if vals:keep(f'valuations/{day}.json',vals)
        ratings=load('ratings.json',{});secodes=list({b['secode'] for b in universe['bonds'] if b.get('secode')})
        with conn.cursor() as cur:
            for offset in range(0,len(secodes),400):
                batch=secodes[offset:offset+400];binds={f's{i}':s for i,s in enumerate(batch)};marks=','.join(':'+k for k in binds);binds['end_day']=today
                cur.execute(f"SELECT SECODE,STDCREDIT,HIDECREDITDATE FROM TQ_BD_NEWHIDECREDIT WHERE SECODE IN ({marks}) AND CREDITSOURCE='1' AND ISVALID=1 AND STDCREDIT IS NOT NULL AND HIDECREDITDATE<=:end_day",binds)
                fresh={s:[] for s in batch}
                for sec,rating,day in cur:fresh[str(sec)].append([db.yyyymmdd(day),str(rating)])
                ratings.update(fresh)
        keep('ratings.json',ratings)
    # On-current-day calendar absence means pending, never invent a trading day.
    available=[d for d in calendar if d<=origin]
    needed=available[-6:];qdays=set(manifest.get('quote_days',[]));changed=False
    for day in needed:
        files=[p for p in HISTORY_DIR.glob(day+'_??????.json') if p.name[9:15]<='160500']
        if not files:continue
        quotes=[];sources=[]
        for p in sorted(files):
            raw=p.read_bytes();obj=json.loads(raw);stamp=obj.get('generated_at','')
            if stamp.replace('-','')[:8]!=day:continue
            quotes.extend({**q,'observed_at':stamp} for q in obj.get('quotes',[]))
            sources.append({'name':p.name,'sha256':hashlib.sha256(raw).hexdigest()})
        if quotes:
            keep(f'quotes/{day}.json',quotes);references.append({'quote_day':day,'sources':sources});qdays.add(day);changed=True
    manifest.update(quote_days=sorted(qdays),refreshed_at=now());keep('manifest.json',manifest)
    if changed and (dest/'quote_daily_v2.json').exists():(dest/'quote_daily_v2.json').unlink()
    if collect_trades:
        # Only quote-evidenced bonds can enter any live rule. Fetch missing days once in 5-code batches.
        quote_codes=set()
        for day in needed:
            for q in load(f'quotes/{day}.json',[]):quote_codes.add(q['code'])
        quote_codes.intersection_update(b['code'] for b in universe['bonds'])
        trade_days=[d for d in needed if d<origin]
        covered={d:set() for d in trade_days}
        for path in (dest/'trades').glob('chunk5_*.json'):
            item=json.loads(path.read_text(encoding='utf8'))
            if item.get('status')!='ok':continue
            for row in item.get('rows',[]):
                day=str(row.get('issue_date','')).replace('-','')[:8]
                code=str(row.get('security_id') or row.get('bond_code') or '')
                if day in covered:covered[day].add(code.split('.')[0])
        for day in trade_days:
            codes=sorted(c for c in quote_codes if c.split('.')[0] not in covered[day]);tasks=[]
            for i in range(0,len(codes),5):
                batch=codes[i:i+5];tag=hashlib.sha256('|'.join(batch).encode()).hexdigest()[:16]
                path=dest/'trades'/f'chunk5_live_{day}_{tag}.json';tasks.append((str(path),batch,day,day))
            with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(fetch_trade_task,tasks))
            for path,_,_,_ in tasks:
                payload=json.loads(Path(path).read_text(encoding='utf8'));references.append(dict(name=Path(path).name,**archive_json(root,Path('raw')/today/'trades',payload)))
    report=dict(created_at=now(),origin=origin,calendar_verified=origin in calendar,inputs=references)
    ref=archive_json(root,Path('batches')/origin,report)
    return dest,ref['sha256'],origin in calendar
