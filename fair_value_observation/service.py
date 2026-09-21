from datetime import datetime
from pathlib import Path
from .storage import ROOT,archive_json,connect,now
from . import engine,rules


def run(kind='predict',root=ROOT,origin=None):
    from .collector import refresh
    from fair_value_research.run import Inputs
    from .features import build
    engine.install(root)
    with connect(root) as db:
        v=db.execute('SELECT selected_at FROM versions WHERE version=?',(rules.VERSION,)).fetchone()
        if not v or not v['selected_at']:raise RuntimeError('请先初始化历史并冻结默认条件')
    timestamp=datetime.fromisoformat(now());today=timestamp.strftime('%Y%m%d');origin=origin or today
    if kind=='predict' and origin==today and timestamp.strftime('%H:%M')<'16:05':
        return dict(state='waiting',message='尚未到16:05输入截止时间，不生成当日冻结预测')
    if kind=='predict':
        from broker_market.storage import HISTORY_DIR
        if not any(p.name[9:15]<='160500' for p in HISTORY_DIR.glob(origin+'_??????.json')):
            return dict(state='waiting',message='该日16:05前没有已留存报价快照，不能用后来抓取的数据补造当时预测')
    inputs,batch,verified=refresh(root,origin,collect_trades=kind=='predict')
    data=Inputs(inputs);outcomes=engine.evaluate(data,root)
    if kind=='settle':engine.refresh_metrics(root);return dict(state='success',outcomes=outcomes)
    if not verified:return dict(state='waiting',message='该日交易日数据尚未确认；不推定休市或虚构交易日',outcomes=outcomes)
    frame=build(data,origin)
    if frame.empty:return dict(state='waiting',message='缺少可用本券报价或官方/曲线基准')
    reference=archive_json(root,Path('features')/origin,frame.to_dict('records'))
    category='prospective' if origin==today else 'backfill'
    # Binding records reference both the immutable raw input manifest and feature snapshot.
    binding=archive_json(root,Path('forecast_batches')/origin,dict(raw_batch=batch,features=reference,created=now(),kind=category))
    count=engine.record(frame,category,binding['sha256'],root);engine.combine(root);engine.evaluate(data,root);engine.refresh_metrics(root)
    return dict(state='success',origin=origin,kind=category,predictions=count,outcomes=outcomes,batch=binding['sha256'])
