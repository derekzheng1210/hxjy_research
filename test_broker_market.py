from __future__ import annotations

import json
import os
import shutil
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# 先于 broker_market.paths 解析：默认沙箱，避免与其他测试同进程组合时
# 落到生产数据目录（本文件曾是无沙箱根设置的测试）。
os.environ.setdefault("PORTAL_DATA_ROOT", str(Path(__file__).resolve().parent / ".test_runtime" / "broker_market"))

from broker_market import dm_deals, dm_trades, fetcher, storage
from broker_market.scheduler import BROKER_TIMES, effective_success_for, latest_due, next_due
from juyuan_update.unified_excel import parse_neiping_sheet
from openpyxl import Workbook


class BrokerMarketStorageTests(unittest.TestCase):
    def test_quote_history_retains_all_trading_days(self):
        snapshot_target = Path.cwd() / ".test-history-latest.json"
        history_dir = Path.cwd() / ".test-quote-history"
        shutil.rmtree(history_dir, ignore_errors=True)
        try:
            with patch.object(storage, "SNAPSHOT_PATH", snapshot_target), \
                 patch.object(storage, "HISTORY_DIR", history_dir):
                # 连续 12 个交易日、每天两个抓取时点
                for day in range(1, 13):
                    for hour in (9, 14):
                        storage.save_snapshot(
                            [{"bondCode": f"{day:02d}{hour}.IB", "ofrYield": 2.0}],
                            generated_at=datetime(2026, 8, day, hour, 30, 0),
                        )
                names = storage.list_quote_history()
                days = sorted({name[:8] for name in names})
                # 历史快照永久保留：12 个交易日全部留存，每天 2 个时点快照
                self.assertEqual(len(days), 12)
                self.assertEqual(days[0], "20260801")
                self.assertEqual(days[-1], "20260812")
                self.assertEqual(len(names), 24)
                self.assertEqual(names, sorted(names))
                # 最新快照不受历史写入影响
                self.assertTrue(snapshot_target.exists())
        finally:
            shutil.rmtree(history_dir, ignore_errors=True)
            snapshot_target.unlink(missing_ok=True)

    def test_quote_history_same_moment_overwrites(self):
        snapshot_target = Path.cwd() / ".test-history-latest.json"
        history_dir = Path.cwd() / ".test-quote-history"
        shutil.rmtree(history_dir, ignore_errors=True)
        try:
            with patch.object(storage, "SNAPSHOT_PATH", snapshot_target), \
                 patch.object(storage, "HISTORY_DIR", history_dir):
                moment = datetime(2026, 8, 28, 14, 0, 3)
                storage.save_snapshot([{"bondCode": "1.IB", "ofrYield": 2.0}], generated_at=moment)
                storage.save_snapshot([{"bondCode": "2.IB", "ofrYield": 2.1}], generated_at=moment)
                names = storage.list_quote_history()
                self.assertEqual(len(names), 1)
                import json
                payload = json.loads((history_dir / names[0]).read_text(encoding="utf-8"))
                self.assertEqual(payload["quote_count"], 1)
        finally:
            shutil.rmtree(history_dir, ignore_errors=True)
            snapshot_target.unlink(missing_ok=True)

    def test_neiping_counterparty_limit_parser_finds_real_headers(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "neiping"
        sheet.append(["信用风险-评级查询-主体查询"])
        sheet.append(["筛选条件"])
        sheet.append(["融资主体", "内部评级", "最新可用对手限额"])
        sheet.append(["主体甲", "AA", 1])
        sheet.append(["主体乙", "BBB", 1.25])
        sheet.append(["主体丙", "BBB-", None])
        self.assertEqual(parse_neiping_sheet(sheet), {"主体甲": 1.0, "主体乙": 1.25})
    def test_normalize_code_and_quote(self):
        self.assertEqual(storage.normalize_code(" 102681601.ib "), "102681601.IB")
        row = storage.normalize_market_row({
            "bondCode": "102681601.ib",
            "bondShortName": "测试债",
            "bidYield": 2.18,
            "ofrYield": 2.16,
            "bidVolumeStr": "3000*",
            "bidVolumeValue": 3000,
            "ofrVolumeStr": "5000 P",
            "ofrVolumeValue": 5000,
        })
        self.assertTrue(row["two_sided"])
        self.assertEqual(row["bid_volume_text"], "3000*")
        self.assertEqual(row["ofr_volume_text"], "5000 P")

    def test_merge_calculates_trading_spreads_and_nulls_last_ready(self):
        base = [
            ["102681601.IB", "便宜券", 2.0, "AA+", "主体", 2.10, "地方国企", "否", "否", "", "AA+"],
            ["102681602.IB", "无报价", 3.0, "AA+", "主体", 2.20, "地方国企", "否", "否", "", "AA+"],
        ]
        snapshot = {"quotes": [{
            "code": "102681601.IB", "bid_yield": 2.15, "ofr_yield": 2.12,
            "bid_volume_text": "1000*", "bid_volume_value": 1000,
            "ofr_volume_text": "5000", "ofr_volume_value": 5000,
        }]}
        merged = storage.merge_bond_rows(base, snapshot)
        self.assertEqual(merged[0][17], -2.0)  # 中债2.10 - Ofr2.12 = -2bp（便宜）
        self.assertEqual(merged[0][18], 3.0)
        self.assertTrue(merged[0][25])
        self.assertIsNone(merged[1][17])
        self.assertFalse(merged[1][25])

    def test_outlier_quote_is_removed_one_side_only(self):
        base = [["1.IB", "单边清洗", 2.0, "AA", "主体", 2.10, "国企", "否", "否", "", "AA"]]
        snapshot = {"quotes": [{
            "code": "1.IB", "bid_yield": 2.41, "ofr_yield": 2.12,
            "bid_volume_text": "5000", "bid_volume_value": 5000,
            "ofr_volume_text": "2000", "ofr_volume_value": 2000,
            "bid_broker": "异常买方", "ofr_broker": "正常卖方",
        }]}
        row = storage.merge_bond_rows(base, snapshot)[0]
        self.assertIsNone(row[13])
        self.assertEqual(row[14], 2.12)
        self.assertEqual(row[11], "")
        self.assertEqual(row[15], "2000")
        self.assertFalse(row[24])
        self.assertTrue(row[25])
        self.assertFalse(row[26])

    def test_market_emotion_is_equal_weighted_and_has_breakdowns(self):
        base = [
            ["1.IB", "24甲行二级资本债01", 0.8, "AA+", "主体甲", 2.10, "国企", "否", "是", "", "AA", "523"],
            ["2.IB", "弱", 4.0, "AA", "主体乙", 2.00, "国企", "否", "否", "", "BBB", "641"],
            ["3.IB", "24券商次级01", 3.0, "AA", "主体丙", 2.00, "国企", "否", "是", "", "AA", ""],
        ]
        snapshot = {"quotes": [
            {"code": "1.IB", "bid_yield": 2.05, "ofr_yield": 2.07},
            {"code": "2.IB", "bid_yield": 2.01, "ofr_yield": 2.03},
            {"code": "3.IB", "bid_yield": 2.03, "ofr_yield": 2.05},
        ]}
        result = storage.calculate_market_emotion(base, snapshot)
        self.assertEqual(result["value"], 0.67)
        self.assertEqual(result["count"], 3)
        self.assertEqual({x["label"] for x in result["breakdown"]["term"]}, {"≤1Y", "1-3Y", "3-5Y"})
        # 券种维度：BONDTYPE2 映射，缺码归「其他」
        by_label = {x["label"]: x for x in result["breakdown"]["bond_type"]}
        self.assertEqual(set(by_label), {"银行次级债", "中票", "其他"})
        self.assertEqual(by_label["银行次级债"]["pool"], 1)
        self.assertEqual(by_label["银行次级债"]["count"], 1)
        self.assertEqual(by_label["银行次级债"]["width"], -2)
        # 全市场中枢口径与二级资本债细分各自携带分组
        self.assertEqual(result["tier2_capital"]["value"], -4.0)
        self.assertEqual(result["tier2_capital"]["count"], 1)
        self.assertTrue(result["tier2_capital"]["breakdown"]["bond_type"])

    def test_emotion_history_keeps_intraday_scheduled_points(self):
        target = Path.cwd() / ".test-market-emotion-history.json"
        base = [["1.IB", "情绪券", 2, "AA", "主体", 2.10, "国企", "否", "否", "", "AA"]]
        try:
            with patch.object(storage, "EMOTION_HISTORY_PATH", target):
                first = {"generated_at": "2026-08-27 09:31:00", "quotes": [{"code": "1.IB", "bid_yield": 2.05, "ofr_yield": 2.07}]}
                second = {"generated_at": "2026-08-27 10:01:00", "quotes": [{"code": "1.IB", "bid_yield": 2.06, "ofr_yield": 2.08}]}
                storage.record_market_emotion(first, datetime(2026, 8, 27, 9, 30), base)
                storage.record_market_emotion(second, datetime(2026, 8, 27, 10, 0), base)
                points = storage.load_emotion_history()["points"]
                self.assertEqual(len(points), 2)
                self.assertEqual(points[0]["scheduled_for"], "2026-08-27 09:30:00")
                self.assertEqual(points[1]["scheduled_for"], "2026-08-27 10:00:00")
        finally:
            target.unlink(missing_ok=True)

    def test_scheduler_status_changes_picker_data_version(self):
        target = Path.cwd() / ".test-scheduler-status-version.json"
        try:
            with patch.object(storage, "STATUS_PATH", target):
                target.write_text("{}", encoding="utf-8")
                before = storage.data_version()
                target.write_text('{"broker":{"state":"running"}}', encoding="utf-8")
                self.assertNotEqual(storage.data_version(), before)
        finally:
            target.unlink(missing_ok=True)

    def test_deals_file_changes_picker_data_version(self):
        target = Path.cwd() / ".test-latest-deals-version.json"
        try:
            with patch.object(storage, "DEALS_PATH", target):
                target.write_text("{}", encoding="utf-8")
                before = storage.data_version()
                target.write_text('{"deal_count": 1}', encoding="utf-8")
                self.assertNotEqual(storage.data_version(), before)
        finally:
            target.unlink(missing_ok=True)

    def test_empty_refresh_does_not_replace_last_success(self):
        target = Path.cwd() / ".test-latest-snapshot.json"
        history_dir = Path.cwd() / ".test-empty-refresh-history"
        shutil.rmtree(history_dir, ignore_errors=True)
        try:
            with patch.object(storage, "SNAPSHOT_PATH", target),                  patch.object(storage, "HISTORY_DIR", history_dir):
                storage.save_snapshot([{"bondCode": "1.IB", "ofrYield": 2.1}])
                before = target.read_bytes()
                with self.assertRaises(RuntimeError):
                    storage.save_snapshot([])
                self.assertEqual(target.read_bytes(), before)
        finally:
            target.unlink(missing_ok=True)
            shutil.rmtree(history_dir, ignore_errors=True)

    def test_preferences_are_bounded_and_validated(self):
        payload = storage.validate_preferences({
            "favorites": ["1.ib", "1.IB", "2.sh"],
            "presets": [{
                "id": "alpha", "name": "便宜卖盘",
                "filters": {"hasOffer": True, "unknown": "drop"},
                "sort": [{"key": "valuationOffer", "dir": "asc"}, {"key": "bad", "dir": "desc"}],
            }],
        })
        self.assertEqual(payload["favorites"], ["1.IB", "2.SH"])
        self.assertNotIn("presets", payload)
        self.assertEqual(payload["recommendation_settings"]["min_offer_volume"], 1000.0)


class BrokerSchedulerTests(unittest.TestCase):
    def test_weekday_due_and_catch_up(self):
        now = datetime(2026, 8, 27, 10, 12)  # Thursday
        self.assertEqual(latest_due(now, BROKER_TIMES), datetime(2026, 8, 27, 10, 0))
        self.assertEqual(next_due(now, BROKER_TIMES), datetime(2026, 8, 27, 10, 30))

    def test_weekend_has_no_latest_due(self):
        saturday = datetime(2026, 8, 29, 12, 0)
        self.assertIsNone(latest_due(saturday, BROKER_TIMES))
        self.assertEqual(next_due(saturday, BROKER_TIMES), datetime(2026, 8, 31, 9, 10))

    def test_first_intraday_point_is_preopen(self):
        morning = datetime(2026, 8, 27, 9, 20)  # Thursday, between the two morning pulls
        self.assertEqual(latest_due(morning, BROKER_TIMES), datetime(2026, 8, 27, 9, 10))
        self.assertEqual(next_due(morning, BROKER_TIMES), datetime(2026, 8, 27, 9, 30))

    def test_snapshot_time_is_fallback_success_for_manual_initialization(self):
        with patch("broker_market.scheduler.load_snapshot", return_value={"generated_at": "2026-08-27 11:36:49"}):
            value = effective_success_for("broker", {"last_success_scheduled_for": ""})
        self.assertEqual(value, datetime(2026, 8, 27, 11, 36, 49))

    def test_valuation_cache_time_is_fallback_success(self):
        with patch("broker_market.scheduler.load_json", return_value={"generated_at": "2026-08-27 08:31:02"}):
            value = effective_success_for("bond_picker", {"last_success_scheduled_for": ""})
        self.assertEqual(value, datetime(2026, 8, 27, 8, 31, 2))


class _FakeTradeClient:
    """仿 DMClient 的成交拉取面，供 dm_trades 单测使用（不触网）。"""

    def __init__(self, stat_rows, bond_items):
        self._stat_rows = stat_rows
        self._bond_items = bond_items

    def fetch_partitioned_today_trade_stats(self):
        return self._stat_rows, []

    def fetch_bond_info(self, codes):
        wanted = set(codes)
        return [item for item in self._bond_items if item.get("bondUniCode") in wanted]


class TodayTradesTests(unittest.TestCase):
    def setUp(self):
        # 显式隔离日缓存目录：模块级沙箱依赖 import 顺序（组合跑测试时
        # broker_market 可能已被未设沙箱的测试按生产路径导入），
        # write_web_today_stats 的副作用曾因此把 "123" 写进生产 dm_deals。
        self.deals_dir = Path.cwd() / ".test-today-trades-cache"
        shutil.rmtree(self.deals_dir, ignore_errors=True)
        patcher = patch.object(dm_deals, "_deals_dir", self.deals_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.deals_dir, ignore_errors=True)

    @staticmethod
    def _row(**overrides):
        row = {
            "bondUniCode": 111, "priceStatus": 0, "issueTime": 1792636393000,
            "yield": 1.90754, "netPrice": 100.20294, "fullPrice": None,
            "stat_tradeNum": 52, "stat_gvnTradeNum": 19, "stat_tknTradeNum": 33,
            "stat_trdTradeNum": 0, "stat_highYield": 1.915, "stat_lowYield": 1.9075,
            "tradeYieldSubCbBp": None,
        }
        row.update(overrides)
        return row

    def test_build_deals_maps_codes_and_drops_cancelled_or_unmapped(self):
        rows = [
            self._row(),
            # 同券重复行：保留 issueTime 最新的一条（yield 为关键字，经字典传参）
            self._row(issueTime=1792636394000, **{"yield": 1.92}),
            # 撤销状态不进入成交列
            self._row(bondUniCode=222, priceStatus=2),
            # 无法映射证券代码的行剔除
            self._row(bondUniCode=333),
        ]
        deals = dm_trades.build_deals(rows, {111: "232680045.IB"})
        self.assertEqual(list(deals), ["232680045.IB"])
        deal = deals["232680045.IB"]
        self.assertEqual(deal["yield"], 1.92)
        self.assertEqual(deal["num"], 52)
        self.assertEqual(deal["tkn"], 33)
        self.assertEqual(deal["gvn"], 19)
        self.assertEqual(deal["time"], datetime.fromtimestamp(1792636394000 / 1000).strftime("%H:%M:%S"))
        self.assertNotIn("_ts", deal)

    def test_build_deals_drops_non_bond_code_shapes(self):
        # 债券信息偶发返回内部短码（如 "123"）：形态不过关的映射整行剔除
        rows = [self._row(bondUniCode=code) for code in (111, 222, 333, 444)]
        mapping = {111: "123.IB", 222: "123", 333: "01100.IB", 444: "232680046.IB"}
        deals = dm_trades.build_deals(rows, mapping)
        self.assertEqual(list(deals), ["232680046.IB"])

    def test_fetch_and_save_today_trades_writes_deals_file(self):
        target = Path.cwd() / ".test-latest-deals.json"
        try:
            client = _FakeTradeClient(
                [self._row()],
                [{"bondUniCode": 111, "bondCode": "232680045.IB"}, {"bondUniCode": 999, "bondCode": "242480015.IB"}],
            )
            with patch.object(dm_trades, "DEALS_PATH", target):
                payload = dm_trades.fetch_and_save_today_trades(client, username="tester")
            self.assertEqual(payload["deal_count"], 1)
            self.assertEqual(payload["dm_account"], "tester")
            self.assertEqual(payload["trade_date"], datetime.now().strftime("%Y-%m-%d"))
            self.assertEqual(payload["deals"]["232680045.IB"]["tkn"], 33)
            written = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(written["deals"]["232680045.IB"]["yield"], 1.9075)
            # 日缓存写入落在沙箱目录，不触生产 dm_deals
            self.assertTrue((self.deals_dir / "232680045").is_dir())
        finally:
            target.unlink(missing_ok=True)

    def test_deals_augmentation_isolated_from_quote_snapshot(self):
        result = {}
        with patch.object(dm_trades, "fetch_and_save_today_trades", return_value={"deal_count": 5}) as fake:
            fetcher._augment_with_today_deals(object(), {"username": "u"}, result)
            fake.assert_called_once()
            self.assertEqual(result, {"deals_count": 5})
        result = {}
        with patch.object(dm_trades, "fetch_and_save_today_trades", side_effect=RuntimeError("boom")):
            fetcher._augment_with_today_deals(object(), {"username": "u"}, result)
            self.assertIn("RuntimeError: boom", result["deals_error"])
            self.assertNotIn("deals_count", result)


class DealsFallbackTests(unittest.TestCase):
    """量化API首选、网页端备源：写入标记 / 当日降级 / 熔断 / 恢复修复。"""

    def setUp(self):
        self.deals_dir = Path.cwd() / ".test-dm-deals-fallback"
        shutil.rmtree(self.deals_dir, ignore_errors=True)
        patcher = patch.object(dm_deals, "_deals_dir", self.deals_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.deals_dir, ignore_errors=True)
        dm_deals._quant_down_until = 0.0
        dm_deals._payload_cache.clear()
        self.addCleanup(setattr, dm_deals, "_quant_down_until", 0.0)
        self.addCleanup(dm_deals._payload_cache.clear)
        self.today = date.today().strftime("%Y%m%d")
        self.yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")

    @staticmethod
    def _web_deal():
        return {"num": 8, "tkn": 6, "gvn": 2, "trd": 0, "yield": 1.645, "net": 100.021,
                "high": 1.645, "low": 1.645, "sub_cb": -1.2, "time": "10:50:58"}

    def test_write_web_today_stats_marks_web_source(self):
        written = dm_deals.write_web_today_stats({
            "102683178.IB": self._web_deal(),
            "000000.IB": {"num": 0},
        })
        self.assertEqual(written, 1)
        data = dm_deals._read_day_cache("102683178.IB", self.today, ignore_ttl=True)
        self.assertEqual(data["stats_source"], "web")
        self.assertEqual(data["stats"]["num"], 8)
        self.assertEqual(data["stats"]["tkn"], 6)
        self.assertEqual(data["stats"]["dev_bp"], -1.2)
        self.assertNotIn("minutes", data)

    def test_today_falls_back_to_web_when_quant_down(self):
        dm_deals.write_web_today_stats({"102683178.IB": self._web_deal()})
        with patch.object(dm_deals, "_fetch_minutes", side_effect=dm_deals.DealsFetchError("无订阅套餐")):
            payload = dm_deals.build_deal_history("102683178.IB", range_start=self.today, range_end=self.today)
        self.assertEqual(payload["today_source"], "web")
        self.assertFalse(payload["quant_available"])
        today_row = payload["days"][-1]
        self.assertTrue(today_row["is_today"])
        self.assertEqual(today_row["num"], 8)
        self.assertEqual(today_row["minutes"], [])

    def test_quant_minutes_preferred_over_web_stats(self):
        dm_deals.write_web_today_stats({"102683178.IB": self._web_deal()})
        minutes = [{"time": "10:00", "yield": 1.7, "net_price": 100.0, "gvn": 0, "tkn": 3, "trd": 0, "num": 3}]
        with patch.object(dm_deals, "_fetch_minutes", return_value=minutes):
            payload = dm_deals.build_deal_history("102683178.IB", range_start=self.today, range_end=self.today)
        self.assertEqual(payload["today_source"], "quant")
        self.assertEqual(payload["days"][-1]["num"], 3)
        self.assertEqual(payload["days"][-1]["minutes"], minutes)

    def test_breaker_skips_quant_within_cooldown(self):
        dm_deals.write_web_today_stats({"102683178.IB": self._web_deal()})
        calls = []

        def failing_fetch(code, day):
            calls.append(day)
            raise dm_deals.DealsFetchError("down")

        with patch.object(dm_deals, "_fetch_minutes", side_effect=failing_fetch):
            dm_deals.build_deal_history("102683178.IB", range_start=self.today, range_end=self.today)
            dm_deals._payload_cache.clear()
            dm_deals.build_deal_history("102683178.IB", range_start=self.today, range_end=self.today)
        self.assertEqual(calls, [self.today], "breaker must skip quant retries within cooldown")

    def test_web_marked_days_repaired_when_quant_returns(self):
        dm_deals._merge_day_cache("102683178.IB", self.yesterday, stats={
            "num": 5, "gvn": 1, "tkn": 4, "trd": 0, "close_yield": 2.0,
            "high_yield": 2.1, "low_yield": 1.9, "cb_yield": None, "dev_bp": None,
        }, stats_set=True, stats_source=True)
        official = {self.yesterday: {
            "num": 6, "gvn": 2, "tkn": 4, "trd": 0, "close_yield": 2.01,
            "high_yield": 2.1, "low_yield": 1.95, "cb_yield": 2.0, "dev_bp": 1.0,
        }}
        with patch.object(dm_deals, "_fetch_stats_range", return_value=official) as fetch_stats, \
             patch.object(dm_deals, "_fetch_minutes", return_value=[]):
            payload = dm_deals.build_deal_history("102683178.IB", range_start=self.yesterday, range_end=self.yesterday)
        fetch_stats.assert_called_once()
        expected_date = f"{self.yesterday[:4]}-{self.yesterday[4:6]}-{self.yesterday[6:]}"
        day = next(d for d in payload["days"] if d["date"] == expected_date)
        self.assertEqual(day["num"], 6)
        stored = dm_deals._read_day_cache("102683178.IB", self.yesterday)
        self.assertNotIn("stats_source", stored, "official stats must clear the web marker")
        self.assertEqual(stored["stats"]["num"], 6)

    def test_quant_down_serves_cached_history_only(self):
        cached = {"num": 3, "gvn": 0, "tkn": 3, "trd": 0, "close_yield": 2.0,
                  "high_yield": 2.0, "low_yield": 2.0, "cb_yield": None, "dev_bp": None}
        dm_deals._merge_day_cache("102683178.IB", self.yesterday, stats=cached, stats_set=True)
        older = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
        with patch.object(dm_deals, "_fetch_stats_range", side_effect=dm_deals.DealsFetchError("down")), \
             patch.object(dm_deals, "_fetch_minutes", side_effect=dm_deals.DealsFetchError("down")):
            payload = dm_deals.build_deal_history("102683178.IB", range_start=older, range_end=self.yesterday)
        self.assertFalse(payload["quant_available"])
        self.assertEqual(len(payload["days"]), 1, "only cached days are served while quant is down")


if __name__ == "__main__":
    unittest.main()
