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
        # 第 12 列为财汇 BONDTYPE2 券种码（load_current_bond_rows 追加列）
        self.base = [['1.IB','测试二级资本债',2,'AAA','主体',2.0,'','','','','AA','523'], ['2.IB','测试债',4,'AA','主体',2.0,'','','','','A','641']]
        self.snapshot = {'generated_at':'2026-10-16 15:30:00','quotes':[
            {'code':'1.IB','bid_yield':2.02,'ofr_yield':1.98},
            {'code':'2.IB','bid_yield':2.4,'ofr_yield':2.01}]}
        self.history={'points':[
            {'observed_at':'2026-10-15 09:30:00','value':-1,'count':1,'tier2_capital':{'value':-2,'count':1}},
            {'observed_at':'2026-10-15 15:30:00','value':-3,'count':2,
             'breakdown':{'implied_rating':[{'label':'AA','value':-3,'count':2}]},
             'tier2_capital':{'value':-4,'count':1,'breakdown':{'term':[{'label':'1-3Y','value':-4,'count':1}]}}}]}
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
    def test_removed_sections_not_in_payload(self):
        # 最新报价快照与区间成交KPI已下线：载荷不再携带两个汇总块
        p=self.build()
        self.assertNotIn('quote_summary',p);self.assertNotIn('deal_summary',p)
    def test_breakdown_enriched_with_bond_type_and_side_metrics(self):
        p=self.build()
        by_label={g['label']:g for g in p['breakdown']['bond_type']}
        self.assertEqual(set(by_label),{'银行次级债','中票'})
        # 券1：bid 2.02/ofr 1.98 vs 估值 2.0 → bid偏离+2 / ofr偏离-2 / 中枢0 / 价差(bid-ofr)+4
        bank=by_label['银行次级债']
        self.assertEqual(bank['count'],1);self.assertEqual(bank['pool'],1)
        self.assertEqual(bank['bid'],2);self.assertEqual(bank['ofr'],-2)
        self.assertEqual(bank['value'],0);self.assertEqual(bank['width'],4)
        # 券2 ofr 偏离 1BP 超 30BP 阈值? 未超（1BP），bid 2.4 偏离+40BP 被剔除 → 仅单边有效
        note=by_label['中票']
        self.assertEqual(note['count'],0);self.assertEqual(note['bid_n'],0)
        self.assertEqual(note['ofr'],1);self.assertEqual(note['ofr_n'],1)
        self.assertIsNone(note['value']);self.assertIsNone(note['width'])
        for dim in ('implied_rating','internal_rating','term'):
            self.assertTrue(p['breakdown'][dim])
    def test_emotion_daily_carries_breakdown(self):
        p=self.build()
        self.assertEqual(p['emotion'][0]['value'],-3)
        self.assertEqual(p['emotion'][0]['breakdown']['implied_rating'],[{'label':'AA','value':-3,'count':2}])
        # tier2 口径取细分点位自带的分组
        t=self.build(scope='tier2')['emotion'][0]
        self.assertEqual(t['value'],-4)
        self.assertEqual(t['breakdown']['term'],[{'label':'1-3Y','value':-4,'count':1}])
    def test_deals_series_from_daily_report(self):
        p=self.build()
        self.assertEqual(p['deals_source'],'dm_report')
        self.assertEqual([r['date'] for r in p['deals']],['2026-10-14','2026-10-15'])
        self.assertEqual(sum(r['num'] for r in p['deals']),300)
        self.assertEqual(sum(r['tkn'] for r in p['deals']),180)
        self.assertEqual(p['report_updated'],'2026-10-16 09:00:00')
        self.assertEqual(p['report_range'],{'first':'2026-10-14','last':'2026-10-20'})
        # tier2 只取银行二永债细分
        t=[r for r in self.build(scope='tier2')['deals']]
        self.assertEqual(sum(r['num'] for r in t),30);self.assertEqual(sum(r['tkn'] for r in t),20)
    def test_no_per_bond_tables_in_payload(self):
        # 报价偏离明细与成交活跃券表已下线：载荷不再携带逐券明细
        p=self.build()
        self.assertNotIn('quotes',p);self.assertNotIn('active_bonds',p)
        self.assertNotIn('skipped',p);self.assertNotIn('deal_cache_updated',p)
    def test_empty_report_history_keeps_series_empty(self):
        with patch.object(dm_report,'load_history',return_value={'days':{}}):
            p=self.build()
        self.assertEqual(p['deals'],[])
        self.assertIsNone(p['report_range']['first'])
    def test_invalid_dates_and_scope(self):
        with self.assertRaises(ValueError):self.build(scope='bad')
        with self.assertRaises(ValueError):analytics.build_dashboard(start='2026-10-17',end='2026-10-16',base_rows=[])

if __name__=='__main__':unittest.main()
