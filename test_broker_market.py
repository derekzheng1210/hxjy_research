from __future__ import annotations

import shutil
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from broker_market import storage
from broker_market.scheduler import BROKER_TIMES, effective_success_for, latest_due, next_due
from juyuan_update.unified_excel import parse_neiping_sheet
from openpyxl import Workbook


class BrokerMarketStorageTests(unittest.TestCase):
    def test_quote_history_retains_last_ten_trading_days(self):
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
                # 只保留最近 10 个有数据的交易日（第 3~12 天），每天 2 个时点快照
                self.assertEqual(len(days), storage.QUOTE_HISTORY_TRADING_DAYS)
                self.assertEqual(days[0], "20260803")
                self.assertEqual(days[-1], "20260812")
                self.assertEqual(len(names), 20)
                self.assertEqual(names, sorted(names))
                # 最新快照不受历史清理影响
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
            ["1.IB", "24甲行二级资本债01", 0.8, "AA+", "主体甲", 2.10, "国企", "否", "是", "", "AA"],
            ["2.IB", "弱", 4.0, "AA", "主体乙", 2.00, "国企", "否", "否", "", "BBB"],
            ["3.IB", "24券商次级01", 3.0, "AA", "主体丙", 2.00, "国企", "否", "是", "", "AA"],
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
        self.assertEqual(result["tier2_capital"], {"value": -4.0, "count": 1})

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
        self.assertEqual(next_due(saturday, BROKER_TIMES), datetime(2026, 8, 31, 9, 30))

    def test_snapshot_time_is_fallback_success_for_manual_initialization(self):
        with patch("broker_market.scheduler.load_snapshot", return_value={"generated_at": "2026-08-27 11:36:49"}):
            value = effective_success_for("broker", {"last_success_scheduled_for": ""})
        self.assertEqual(value, datetime(2026, 8, 27, 11, 36, 49))

    def test_valuation_cache_time_is_fallback_success(self):
        with patch("broker_market.scheduler.load_json", return_value={"generated_at": "2026-08-27 08:31:02"}):
            value = effective_success_for("bond_picker", {"last_success_scheduled_for": ""})
        self.assertEqual(value, datetime(2026, 8, 27, 8, 31, 2))


if __name__ == "__main__":
    unittest.main()
