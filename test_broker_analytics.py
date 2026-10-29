from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from broker_market import analytics, storage

class BrokerAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd()/'.test_runtime')
        self.root = Path(self.tmp.name)
        self.base = [['1.IB','测试二级资本债',2,'AAA','主体',2.0,'','','','','AA'], ['2.IB','测试债',4,'AA','主体',2.0,'','','','','A']]
        self.snapshot = {'generated_at':'2026-10-16 15:30:00','quotes':[
            {'code':'1.IB','bid_yield':2.02,'ofr_yield':1.98},
            {'code':'2.IB','bid_yield':2.4,'ofr_yield':2.01}]}
        self.history={'points':[
            {'observed_at':'2026-10-15 09:30:00','value':-1,'count':1,'tier2_capital':{'value':-2,'count':1}},
            {'observed_at':'2026-10-15 15:30:00','value':-3,'count':2,'tier2_capital':{'value':-4,'count':1}}]}
        self.patches=[patch.object(analytics,'DATA_DIR',self.root),patch.object(storage,'load_snapshot',return_value=self.snapshot),patch.object(storage,'load_emotion_history',return_value=self.history)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def cache(self,code,day,data):
        p=self.root/'dm_deals'/code/(day+'.json');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data),encoding='utf8')
    def build(self,**kw):
        return analytics.build_dashboard(base_rows=self.base,start='2026-10-01',end='2026-10-16',**kw)
    def test_quote_cleaning_and_daily_last_observation(self):
        p=self.build();q=p['quote_summary']
        self.assertEqual(q['two_sided'],1);self.assertEqual(q['quoted'],2)
        self.assertEqual(q['width'],4);self.assertEqual(q['mid_dev'],0)
        self.assertEqual(p['emotion'][0]['value'],-3)
        self.assertEqual(self.build(scope='tier2')['emotion'][0]['value'],-4)
    def test_count_weighting_and_no_daily_minute_double_count(self):
        self.cache('1','20261015',{'stats':{'num':10,'tkn':8,'gvn':1,'trd':1},'minutes':[{'num':100,'tkn':100}]})
        self.cache('2','20261015',{'stats':{'num':90,'tkn':9,'gvn':81,'trd':0}})
        self.cache('2','20261016',{'minutes':[{'num':2,'tkn':1,'gvn':1,'trd':0}]})
        p=self.build();s=p['deal_summary']
        self.assertEqual(s['num'],102);self.assertEqual(s['tkn'],18);self.assertEqual(s['tkn_pct'],17.65)
        self.assertEqual(p['deals'][0]['bonds'],2)
        self.assertEqual(self.build(scope='tier2')['deal_summary']['num'],10)
    def test_missing_invalid_and_zero_denominators(self):
        self.assertIsNone(self.build()['deal_summary']['tkn_pct'])
        self.cache('1','20261015',{'stats':{'num':1,'tkn':2,'gvn':0,'trd':0}})
        self.cache('1','20261016',{'stats':{'num':0,'tkn':0,'gvn':0,'trd':0}})
        p=self.build();self.assertEqual(p['skipped'],1);self.assertEqual(p['deals'],[])
    def test_invalid_dates_and_scope(self):
        with self.assertRaises(ValueError):self.build(scope='bad')
        with self.assertRaises(ValueError):analytics.build_dashboard(start='2026-10-17',end='2026-10-16',base_rows=[])

if __name__=='__main__':unittest.main()
