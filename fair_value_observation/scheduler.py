"""Independent single-instance worker alongside the portal's broker scheduler."""
import os
import threading
from datetime import datetime,timedelta
from pathlib import Path
from .storage import ROOT,connect,initialize,job,now,dumps
from broker_market.scheduler import SchedulerLock

_scheduler=None


class ObservationScheduler:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.stop=threading.Event();self.busy=threading.Lock();self.lock=SchedulerLock(self.root/'scheduler.lock')
    def start(self):
        initialize(self.root)
        if not self.lock.acquire():return False
        with connect(self.root,True) as db:
            db.execute("UPDATE jobs SET state='failed',error='上次进程已结束，可重新执行',finished=NULL WHERE state IN ('running','retrying')")
        threading.Thread(target=self.loop,name='fair-value-observation',daemon=True).start();return True
    def loop(self):
        while not self.stop.is_set():
            try:self.tick()
            except Exception as exc:job(self.root,'scheduler',state='failed',error=type(exc).__name__,finished=now())
            self.stop.wait(60)
    def tick(self):
        stamp=datetime.fromisoformat(now());day=stamp.strftime('%Y%m%d')
        if stamp.weekday()>=5:return
        with connect(self.root) as db:status={r['kind']:dict(r) for r in db.execute('SELECT * FROM jobs')}
        for kind,cutoff in [('settle','08:30'),('predict','16:10')]:
            if stamp.strftime('%H:%M')<cutoff:continue
            old=status.get(kind,{})
            if old.get('scheduled')==day and old.get('state')=='success':continue
            if old.get('finished') and stamp-datetime.fromisoformat(old['finished'])<timedelta(minutes=5):continue
            self.execute(kind,day)
    def execute(self,kind,day=None):
        if not self.busy.acquire(blocking=False):return
        day=day or datetime.fromisoformat(now()).strftime('%Y%m%d')
        try:
            job(self.root,kind,state='running',scheduled=day,started=now(),error='')
            from .service import run
            result=run(kind,self.root)
            job(self.root,kind,state=result['state'],finished=now(),result=dumps(result))
        except Exception as exc:
            # Do not expose connection strings or credentials from upstream exceptions.
            job(self.root,kind,state='failed',finished=now(),error=type(exc).__name__+'：采集或计算失败，可重试；详细输入状态见归档')
        finally:self.busy.release()
    def trigger(self,kind):
        if kind not in {'predict','settle'}:return False,'未知任务'
        if self.busy.locked():return False,'已有任务运行中'
        threading.Thread(target=self.execute,args=(kind,),daemon=True).start();return True,'任务已提交'


def start():
    global _scheduler
    if os.environ.get('FAIR_VALUE_OBSERVATION_ENABLED','1')!='1':return False
    if _scheduler is not None:return True
    worker=ObservationScheduler()
    if worker.start():_scheduler=worker;return True
    return False


def trigger(kind):
    return _scheduler.trigger(kind) if _scheduler else (False,'本进程未持有调度锁，请等待生产调度或使用命令行重试')
