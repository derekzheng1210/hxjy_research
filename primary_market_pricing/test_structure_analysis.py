# -*- coding: utf-8 -*-
"""加权票面 与 结构分解/分档统计 的单测（裸 Flask 挂蓝图 + 临时 cache.db）。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from primary_market_pricing import app as app_module
from primary_market_pricing.cache_builder import init_cache_db

app_module.app = Flask(__name__)
app_module.app.register_blueprint(app_module.pricing_bp)


class BreakdownFixture(unittest.TestCase):
    """共用：临时 cache.db，插入五只样本券。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_path = str(Path(self._tmp.name) / "cache.db")
        conn = init_cache_db(self.cache_path)
        rule = app_module.ISSUE_DATE_RULE_VERSION
        rows = [
            # symbol, term, coupon, dev_bp, nm, nj, bond_type, amount_wan
            ("B1", 0.9, 2.0, -5.0, 1, 0, "ordinary", 10000),
            ("B2", 2.0, 2.2, 5.0, 0, 0, "ordinary", 30000),
            ("B3", 3.0, 2.1, 0.0, 0, 0, "ordinary", 20000),
            ("B4", 30.0, 3.2, None, 0, 1, "tier2", None),
            ("B5", 5.0, 2.5, 12.0, 0, 0, "perpetual", 40000),
        ]
        for symbol, term, coupon, dev, nm, nj, btype, amount in rows:
            conn.execute(
                """
                INSERT INTO bond_deviations
                    (symbol, issue_date_rule, bond_name, issuer, coupon_rate, issue_amount_wan,
                     issue_date, effective_term, raise_mode, bond_type, deviation_bp,
                     is_non_market, is_no_judgement, curve_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '1', ?, ?, ?, ?, '216')
                """,
                (symbol, rule, f"测试{symbol}", "测试发行人", coupon, amount,
                 "20260701", term, btype, dev, nm, nj),
            )
        conn.commit()
        conn.close()
        self.patcher = patch.object(app_module, "CACHE_DB_PATH", self.cache_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self._tmp.cleanup()


class BreakdownTests(BreakdownFixture):
    def test_type_term_breakdown_histogram_and_buckets(self):
        client = app_module.app.test_client()
        response = client.get("/api/structure-breakdown?start_date=20260601&end_date=20260801")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        by_type = {row["key"]: row for row in data["by_type"]}
        self.assertEqual(by_type["ordinary"]["total"], 3)
        self.assertEqual(by_type["ordinary"]["nm_n"], 1)
        self.assertEqual(by_type["perpetual"]["ovr_n"], 1)  # +12BP 发飞
        self.assertEqual(by_type["tier2"]["calc_n"], 0)     # 无判断不进分母
        self.assertEqual(
            [row["key"] for row in data["by_type"]],
            ["tier2", "perpetual", "ordinary"],
        )
        self.assertEqual(
            [row["key"] for row in data["by_term"]],
            ["<=1Y", "2Y", "3Y", "5Y", "30Y"],
        )
        hist = {row["bin"]: row["count"] for row in data["histogram"]}
        self.assertEqual(hist["-10~-3"], 1)  # -5
        self.assertEqual(hist["-3~3"], 1)    # 0
        self.assertEqual(hist["3~10"], 1)    # +5
        self.assertEqual(hist[">10"], 1)     # +12
        self.assertEqual(hist["<-10"], 0)

        buckets = {row["bucket"]: row for row in data["buckets"]}
        self.assertEqual(buckets["<=1Y"]["count"], 1)
        self.assertAlmostEqual(buckets["2Y"]["coupon_w"], 2.2, places=4)
        self.assertEqual(buckets["<=1Y"]["nm_ratio"], 1.0)
        self.assertEqual(buckets["30Y"]["calc_n"], 0)  # B4 无判断

    def test_invalid_range_returns_400(self):
        response = app_module.app.test_client().get(
            "/api/structure-breakdown?start_date=20260801&end_date=20260601"
        )
        self.assertEqual(response.status_code, 400)


class WeightedCouponTests(unittest.TestCase):
    def test_summarize_bonds_amount_weighted_coupon(self):
        bonds = [
            {"bond_symbol": "A", "issuer": "I", "issue_amount_wan": 10000.0, "coupon_rate": 2.0,
             "deviation_bp": -5.0, "is_non_market": True, "is_overpriced": False, "is_no_judgement": False},
            {"bond_symbol": "B", "issuer": "I", "issue_amount_wan": 30000.0, "coupon_rate": 3.0,
             "deviation_bp": 1.0, "is_non_market": False, "is_overpriced": False, "is_no_judgement": False},
            {"bond_symbol": "C", "issuer": "I", "issue_amount_wan": None, "coupon_rate": 9.9,
             "deviation_bp": None, "is_non_market": False, "is_overpriced": False, "is_no_judgement": True},
        ]
        result = app_module._summarize_bonds(bonds, issuer="I")
        self.assertAlmostEqual(result["coupon_w"], (2.0 * 1 + 3.0 * 3) / 4, places=4)

    def test_issuer_summary_aggregated_has_coupon_w(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = str(Path(temp) / "cache.db")
            conn = init_cache_db(cache_path)
            rule = app_module.ISSUE_DATE_RULE_VERSION
            conn.execute(
                """
                INSERT INTO bond_deviations
                    (symbol, issue_date_rule, issuer, coupon_rate, issue_amount_wan,
                     issue_date, effective_term, raise_mode, deviation_bp, is_non_market,
                     is_no_judgement)
                VALUES ('A', ?, '发行人X', 2.0, 10000, '20260701', 3, '1', -5.0, 1, 0)
                """,
                (rule,),
            )
            conn.commit()
            conn.close()
            with patch.object(app_module, "CACHE_DB_PATH", cache_path):
                result = app_module._read_issuer_summary_aggregated(
                    "20260601", "20260801", False, False, False, None, None,
                )
            self.assertAlmostEqual(result["issuers"][0]["coupon_w"], 2.0, places=4)


if __name__ == "__main__":
    unittest.main()
