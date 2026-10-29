from __future__ import annotations
import os
import unittest
from pathlib import Path
from unittest.mock import patch

# 先于 broker_market.paths 解析：默认沙箱，避免组合跑测试时把模块常量
# 绑定到生产数据目录。
os.environ.setdefault("PORTAL_DATA_ROOT", str(Path(__file__).resolve().parent / ".test_runtime" / "broker_analytics"))

from broker_market import analytics, dm_report, storage

class BrokerAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.base = [['1.IB','测试二级资本债',2,'AAA','主体',2.0,'','','','','AA'], ['2.IB','测试债',4,'AA','主体',2.0,'','','','','A']]
        self.snapshot = {'generated_at':'2026-10-16 15:30:00','quotes':[
            {'code':'1.IB','bid_yield':2.02,'ofr_yield':1.98},
            {'code':'2.IB','bid_yield':2.4,'ofr_yield':2.01}]}
        self.history={'points':[
            {'observed_at':'2026-10-15 09:30:00','value':-1,'count':1,'tier2_capital':{'value':-2,'count':1}},
            {'observed_at':'2026-10-15 15:30:00','value':-3,'count':2,'tier2_capital':{'value':-4,'count':1}}]}
        # 盘面日报历史：成交笔数/TKN/GVN 的权威来源
        self.report={'version':1,'updated_at':'2026-10-16 09:00:00','days':{
            '2026-10-14':{'num':200,'tkn':120,'gvn':70,'trd':0,'sectors':{}},
            '2026-10-15':{'num':100,'tkn':60,'gvn':35,'trd':0,'sectors':{
                '银行二永债':{'num':30,'tkn':20,'gvn':8,'maturity':{'<1Y':{'num':30,'tkn_pct':66.67,'gvn_pct':26.67}}}}},
            '2026-10-20':{'num':50,'tkn':25,'gvn':20,'trd':0,'sectors':{
                '银行二永债':{'num':10,'tkn':5,'gvn':4,'maturity':{}}}}}}
        self.patches=[patch.object(storage,'load_snapshot',return_value=self.snapshot),patch.object(storage,'load_emotion_history',return_value=self.history),patch.object(dm_report,'load_history',return_value=self.report)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
    def build(self,**kw):
        return analytics.build_dashboard(base_rows=self.base,start='2026-10-01',end='2026-10-16',**kw)
    def test_quote_cleaning_and_daily_last_observation(self):
        p=self.build();q=p['quote_summary']
        self.assertEqual(q['two_sided'],1);self.assertEqual(q['quoted'],2)
        self.assertEqual(q['width'],4);self.assertEqual(q['mid_dev'],0)
        self.assertEqual(p['emotion'][0]['value'],-3)
        self.assertEqual(self.build(scope='tier2')['emotion'][0]['value'],-4)
    def test_deals_series_from_daily_report(self):
        p=self.build();s=p['deal_summary']
        self.assertEqual(p['deals_source'],'dm_report')
        self.assertEqual([r['date'] for r in p['deals']],['2026-10-14','2026-10-15'])
        self.assertEqual(s['num'],300);self.assertEqual(s['tkn'],180);self.assertEqual(s['tkn_pct'],60.0)
        self.assertEqual(s['days'],2);self.assertEqual(p['report_updated'],'2026-10-16 09:00:00')
        self.assertEqual(p['report_range'],{'first':'2026-10-14','last':'2026-10-20'})
        # tier2 只取银行二永债细分
        t=self.build(scope='tier2')['deal_summary']
        self.assertEqual(t['num'],30);self.assertEqual(t['tkn'],20);self.assertEqual(t['tkn_pct'],66.67)
    def test_no_per_bond_tables_in_payload(self):
        # 报价偏离明细与成交活跃券表已下线：载荷不再携带逐券明细
        p=self.build()
        self.assertNotIn('quotes',p);self.assertNotIn('active_bonds',p)
        self.assertNotIn('skipped',p);self.assertNotIn('deal_cache_updated',p)
    def test_empty_report_history_keeps_series_empty(self):
        with patch.object(dm_report,'load_history',return_value={'days':{}}):
            p=self.build()
        self.assertEqual(p['deals'],[]);self.assertEqual(p['deal_summary']['num'],0)
        self.assertIsNone(p['deal_summary']['tkn_pct'])
        self.assertIsNone(p['report_range']['first'])
    def test_invalid_dates_and_scope(self):
        with self.assertRaises(ValueError):self.build(scope='bad')
        with self.assertRaises(ValueError):analytics.build_dashboard(start='2026-10-17',end='2026-10-16',base_rows=[])

if __name__=='__main__':unittest.main()
