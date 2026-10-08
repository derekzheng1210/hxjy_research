# -*- coding: utf-8 -*-
"""历史趋势聚合单测（/api/trends 底层，不连 Oracle、不依赖真实 cache.db）。"""
import math
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primary_market_pricing.trends import (  # noqa: E402
    build_trends,
    _term_bucket,
    _type_label,
)


def _make_conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE bond_deviations (
            symbol TEXT, issue_date TEXT, coupon_rate REAL, effective_term REAL,
            bond_type TEXT, issue_amount_wan REAL, issuer TEXT, computed_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO bond_deviations VALUES (?,?,?,?,?,?,?,?)", rows
    )
    return conn


class TermBucketTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(_term_bucket(0.74), "<1Y")
        self.assertEqual(_term_bucket(2.5), "1-3Y")
        self.assertEqual(_term_bucket(4.0), "3-5Y")
        self.assertEqual(_term_bucket(7.0), "5-10Y")
        self.assertEqual(_term_bucket(30.0), ">=10Y")
        self.assertEqual(_term_bucket(None), "未知")

    def test_type_label(self):
        self.assertEqual(_type_label("ordinary"), "普通信用债")
        self.assertEqual(_type_label("perpetual"), "永续债")
        self.assertEqual(_type_label(None), "其他")
        self.assertEqual(_type_label("unknown_kind"), "其他")


class BuildTrendsTests(unittest.TestCase):
    def setUp(self):
        # 2 只 2024-01（3Y 与 5Y 各一）+ 1 只 2025-06（0.74Y 短融）+ 1 只无票面
        self.rows = [
            ("B1", "20240115", 2.5, 3.0, "ordinary", 100000, "甲城投", "2024-01-16"),
            ("B2", "20240120", 3.5, 5.0, "perpetual", 50000, "乙集团", "2024-01-21"),
            ("B3", "20250610", 1.8, 0.74, "ordinary", 20000, "甲城投", "2025-06-11"),
            ("B4", "20250620", None, None, "tier2", None, "丙银行", "2025-06-21"),
        ]
        self.conn = _make_conn(self.rows)
        self.prov = {"甲城投": "江苏", "乙集团": "广东"}

    def test_monthly_basic(self):
        d = build_trends(self.conn, "20240101", "20251231", None)
        m = {r["ym"]: r for r in d["monthly"]}
        self.assertEqual(m["2024-01"]["cnt"], 2)
        self.assertAlmostEqual(m["2024-01"]["plan"], 15.0)  # 10+5 亿
        self.assertAlmostEqual(m["2024-01"]["yield_mean"], 3.0)  # (2.5+3.5)/2
        self.assertEqual(m["2025-06"]["cnt"], 2)
        self.assertAlmostEqual(m["2025-06"]["plan"], 2.0)  # 2 亿 + None 记 0
        self.assertAlmostEqual(m["2025-06"]["yield_mean"], 1.8)  # 无票面样本不进均值
        self.assertEqual(d["meta"]["years"], ["2024", "2025"])
        self.assertEqual(d["meta"]["total"], 4)

    def test_yield_term_tolerance(self):
        d = build_trends(self.conn, "20240101", "20251231", None)
        row = next(r for r in d["monthly_yield_term"] if r["ym"] == "2024-01")
        self.assertAlmostEqual(row["t3"], 2.5)  # term 3.0 落 t3
        self.assertAlmostEqual(row["t5"], 3.5)  # term 5.0 落 t5
        self.assertIsNone(row["t10"])
        # 0.74Y 不落任何目标桶，2025-06 不出现在序列里
        self.assertFalse(any(r["ym"] == "2025-06" for r in d["monthly_yield_term"]))

    def test_type_and_term_monthly(self):
        d = build_trends(self.conn, "20240101", "20251231", None)
        tm = {r["ym"]: r for r in d["type_monthly"]}
        self.assertAlmostEqual(tm["2024-01"]["普通信用债"], 10.0)
        self.assertAlmostEqual(tm["2024-01"]["永续债"], 5.0)
        ty = next(r for r in d["type_year_total"] if r["year"] == "2025")
        self.assertAlmostEqual(ty["二级资本债"], 0.0)  # 无金额
        rm = {r["ym"]: r for r in d["term_monthly"]}
        self.assertAlmostEqual(rm["2024-01"]["3-5Y"], 10.0)
        self.assertAlmostEqual(rm["2024-01"].get(">=10Y", 0.0), 0.0)
        self.assertAlmostEqual(rm["2025-06"]["<1Y"], 2.0)
        # 无期限样本进"未知"档（金额缺失记 0，但键应出现）
        self.assertIn("未知", rm["2025-06"])

    def test_yield_hist_normal(self):
        d = build_trends(self.conn, "20240101", "20251231", None)
        h = {x["year"]: x for x in d["yield_hist"]}
        self.assertEqual(h["2024"]["n"], 2)
        self.assertAlmostEqual(h["2024"]["mean"], 3.0)
        bins = {b["ybin"]: b["cnt"] for b in h["2024"]["bins"]}
        self.assertEqual(bins.get(2.5), 1)
        self.assertEqual(bins.get(3.5), 1)

    def test_province_top_year(self):
        d = build_trends(self.conn, "20240101", "20251231", self.prov)
        p = {g["year"]: {i["province_name"]: i for i in g["items"]} for g in d["province_top_year"]}
        self.assertAlmostEqual(p["2024"]["江苏"]["plan"], 10.0)
        self.assertAlmostEqual(p["2024"]["广东"]["plan"], 5.0)
        self.assertAlmostEqual(p["2025"]["江苏"]["cnt"], 1)
        # 丙银行无省份映射，不计入

    def test_no_province_map(self):
        d = build_trends(self.conn, "20240101", "20251231", None)
        self.assertEqual(d["province_top_year"], [])

    def test_empty_range(self):
        d = build_trends(self.conn, "20200101", "20201231", None)
        self.assertEqual(d["monthly"], [])
        self.assertEqual(d["meta"]["range"], "")
        self.assertEqual(d["yield_hist"], [])

    def test_range_filter(self):
        d = build_trends(self.conn, "20250101", "20251231", None)
        self.assertEqual([r["ym"] for r in d["monthly"]], ["2025-06"])


class ProvinceFileTests(unittest.TestCase):
    def test_load_missing_returns_none(self):
        from primary_market_pricing import trends as trends_mod

        with tempfile.TemporaryDirectory() as td:
            original = trends_mod._province_file_path
            trends_mod._province_file_path = lambda: os.path.join(td, "nope.json")
            try:
                self.assertIsNone(trends_mod.load_province_map())
            finally:
                trends_mod._province_file_path = original


if __name__ == "__main__":
    unittest.main()
