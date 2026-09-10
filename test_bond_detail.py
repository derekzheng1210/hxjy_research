from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PORTAL_DATA_ROOT", str(Path(__file__).resolve().parent / ".test_runtime" / "bond_detail"))

import app as portal_app
from bond_detail import service as bond_service
from bond_detail.service import (
    calculate_holding_returns,
    interpolate_points,
    issuer_curve_analysis,
    rating_band_metrics,
)


class BondDetailMathTests(unittest.TestCase):
    def test_interpolate_points_does_not_extrapolate_by_default(self):
        points = [(1, 1.5), (2, 1.7), (2, 1.9), (3, 2.1)]
        self.assertAlmostEqual(interpolate_points(points, 2), 1.8)
        self.assertAlmostEqual(interpolate_points(points, 2.5), 1.95)
        self.assertIsNone(interpolate_points(points, 0.5))

    def test_rating_band_interpolates_the_exact_bond_tenor(self):
        start = date(2026, 1, 1)
        dates = [(start + timedelta(days=i)).isoformat() for i in range(40)]
        std_data = {
            "data": {
                "中短票AAA-国开_1Y": {
                    "dates": dates,
                    "values": [0.10 + i * 0.001 for i in range(40)],
                },
                "中短票AAA-国开_2Y": {
                    "dates": dates,
                    "values": [0.20 + i * 0.001 for i in range(40)],
                },
            }
        }
        result = rating_band_metrics(std_data, "中短票AAA-国开", 1.5)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["credit_spread_bp"], 18.9)
        self.assertEqual(result["sample_days"], 40)

    def test_issuer_curve_uses_leave_one_out_and_requires_both_sides(self):
        target = {"code": "T.IB", "term": 2.0, "sub": "否", "guarantor": ""}
        bonds = [
            target,
            {"code": "L1.IB", "name": "左1", "term": 1.0, "sub": "否", "guarantor": ""},
            {"code": "L2.IB", "name": "左2", "term": 1.5, "sub": "否", "guarantor": ""},
            {"code": "R1.IB", "name": "右1", "term": 3.0, "sub": "否", "guarantor": ""},
            {"code": "R2.IB", "name": "右2", "term": 4.0, "sub": "否", "guarantor": ""},
            {"code": "TECH.SH", "name": "科创债", "term": 2.5, "sub": "否", "guarantor": "", "tech": "是"},
            {"code": "SUB.IB", "name": "次级", "term": 2.5, "sub": "是", "guarantor": ""},
        ]
        yields = {
            "T.IB": 2.10,
            "L1.IB": 1.80,
            "L2.IB": 1.85,
            "R1.IB": 1.95,
            "R2.IB": 2.00,
            "TECH.SH": 2.80,
            "SUB.IB": 3.00,
        }
        result = issuer_curve_analysis(target, bonds, yields)
        self.assertTrue(result["available"])
        self.assertEqual(result["sample_count"], 5)
        self.assertEqual(result["excluded_exchange_tech_count"], 1)
        self.assertGreater(result["residual_bp"], result["threshold_bp"])
        self.assertEqual(result["convexity"], "明显凸点")
        included = issuer_curve_analysis(target, bonds, yields, exclude_exchange_tech=False)
        self.assertEqual(included["sample_count"], 6)
        self.assertEqual(included["excluded_exchange_tech_count"], 0)

    def test_issuer_curve_extrapolates_with_sufficient_samples(self):
        target = {"code": "T.IB", "term": 5.0, "sub": "否", "guarantor": ""}
        bonds = [
            target,
            {"code": "L1.IB", "name": "左1", "term": 1.0, "sub": "否", "guarantor": ""},
            {"code": "L2.IB", "name": "左2", "term": 1.5, "sub": "否", "guarantor": ""},
            {"code": "R1.IB", "name": "右1", "term": 3.0, "sub": "否", "guarantor": ""},
            {"code": "R2.IB", "name": "右2", "term": 4.0, "sub": "否", "guarantor": ""},
        ]
        yields = {
            "T.IB": 2.10,
            "L1.IB": 1.80,
            "L2.IB": 1.85,
            "R1.IB": 1.95,
            "R2.IB": 2.00,
        }
        result = issuer_curve_analysis(target, bonds, yields)
        self.assertTrue(result["available"])
        self.assertTrue(result["extrapolated"])
        self.assertIn("线性外推", result["extrapolation_note"])
        self.assertFalse(result["has_right_sample"])
        self.assertTrue(result["has_left_sample"])
        # 最近3只样本 (1.5,1.85)(3.0,1.95)(4.0,2.00) 的最小二乘外推值
        self.assertAlmostEqual(result["curve_yield"], 2.0645, places=3)
        # 外推结果置信度上限为“中”
        self.assertEqual(result["confidence"], "中")

    def test_issuer_curve_declines_extrapolation_when_target_too_far(self):
        target = {"code": "T.IB", "term": 8.5, "sub": "否", "guarantor": ""}
        bonds = [
            target,
            {"code": "L1.IB", "name": "左1", "term": 1.0, "sub": "否", "guarantor": ""},
            {"code": "L2.IB", "name": "左2", "term": 1.5, "sub": "否", "guarantor": ""},
            {"code": "R1.IB", "name": "右1", "term": 3.0, "sub": "否", "guarantor": ""},
            {"code": "R2.IB", "name": "右2", "term": 4.0, "sub": "否", "guarantor": ""},
        ]
        yields = {
            "T.IB": 2.10,
            "L1.IB": 1.80,
            "L2.IB": 1.85,
            "R1.IB": 1.95,
            "R2.IB": 2.00,
        }
        result = issuer_curve_analysis(target, bonds, yields)
        self.assertFalse(result["available"])
        self.assertIn("过远", result["reason"])

    def test_issuer_curve_extrapolates_by_rating_curve_spread_when_far(self):
        # 样本 0.5-6Y，目标 7.5Y（偏离边界 1.5Y > 1Y）：按最近样本相对评级曲线的平均利差外推
        target = {"code": "T.IB", "term": 7.5, "sub": "否", "guarantor": ""}
        bonds = [
            target,
            {"code": "B1.IB", "name": "债1", "term": 0.5, "sub": "否", "guarantor": ""},
            {"code": "B2.IB", "name": "债2", "term": 1.0, "sub": "否", "guarantor": ""},
            {"code": "B3.IB", "name": "债3", "term": 3.0, "sub": "否", "guarantor": ""},
            {"code": "B4.IB", "name": "债4", "term": 6.0, "sub": "否", "guarantor": ""},
        ]
        # 评级曲线：2.0 + 0.1t；样本收益率 = 曲线 + 0.3 利差
        curve = lambda t: 2.0 + 0.1 * t
        yields = {
            "T.IB": 3.10,
            "B1.IB": 2.35,
            "B2.IB": 2.40,
            "B3.IB": 2.60,
            "B4.IB": 2.90,
        }
        result = issuer_curve_analysis(target, bonds, yields, rating_curve_yield=curve)
        self.assertTrue(result["available"])
        self.assertTrue(result["extrapolated"])
        self.assertIn("平均利差", result["extrapolation_note"])
        # 评级曲线(7.5)=2.75 + 平均利差0.3 = 3.05
        self.assertAlmostEqual(result["curve_yield"], 3.05, places=4)
        self.assertEqual(result["confidence"], "中")

    def test_issuer_curve_declines_far_extrapolation_without_rating_curve(self):
        target = {"code": "T.IB", "term": 7.0, "sub": "否", "guarantor": ""}
        bonds = [
            target,
            {"code": "L1.IB", "name": "左1", "term": 1.0, "sub": "否", "guarantor": ""},
            {"code": "L2.IB", "name": "左2", "term": 1.5, "sub": "否", "guarantor": ""},
            {"code": "R1.IB", "name": "右1", "term": 3.0, "sub": "否", "guarantor": ""},
            {"code": "R2.IB", "name": "右2", "term": 4.0, "sub": "否", "guarantor": ""},
        ]
        yields = {
            "T.IB": 2.10,
            "L1.IB": 1.80,
            "L2.IB": 1.85,
            "R1.IB": 1.95,
            "R2.IB": 2.00,
        }
        result = issuer_curve_analysis(target, bonds, yields)
        self.assertFalse(result["available"])
        self.assertIn("利差外推", result["reason"])

    def test_issuer_curve_declines_extrapolation_with_few_samples(self):
        target = {"code": "T.IB", "term": 5.0, "sub": "否", "guarantor": ""}
        bonds = [
            target,
            {"code": "R1.IB", "name": "右1", "term": 3.0, "sub": "否", "guarantor": ""},
            {"code": "R2.IB", "name": "右2", "term": 4.0, "sub": "否", "guarantor": ""},
        ]
        yields = {"T.IB": 2.10, "R1.IB": 1.95, "R2.IB": 2.00}
        result = issuer_curve_analysis(target, bonds, yields)
        self.assertFalse(result["available"])
        self.assertIn("样本不足", result["reason"])

    def test_holding_return_compares_same_cashflow_rating_benchmark(self):
        bond = {"effective_maturity_date": "2028-08-31"}
        details = {
            "coupon_rate": 2.0,
            "start_date": "2025-08-31",
            "maturity_date": "2028-08-31",
            "payment_mode": "4",
            "payments_per_year": 1,
            "payment_day_rules": "08-31",
        }
        result = calculate_holding_returns(
            bond, details, "2026-08-31", 2.30, 2.00, 1.90
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["horizon_days"], 91)
        self.assertIsNotNone(result["excess_return_pct"])
        self.assertAlmostEqual(
            result["actual"]["cash_received"],
            result["rating_benchmark"]["cash_received"],
        )
        six_month = calculate_holding_returns(
            bond, details, "2026-08-31", 2.30, 2.00, 1.85, horizon_months=6
        )
        self.assertTrue(six_month["available"])
        self.assertEqual(six_month["horizon_months"], 6)
        self.assertEqual(six_month["horizon_days"], 182)


    def test_rating_curve_overlay_includes_whole_year_points(self):
        # 曲线节点不在整年上时，整年也应补出可悬停的数据点
        day = {"中短票AA": {"0.5": 2.0, "1.5": 2.1, "2.5": 2.2}}
        points = bond_service._rating_curve_overlay(day, "中短票AA", [0.8, 2.3])
        by_term = {point["term"]: point["yield"] for point in points}
        self.assertIn(1.0, by_term)
        self.assertAlmostEqual(by_term[1.0], 2.05, places=4)
        self.assertIn(2.0, by_term)
        self.assertAlmostEqual(by_term[2.0], 2.15, places=4)


class CreditAndComplianceTests(unittest.TestCase):
    def test_credit_facility_reports_portal_limit(self):
        portal_payload = {
            "limits": {"某集团": 18.03},
            "ratings": {"某集团": "BBB+"},
            "limits_date": "2026-08-30",
        }
        with patch(
            "juyuan_update.neiping_portal_fetch.load_portal_data",
            return_value=portal_payload,
        ):
            result = bond_service.credit_facility_analysis({"issuer": "某集团"})
        self.assertTrue(result["available"])
        self.assertEqual(result["available_limit"], 18.03)
        self.assertEqual(result["internal_rating"], "BBB+")
        self.assertTrue(result["meets_recommend_threshold"])
        self.assertEqual(result["data_date"], "2026-08-30")

    def test_credit_facility_handles_missing_issuer(self):
        with patch(
            "juyuan_update.neiping_portal_fetch.load_portal_data",
            return_value={"limits": {}, "ratings": {}, "limits_date": "2026-08-30"},
        ):
            result = bond_service.credit_facility_analysis({"issuer": "无此主体"})
        self.assertFalse(result["available"])
        self.assertIsNone(result["available_limit"])
        self.assertFalse(result["meets_recommend_threshold"])
        self.assertTrue(result["note"])

    def test_rating_compliance_uses_cached_facts(self):
        fact = {
            "issue_date": "2020-01-01",
            "issuer_dates": ["2020-06-20", "2026-06-26"],
            "credit_dates": [],
        }
        with patch(
            "juyuan_update.rating_compliance.load_rating_facts_cache",
            return_value={"generated_at": "2026-08-31 08:34:30", "facts": {"T.IB": fact}},
        ):
            result = bond_service.rating_compliance_analysis("T.IB")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["issuer_rating_latest"], "2026-06-26")
        self.assertEqual(result["issuer_rating_count"], 2)
        self.assertEqual(result["credit_rating_count"], 0)
        self.assertEqual(result["issue_date"], "2020-01-01")

    def test_rating_compliance_reports_missing_bond(self):
        with patch(
            "juyuan_update.rating_compliance.load_rating_facts_cache",
            return_value={"generated_at": "2026-08-31 08:34:30", "facts": {}},
        ):
            result = bond_service.rating_compliance_analysis("UNKNOWN.IB")
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["reason"])


class BondDetailRouteTests(unittest.TestCase):
    def setUp(self):
        portal_app.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = portal_app.app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def test_page_is_available_and_contains_search_surface(self):
        response = self.client.get("/bond-detail?code=TEST.IB")
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("债券详查", text)
        self.assertIn("TEST.IB", text)

    def test_api_returns_a_bounded_not_found_error(self):
        with patch.object(portal_app, "build_bond_detail", side_effect=KeyError("未找到该债券")):
            response = self.client.get("/api/bond-detail/UNKNOWN.IB")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "未找到该债券")

    def test_api_sets_etag_for_successful_payload(self):
        payload = {"version": "abc123", "bond": {"code": "T.IB"}}
        with patch.object(portal_app, "build_bond_detail", return_value=payload):
            response = self.client.get("/api/bond-detail/T.IB")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_etag()[0], "abc123")

    def test_api_passes_six_month_horizon_and_curve_filter(self):
        payload = {"version": "six123", "bond": {"code": "T.IB"}}
        with patch.object(portal_app, "build_bond_detail", return_value=payload) as builder:
            response = self.client.get(
                "/api/bond-detail/T.IB?horizon_months=6&exclude_exchange_tech=0"
            )
        self.assertEqual(response.status_code, 200)
        builder.assert_called_once_with(
            "T.IB", exclude_exchange_tech=False, horizon_months=6
        )

    def test_quote_history_route_returns_payload_with_etag(self):
        payload = {"version": "qh123", "days": []}
        with patch.object(portal_app, "build_quote_history", return_value=payload) as builder:
            response = self.client.get("/api/bond-quote-history/T.IB?days=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_etag()[0], "qh123")
        builder.assert_called_once_with("T.IB", days=5, start=None, end=None)

    def test_quote_history_route_maps_parameter_and_not_found_errors(self):
        with patch.object(portal_app, "build_quote_history", side_effect=ValueError("天数仅支持1至30天")):
            response = self.client.get("/api/bond-quote-history/T.IB?days=31")
        self.assertEqual(response.status_code, 422)
        self.assertIn("天数", response.get_json()["error"])
        response = self.client.get("/api/bond-quote-history/T.IB?days=abc")
        self.assertEqual(response.status_code, 422)
        with patch.object(portal_app, "build_quote_history", side_effect=KeyError("未找到该债券")):
            response = self.client.get("/api/bond-quote-history/UNKNOWN.IB")
        self.assertEqual(response.status_code, 404)


class BondDetailPerformanceCacheTests(unittest.TestCase):
    def setUp(self):
        bond_service._reset_detail_caches_for_test()

    def tearDown(self):
        bond_service._reset_detail_caches_for_test()

    def test_read_model_rebuilds_only_when_source_version_changes(self):
        first = ((1, 1), (1, 1), (1, 1))
        second = ((2, 1), (1, 1), (1, 1))
        with patch.object(bond_service, "_read_model_signature", side_effect=[first, first, second]), patch.object(
            bond_service,
            "_build_read_model",
            side_effect=lambda signature: {"signature": signature},
        ) as builder:
            self.assertIs(bond_service._get_read_model(), bond_service._get_read_model())
            bond_service._get_read_model()
        self.assertEqual(builder.call_count, 2)

    def test_spread_index_rebuilds_when_spread_source_changes(self):
        first = ((1, 1), (1, 1))
        second = ((1, 2), (1, 1))
        with patch.object(bond_service, "_spread_model_signature", side_effect=[first, first, second]), patch.object(
            bond_service,
            "_build_spread_model",
            side_effect=lambda signature: {"signature": signature},
        ) as builder:
            bond_service._get_spread_model()
            bond_service._get_spread_model()
            bond_service._get_spread_model()
        self.assertEqual(builder.call_count, 2)

    def test_detail_cache_is_keyed_by_source_and_parameters(self):
        payload = {"version": "v1", "bond": {"code": "T.IB"}}
        with patch.object(bond_service, "_detail_source_token", return_value="source-v1"), patch.object(
            bond_service, "_build_bond_detail_uncached", return_value=payload
        ) as builder:
            self.assertIs(bond_service.build_bond_detail("T.IB"), payload)
            self.assertIs(bond_service.build_bond_detail("T.IB"), payload)
            bond_service.build_bond_detail("T.IB", horizon_months=6)
            bond_service.build_bond_detail("T.IB", exclude_exchange_tech=False)
        self.assertEqual(builder.call_count, 3)

    def test_detail_cache_invalidates_when_any_source_version_changes(self):
        with patch.object(bond_service, "_detail_source_token", side_effect=["source-v1", "source-v2"]), patch.object(
            bond_service,
            "_build_bond_detail_uncached",
            side_effect=[{"version": "v1"}, {"version": "v2"}],
        ) as builder:
            self.assertEqual(bond_service.build_bond_detail("T.IB")["version"], "v1")
            self.assertEqual(bond_service.build_bond_detail("T.IB")["version"], "v2")
        self.assertEqual(builder.call_count, 2)

    def test_detail_cache_single_flight_prevents_duplicate_builds(self):
        def slow_builder(*_args, **_kwargs):
            time.sleep(0.04)
            return {"version": "v1", "bond": {"code": "T.IB"}}

        with patch.object(bond_service, "_detail_source_token", return_value="source-v1"), patch.object(
            bond_service, "_build_bond_detail_uncached", side_effect=slow_builder
        ) as builder, ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(lambda _: bond_service.build_bond_detail("T.IB"), range(5)))
        self.assertEqual(builder.call_count, 1)
        self.assertTrue(all(result["version"] == "v1" for result in results))

    def test_oracle_disabled_degrades_without_connecting(self):
        with patch.object(bond_service, "ORACLE_INSTRUMENT_ENABLED", False), patch.object(
            bond_service, "connect"
        ) as connect:
            result = bond_service.fetch_instrument_details("NO_ORACLE_CACHE_TEST.IB")
        connect.assert_not_called()
        self.assertIn("未启用", result["error"])

    def test_quote_history_uses_latest_snapshot_per_day(self):
        names = [
            "20260901_090000.json", "20260901_150000.json",
            "20260902_093000.json", "20260902_153000.json",
            "20260903_090000.json",
        ]
        with patch.object(bond_service, "list_quote_history", return_value=names):
            paths = bond_service._selected_quote_history_paths()
        self.assertEqual([path.name for path in paths], [
            "20260903_090000.json", "20260902_153000.json", "20260901_150000.json",
        ])


class QuoteHistoryTests(unittest.TestCase):
    """本券报价历史：全部盘中时点、逐日上一交易日估值、增量缓存与参数校验。"""

    def setUp(self):
        bond_service._reset_detail_caches_for_test()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.history_dir = Path(self._tmp.name)
        self._names: list[str] = []
        self.bond = {"code": "T.IB", "name": "测试债", "issuer": "测试发行人", "term": 2.0}
        self.spread_cache: dict[str, dict] = {}

    def _write_snapshot(self, name: str, rows: list[dict], *, pretty: bool = False):
        payload = {
            "version": name, "generated_at": bond_service._history_name_to_at(name),
            "quote_count": len(rows), "quotes": rows,
        }
        with (self.history_dir / name).open("w", encoding="utf-8", newline="\n") as stream:
            if pretty:
                json.dump(payload, stream, ensure_ascii=False)
            else:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        if name not in self._names:
            self._names.append(name)
            self._names.sort()

    def _quote_row(self, code: str = "T.IB", bid=2.0, ofr=2.05):
        return {
            "code": code, "name": "测试债", "bid_yield": bid, "ofr_yield": ofr,
            "bid_volume_value": 1000.0, "ofr_volume_value": 2000.0,
            "bid_volume_text": "1000", "ofr_volume_text": "2000",
            "bid_broker": "国利", "ofr_broker": "平安",
            "bid_time": "09:30", "ofr_time": "09:31", "quote_time": "09:31",
            "has_bid": True, "has_offer": True, "two_sided": True,
        }

    def _patches(self):
        return (
            patch.object(bond_service, "list_quote_history", return_value=list(self._names)),
            patch.object(bond_service, "HISTORY_DIR", self.history_dir),
            patch.object(bond_service, "_bond_indexes", return_value=([], {"T.IB": self.bond, "T": self.bond}, {})),
            patch.object(bond_service, "_get_spread_model", return_value={"history_cache": self.spread_cache}),
        )

    def _build(self, **kwargs):
        patch_days, patch_dir, patch_bonds, patch_spread = self._patches()
        with patch_days, patch_dir, patch_bonds, patch_spread:
            return bond_service.build_quote_history("T.IB", **kwargs)

    def test_days_preset_returns_all_intraday_points_from_old_to_new(self):
        self.spread_cache = {"20260908": {"T": {"yield": 2.0}}}
        self._write_snapshot("20260909_093000.json", [self._quote_row(bid=2.0, ofr=2.05)])
        self._write_snapshot("20260909_150000.json", [self._quote_row(bid=2.02, ofr=2.06)])
        self._write_snapshot("20260910_093000.json", [])
        self._write_snapshot("20260910_103000.json", [self._quote_row(bid=2.01, ofr=2.04)])
        payload = self._build(days=2)
        self.assertEqual([day["date"] for day in payload["days"]], ["2026-09-09", "2026-09-10"])
        self.assertEqual(payload["range"]["trading_days"], 2)
        self.assertEqual(payload["range"]["start"], "2026-09-09")
        self.assertEqual(payload["available"]["latest"], "2026-09-10")
        first, second = payload["days"]
        self.assertEqual(
            [s["observed_at"] for s in first["snapshots"]],
            ["2026-09-09 09:30:00", "2026-09-09 15:00:00"],
        )
        self.assertAlmostEqual(first["snapshots"][0]["bid"], 2.0)
        self.assertAlmostEqual(first["snapshots"][0]["ofr_vs_valuation_bp"], 5.0)
        # 09-10 09:30 本券无报价：时间点保留（图上留空档），标记 absent
        self.assertTrue(second["snapshots"][0]["absent"])
        self.assertIsNone(second["snapshots"][0]["bid"])
        self.assertAlmostEqual(second["snapshots"][1]["bid"], 2.01)

    def test_days_preset_keeps_only_days_with_snapshots(self):
        self._write_snapshot("20260907_093000.json", [self._quote_row()])
        self._write_snapshot("20260909_093000.json", [self._quote_row()])
        payload = self._build(days=5)
        self.assertEqual([day["date"] for day in payload["days"]], ["2026-09-07", "2026-09-09"])

    def test_daily_valuation_uses_previous_trading_day(self):
        self.spread_cache = {
            "20260908": {"T": {"yield": 1.90}},
            "20260909": {"T": {"yield": 1.95}},
            "20260910": {"T": {"yield": 2.00}},
        }
        self._write_snapshot("20260909_093000.json", [self._quote_row()])
        self._write_snapshot("20260910_093000.json", [self._quote_row()])
        payload = self._build(days=2)
        first, second = payload["days"]
        self.assertAlmostEqual(first["valuation_yield"], 1.90)
        self.assertEqual(first["valuation_date"], "2026-09-08")
        self.assertAlmostEqual(second["valuation_yield"], 1.95)
        self.assertEqual(second["valuation_date"], "2026-09-09")

    def test_daily_valuation_falls_back_when_bond_missing_that_date(self):
        self.spread_cache = {
            "20260908": {"T": {"yield": 1.90}},
            "20260909": {"OTHER": {"yield": 1.00}},
        }
        self._write_snapshot("20260910_093000.json", [self._quote_row()])
        payload = self._build(days=1)
        day = payload["days"][0]
        self.assertAlmostEqual(day["valuation_yield"], 1.90)
        self.assertEqual(day["valuation_date"], "2026-09-08")

    def test_daily_valuation_missing_everywhere_is_null(self):
        self.spread_cache = {"20260909": {"OTHER": {"yield": 1.00}}}
        self._write_snapshot("20260910_093000.json", [self._quote_row()])
        payload = self._build(days=1)
        day = payload["days"][0]
        self.assertIsNone(day["valuation_yield"])
        self.assertEqual(day["valuation_date"], "")
        self.assertIsNone(day["snapshots"][0]["ofr_vs_valuation_bp"])

    def test_outlier_is_judged_against_daily_valuation(self):
        self.spread_cache = {
            "20260908": {"T": {"yield": 2.00}},
            "20260909": {"T": {"yield": 2.50}},
        }
        # 同一报价 2.40：09-09 时点对照 09-08 估值 2.00 偏离 40BP（异常置空），
        # 09-10 时点对照 09-09 估值 2.50 偏离 10BP（保留）。
        self._write_snapshot("20260909_093000.json", [self._quote_row(bid=2.40, ofr=2.40)])
        self._write_snapshot("20260910_093000.json", [self._quote_row(bid=2.40, ofr=2.40)])
        payload = self._build(days=2)
        first, second = payload["days"]
        self.assertIsNone(first["snapshots"][0]["bid"])
        self.assertTrue(first["snapshots"][0]["bid_outlier"])
        self.assertAlmostEqual(second["snapshots"][0]["bid"], 2.40)
        self.assertFalse(second["snapshots"][0]["bid_outlier"])

    def test_custom_range_filters_days(self):
        for day in ("20260907", "20260908", "20260909", "20260910"):
            self._write_snapshot(f"{day}_093000.json", [self._quote_row()])
        payload = self._build(start="2026-09-08", end="2026-09-09")
        self.assertEqual([day["date"] for day in payload["days"]], ["2026-09-08", "2026-09-09"])
        self.assertEqual(payload["range"]["start"], "2026-09-08")
        self.assertEqual(payload["range"]["end"], "2026-09-09")

    def test_invalid_params_raise_value_error(self):
        self._write_snapshot("20260909_093000.json", [self._quote_row()])
        with self.assertRaises(ValueError):
            self._build(days=0)
        with self.assertRaises(ValueError):
            self._build(days=31)
        with self.assertRaises(ValueError):
            self._build(days="x")
        with self.assertRaises(ValueError):
            self._build(days=5, start="2026-09-01", end="2026-09-02")
        with self.assertRaises(ValueError):
            self._build(start="2026-09-05", end="2026-09-01")
        with self.assertRaises(ValueError):
            self._build(start="2026-08-01", end="2026-09-10")
        with self.assertRaises(ValueError):
            self._build(start="2026-09-01")

    def test_unknown_bond_raises_key_error(self):
        self._write_snapshot("20260909_093000.json", [self._quote_row()])
        with patch.object(bond_service, "list_quote_history", return_value=list(self._names)), \
                patch.object(bond_service, "HISTORY_DIR", self.history_dir), \
                patch.object(bond_service, "_bond_indexes", return_value=([], {}, {})):
            with self.assertRaises(KeyError):
                bond_service.build_quote_history("UNKNOWN.IB", days=5)

    def test_day_cache_extracts_only_new_files(self):
        self._write_snapshot("20260909_093000.json", [self._quote_row(bid=2.0)])
        self._write_snapshot("20260909_100000.json", [self._quote_row(bid=2.01)])
        original = bond_service._extract_quote_row
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3], \
                patch.object(bond_service, "_extract_quote_row", side_effect=original) as extractor:
            bond_service.build_quote_history("T.IB", days=1)
            self.assertEqual(extractor.call_count, 2)
            self._write_snapshot("20260909_150000.json", [self._quote_row(bid=2.02)])
            # list_quote_history 的返回值在补丁创建时已固定，手动纳入新文件
            with patch.object(bond_service, "list_quote_history", return_value=list(self._names)):
                payload = bond_service.build_quote_history("T.IB", days=1)
            self.assertEqual(extractor.call_count, 3)
        self.assertEqual(
            [s["observed_at"] for s in payload["days"][0]["snapshots"]],
            ["2026-09-09 09:30:00", "2026-09-09 10:00:00", "2026-09-09 15:00:00"],
        )

    def test_payload_cache_hits_and_invalidates_on_file_change(self):
        self._write_snapshot("20260909_093000.json", [self._quote_row(bid=2.0)])
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3]:
            first = bond_service.build_quote_history("T.IB", days=1)
            second = bond_service.build_quote_history("T.IB", days=1)
            self.assertIs(first, second)
            # 文件签名变化（重写内容）后缓存失效，重新组装
            self._write_snapshot("20260909_093000.json", [self._quote_row(bid=2.05)])
            with patch.object(bond_service, "list_quote_history", return_value=list(self._names)):
                third = bond_service.build_quote_history("T.IB", days=1)
        self.assertIsNot(third, first)
        self.assertAlmostEqual(third["days"][0]["snapshots"][0]["bid"], 2.05)

    def test_extract_quote_row_survives_tricky_content_and_format(self):
        tricky = self._quote_row(code="A.IB")
        tricky["name"] = '怪名{带}花括号"code":"T.IB"转义'
        self._write_snapshot("20260909_093000.json", [tricky, self._quote_row(bid=2.0)])
        row = bond_service._extract_quote_row(self.history_dir / "20260909_093000.json", "T.IB")
        self.assertEqual(row["code"], "T.IB")
        self.assertAlmostEqual(row["bid_yield"], 2.0)
        # 紧凑分隔符匹配失败（如此处用默认带空格格式写出）时回退整文件解析
        self._write_snapshot("20260909_100000.json", [tricky, self._quote_row(bid=2.1)], pretty=True)
        row = bond_service._extract_quote_row(self.history_dir / "20260909_100000.json", "T.IB")
        self.assertEqual(row["code"], "T.IB")
        self.assertAlmostEqual(row["bid_yield"], 2.1)
        # 文件中无本券时返回 None
        self._write_snapshot("20260909_110000.json", [tricky])
        self.assertIsNone(bond_service._extract_quote_row(self.history_dir / "20260909_110000.json", "T.IB"))


if __name__ == "__main__":
    unittest.main()
