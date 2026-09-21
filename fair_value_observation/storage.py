import hashlib
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from paths import DATA_DIR

ROOT=DATA_DIR/'fair_value_observation'


def now():return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if hasattr(x,'item'):return clean(x.item())
    if isinstance(x,float) and not math.isfinite(x):return None
    return x


def dumps(x):return json.dumps(clean(x),ensure_ascii=False,separators=(',',':'),allow_nan=False)


@contextmanager
def connect(root=ROOT,write=False):
    path=Path(root).resolve()/'observations.sqlite3'
    if write:
        path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(path,timeout=60)
    else:db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)
    db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA busy_timeout=60000')
        yield db
        if write:db.commit()
    except Exception:
        if write:db.rollback()
        raise
    finally:db.close()


def initialize(root=ROOT):
    with connect(root,True) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS versions(version TEXT PRIMARY KEY,created TEXT,selected_at TEXT,manifest TEXT);
        CREATE TABLE IF NOT EXISTS rules(version TEXT,id TEXT,direction TEXT,family TEXT,name TEXT,spec TEXT,sources TEXT,selected INTEGER DEFAULT 0,PRIMARY KEY(version,id,direction));
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,origin TEXT,kind TEXT,created TEXT,manifest TEXT);
        CREATE TABLE IF NOT EXISTS bonds(version TEXT,origin TEXT,code TEXT,kind TEXT,batch TEXT,payload TEXT,PRIMARY KEY(version,origin,code));
        CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY,version TEXT,origin TEXT,code TEXT,rule TEXT,direction TEXT,kind TEXT,batch TEXT,created TEXT,delta REAL,selected INTEGER,conflict INTEGER DEFAULT 0,members TEXT,UNIQUE(version,origin,code,rule));
        CREATE INDEX IF NOT EXISTS pred_filter ON predictions(version,kind,rule,origin,direction);
        CREATE TABLE IF NOT EXISTS outcomes(prediction INTEGER,horizon INTEGER,target_date TEXT,observed TEXT,actual_delta REAL,error REAL,baseline_error REAL,correct INTEGER,reached INTEGER,touched INTEGER,adverse REAL,complete INTEGER,payload TEXT,PRIMARY KEY(prediction,horizon));
        CREATE TABLE IF NOT EXISTS revisions(prediction INTEGER,horizon INTEGER,observed TEXT,payload TEXT,UNIQUE(prediction,horizon,payload));
        CREATE TABLE IF NOT EXISTS jobs(kind TEXT PRIMARY KEY,state TEXT,scheduled TEXT,started TEXT,finished TEXT,error TEXT,result TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints(version TEXT,origin TEXT,kind TEXT,batch TEXT,PRIMARY KEY(version,origin));
        CREATE TABLE IF NOT EXISTS metric_cache(version TEXT,kind TEXT,horizon INTEGER,rule TEXT,direction TEXT,payload TEXT,PRIMARY KEY(version,kind,horizon,rule,direction));
        CREATE TABLE IF NOT EXISTS condition_snapshots(prediction INTEGER,horizon INTEGER,created TEXT,mode TEXT,payload TEXT,PRIMARY KEY(prediction,horizon));
        CREATE TABLE IF NOT EXISTS daily_points(prediction INTEGER,step INTEGER,day TEXT,yield REAL,spread REAL,observed TEXT,PRIMARY KEY(prediction,step));
        CREATE TABLE IF NOT EXISTS daily_revisions(prediction INTEGER,step INTEGER,observed TEXT,payload TEXT,UNIQUE(prediction,step,payload));
        ''')


def archive_json(root,relative,payload):
    raw=dumps(payload).encode('utf8');digest=hashlib.sha256(raw).hexdigest()
    path=Path(root)/'archive'/relative/f'{digest}.json';path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        tmp=path.with_suffix('.tmp');tmp.write_bytes(raw);os.replace(tmp,path)
    return dict(path=str(path.relative_to(root)),sha256=digest)


def job(root,kind,**values):
    with connect(root,True) as db:
        db.execute('INSERT OR IGNORE INTO jobs(kind,state) VALUES (?,?)',(kind,'idle'))
        for k,v in values.items():
            if k not in {'state','scheduled','started','finished','error','result'}:raise ValueError(k)
            db.execute(f'UPDATE jobs SET {k}=? WHERE kind=?',(v,kind))
