from __future__ import annotations

import os
import unittest
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
        self.assertIn("过远", result["reason"])

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


if __name__ == "__main__":
    unittest.main()
