import argparse
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from .storage import ROOT,dumps


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['init','predict','settle','backfill','migrate']);p.add_argument('--root',default=str(ROOT));p.add_argument('--source',default=str(Path.cwd()));p.add_argument('--date')
    a=p.parse_args();root=Path(a.root).resolve()
    from broker_market.scheduler import SchedulerLock
    root.mkdir(parents=True,exist_ok=True);lock=SchedulerLock(root/'scheduler.lock')
    if not lock.acquire():raise RuntimeError('调度器正在运行，不能并发修改观测数据；请使用管理员重试入口')
    if a.command=='migrate':
        from .presentation import migrate
        migrate(root)
        print('User-view attributes, condition snapshots and daily points migrated.')
    elif a.command=='init':
        from .engine import initialize_history
        from .collector import seed
        initialize_history(a.source,root);seed(a.source,root)
    else:
        from .service import run
        if a.command=='backfill' and not a.date:p.error('backfill requires --date')
        print(dumps(run('settle' if a.command=='settle' else 'predict',root,a.date)))


if __name__=='__main__':main()
