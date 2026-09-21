import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from functools import wraps
import pandas as pd
from flask import Flask,session,redirect
from fair_value_observation import engine,query,rules
from fair_value_observation.storage import connect
from fair_value_observation.routes import register


RULE=dict(id='test',family='test',name='test',sources=['test'],spec=dict(engine='persistent',anchor='level',gate='persistent',scale=.25,minimum=1.))


def frame(delta=8.,day='20260904'):
    return pd.DataFrame([dict(date=day,code='B.IB',name='测试债',issuer='发行人',family='family',sub='',guarantor='',
        effective_maturity_date='20280904',official_yield=2.,base_spread=50.,curve_yield=1.5,curve_name='curve',valuation_date='20260903',
        quote_days=5,trade_days=0,trade_status='ok',q_delta=delta,quote_level_only=delta,level_delta=100.,any_quote_age=0,
        any_freshness=1.,boundary_consistent_days=4,any_boundary=delta,known_relevant_volume_days=0,last_trade_age=None,t_delta=None)])


class FakeData:
    calendar=['20260903','20260904','20260907','20260908','20260909','20260910','20260911']
    bonds=[dict(code='B.IB',effective_maturity_date='20280904')]
    values={d:{'B.IB':2.01} for d in calendar}
    def curve(self,*a):return 1.5


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.reg=patch.object(rules,'registry',return_value=([RULE],[]));self.reg.start();engine.install(self.root)
    def tearDown(self):self.reg.stop();self.tmp.cleanup()
    def test_quote_only_no_indirect_trade_weight(self):
        f=frame();d,m=rules.apply(f,RULE['spec']);f['level_delta']=1000.;f['t_delta']=100.
        d2,m2=rules.apply(f,RULE['spec']);self.assertTrue(d.equals(d2));self.assertEqual(d.iloc[0],2.)
    def test_user_list_filters_and_daily_chart_first_observation(self):
        from fair_value_observation import presentation
        f=frame();f['term']=2.;f['rating']='AA';f['ct']='否'
        engine.record(f,'historical','one',self.root)
        with connect(self.root,True) as db:db.execute('UPDATE rules SET selected=1')
        engine.combine(self.root)
        data=FakeData();data.values={d:dict(v) for d,v in data.values.items()};data.values['20260907']={}
        engine.evaluate(data,self.root)
        opts=self.options();rows=presentation.listings(opts,self.root)
        self.assertEqual(rows['total'],1);r=rows['items'][0]
        self.assertEqual(r['judgment'],'估值收益率偏低');self.assertEqual(r['performance'],'方向正确')
        self.assertEqual(presentation.listings(dict(opts,min_term=3),self.root)['total'],0)
        self.assertEqual(presentation.listings(dict(opts,internal_rating=['未知'],ct=['否']),self.root)['total'],1)
        chart=presentation.chart(r['id'],dict(opts,horizon=10),self.root)
        self.assertIsNone(chart['points'][1]['spread']);self.assertIsNone(chart['points'][-1]['day'])
        self.assertEqual(chart['item']['fair_spread'],52.)
        revised=FakeData();revised.values={d:{'B.IB':1.9} for d in revised.calendar};engine.evaluate(revised,self.root)
        again=presentation.chart(r['id'],opts,self.root)
        self.assertAlmostEqual(again['points'][2]['yield_value'],2.01)
        self.assertAlmostEqual(again['points'][1]['yield_value'],1.9)
        with connect(self.root) as db:self.assertGreater(db.execute('SELECT COUNT(*) FROM daily_revisions').fetchone()[0],0)
    def test_rate_snapshot_excludes_later_results_and_is_immutable(self):
        from fair_value_observation import presentation
        engine.record(frame(),'historical','one',self.root);engine.evaluate(FakeData(),self.root)
        with connect(self.root,True) as db:
            db.execute('UPDATE rules SET selected=1')
            db.execute("UPDATE predictions SET created='2026-09-04T16:10:00+08:00'")
            db.execute("UPDATE outcomes SET observed='2026-09-11T18:00:00+08:00'")
        with patch.object(engine,'now',return_value='2026-09-11T16:10:00+08:00'):
            engine.record(frame(day='20260911'),'prospective','two',self.root)
        engine.combine(self.root)
        row=presentation.listings(dict(self.options(),origin='20260911'),self.root)['items'][0]
        self.assertEqual(row['rates']['known'],0);self.assertIsNone(row['conditions'][0]['rate'])
        with connect(self.root,True) as db:
            db.execute("UPDATE outcomes SET observed='2026-09-10T18:00:00+08:00'")
            presentation.freeze_condition_stats(db)
        self.assertEqual(presentation.listings(dict(self.options(),origin='20260911'),self.root)['items'][0]['rates']['known'],0)
    def test_dm_client_legacy_and_new_constructor(self):
        from broker_market.fetcher import make_client
        class Legacy:
            def __init__(self,username,password,timeout=45):self.timeout=timeout
        class New:
            def __init__(self,username,password,timeout=45,device_id=''):self.device=device_id
        account=dict(username='test',password='test',device_id='frozen-device')
        self.assertEqual(make_client(Legacy,account,9).timeout,9)
        self.assertEqual(make_client(New,account).device,'frozen-device')
        class Broken:
            def __init__(self,*args,**kwargs):raise TypeError('internal failure')
        with self.assertRaisesRegex(TypeError,'internal failure'):make_client(Broken,account)
    def test_scheduler_recovers_orphans_only_after_lock_ownership(self):
        from fair_value_observation.scheduler import ObservationScheduler
        from fair_value_observation.storage import job
        job(self.root,'predict',state='running')
        worker=ObservationScheduler(self.root)
        with patch.object(worker.lock,'acquire',return_value=False):self.assertFalse(worker.start())
        with connect(self.root) as db:self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'running')
        with patch.object(worker.lock,'acquire',return_value=True),patch('fair_value_observation.scheduler.threading.Thread'):
            self.assertTrue(worker.start())
        with connect(self.root) as db:self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'failed')
    def test_broker_orphan_recovery(self):
        from broker_market.scheduler import BondTradingScheduler
        state={'broker':{'state':'retrying'},'bond_picker':{'state':'running'}}
        worker=BondTradingScheduler()
        with patch('broker_market.scheduler.ensure_directories'),patch.object(worker._process_lock,'acquire',return_value=True),patch('broker_market.scheduler.load_status',return_value=state),patch('broker_market.scheduler.save_status') as save,patch('broker_market.scheduler.threading.Thread'):
            self.assertTrue(worker.start());self.assertEqual(save.call_args.args[0]['broker']['state'],'failed');self.assertEqual(save.call_args.args[0]['bond_picker']['state'],'failed')
    def test_idempotent_prediction_never_overwritten(self):
        engine.record(frame(),'prospective','one',self.root);engine.record(frame(12),'backfill','two',self.root)
        with connect(self.root) as db:
            p=db.execute('SELECT * FROM predictions').fetchall();self.assertEqual(len(p),1);self.assertEqual(p[0]['delta'],2.);self.assertEqual(p[0]['kind'],'prospective')
    def test_nested_median_not_parameter_count(self):
        value,conflict=engine.combine_values([dict(family='a',delta=6)]*10+[dict(family='b',delta=-2)])
        self.assertEqual(value,2);self.assertTrue(conflict)
    def test_outcome_revision_preserves_first(self):
        engine.record(frame(),'historical','one',self.root);engine.evaluate(FakeData(),self.root)
        data=FakeData();data.values={d:{'B.IB':1.9} for d in data.calendar};engine.evaluate(data,self.root)
        with connect(self.root) as db:
            self.assertAlmostEqual(db.execute('SELECT actual_delta FROM outcomes LIMIT 1').fetchone()[0],1)
            self.assertGreater(db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],0)
    def test_missing_path_not_fabricated_and_ten_day_pending(self):
        engine.record(frame(),'historical','one',self.root);data=FakeData();data.values={d:dict(v) for d,v in data.values.items()};data.values['20260907']={}
        engine.evaluate(data,self.root)
        with connect(self.root) as db:
            rows=db.execute('SELECT * FROM outcomes').fetchall();self.assertTrue(rows);self.assertTrue(all(r['horizon']!=10 for r in rows));self.assertIsNone(rows[0]['touched']);self.assertIsNone(rows[0]['adverse'])
    def test_selection_frozen_even_after_changed_results(self):
        engine.record(frame(),'historical','one',self.root);engine.evaluate(FakeData(),self.root);engine.freeze_selection(self.root)
        with connect(self.root,True) as db:db.execute('UPDATE outcomes SET correct=0')
        engine.freeze_selection(self.root)
        with connect(self.root) as db:self.assertEqual(db.execute("SELECT selected FROM rules WHERE direction='widen'").fetchone()[0],1)
    def test_neutral_merged_no_fair_value(self):
        engine.record(frame(),'historical','one',self.root)
        with connect(self.root,True) as db:
            db.execute('UPDATE rules SET selected=1');db.execute('UPDATE predictions SET delta=.4')
        engine.combine(self.root)
        result=query.bond_list(self.options(),self.root)
        self.assertIsNone(result['items'][0]['fair_yield']);self.assertEqual(result['items'][0]['direction'],'neutral')
    def options(self):return dict(version=rules.VERSION,kind='historical',horizon=5,origin=None,direction=None,search='',page=1,page_size=50,rule=None)
    def test_actual_zero_is_not_win(self):
        engine.record(frame(),'historical','one',self.root);data=FakeData();data.values={d:{'B.IB':2.} for d in data.calendar};engine.evaluate(data,self.root)
        with connect(self.root) as db:self.assertEqual(db.execute('SELECT SUM(correct) FROM outcomes').fetchone()[0],0)
    def test_api_login_pagination_export_and_readonly(self):
        engine.record(frame(),'historical','one',self.root)
        with connect(self.root,True) as db:db.execute('UPDATE rules SET selected=1')
        engine.combine(self.root);app=Flask(__name__);app.secret_key='test';app.config['FAIR_VALUE_OBSERVATION_ROOT']=str(self.root)
        def auth(fn):
            @wraps(fn)
            def wrapped(*a,**k):return fn(*a,**k) if session.get('authenticated') else redirect('/login')
            return wrapped
        register(app,auth,auth,lambda *a,**k:'page');client=app.test_client()
        self.assertEqual(client.get('/api/fair-value-observation/bonds').status_code,302)
        for path in ['dates','facets','listings','predictions/1/chart']:
            self.assertEqual(client.get('/api/fair-value-observation/'+path).status_code,302)
        with client.session_transaction() as s:s['authenticated']=True
        r=client.get('/api/fair-value-observation/bonds?kind=historical').get_json();self.assertEqual(r['total'],1)
        self.assertEqual(client.get('/api/fair-value-observation/bonds?page=0').status_code,400)
        self.assertEqual(client.get('/api/fair-value-observation/export?kind=historical').status_code,200)
        self.assertEqual(client.get('/api/fair-value-observation/listings').get_json()['total'],1)
        self.assertEqual(client.get('/api/fair-value-observation/listings?sort=invalid').status_code,400)
        self.assertEqual(client.get('/api/fair-value-observation/export?view=user').status_code,200)
        self.assertEqual(client.post('/api/fair-value-observation/retry',json={}).status_code,403)
        with connect(self.root) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)


if __name__=='__main__':unittest.main()
