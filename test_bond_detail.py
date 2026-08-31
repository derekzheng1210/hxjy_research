from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PORTAL_DATA_ROOT", str(Path(__file__).resolve().parent / ".test_runtime" / "bond_detail"))

import app as portal_app
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
