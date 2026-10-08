"""新发券债券详查门禁、债券代码搜索与嵌入模式的单测。

覆盖 2026-09-30 需求：
1. 未进入债券详查数据池（bond_static）的新发券不可跳详查（detail_available 标注）；
2. 顶部搜索支持按债券代码匹配（缓存路径）；
3. embed=1 嵌入模式（看板 iframe 内嵌发行人分析）模板把门户导航高度归零。
"""

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


def _pool(codes):
    """构造 load_bond_static 的返回载荷。"""
    return {
        "generated_at": "2026-09-30 08:00:00",
        "bonds": [{"code": code} for code in codes],
    }


class BondDetailGateTests(unittest.TestCase):
    def setUp(self):
        app_module._bond_detail_codes_cache = None

    def tearDown(self):
        app_module._bond_detail_codes_cache = None

    def test_codes_index_full_and_bare_forms(self):
        pool = _pool([f"{i:09d}.IB" for i in range(1200)] + ["253268.SH"])
        with patch.object(app_module, "load_bond_static", return_value=pool):
            codes = app_module._bond_detail_codes()
        self.assertIn("253268.SH", codes)
        self.assertIn("253268", codes)
        self.assertIn("000000001", codes)
        self.assertIn("000000001.IB", codes)

    def test_small_pool_disables_gate(self):
        with patch.object(app_module, "load_bond_static", return_value=_pool(["1", "2"])):
            self.assertIsNone(app_module._bond_detail_codes())
        bonds = [{"bond_symbol": "NOT_IN_POOL", "ref_bond_symbol": "ALSO_NOT"}]
        with patch.object(app_module, "load_bond_static", return_value=_pool(["1", "2"])):
            app_module._annotate_detail_availability(bonds)
        self.assertTrue(bonds[0]["detail_available"])
        self.assertTrue(bonds[0]["ref_detail_available"])

    def test_annotate_marks_pool_membership(self):
        pool = _pool([f"{i:09d}.IB" for i in range(1200)] + ["253268.SH"])
        bonds = [
            {"bond_symbol": "253268"},                      # 裸代码命中池内 253268.SH
            {"bond_symbol": "282849", "ref_bond_symbol": "253268.SH"},  # 新券在池外，参考债券在池内
            {"bond_symbol": ""},
        ]
        with patch.object(app_module, "load_bond_static", return_value=pool):
            app_module._annotate_detail_availability(bonds)
        self.assertTrue(bonds[0]["detail_available"])
        self.assertFalse(bonds[1]["detail_available"])
        self.assertTrue(bonds[1]["ref_detail_available"])
        self.assertFalse(bonds[2]["detail_available"])  # 无代码本就不可跳转

    def test_issuer_api_bonds_carry_availability(self):
        pool = _pool([f"{i:09d}.IB" for i in range(1200)])
        cached = {
            "issuer": "测试发行人",
            "bonds": [
                {"bond_symbol": "282849", "issuer": "测试发行人", "issue_date": "20260930"},
                {"bond_symbol": "000000001", "issuer": "测试发行人", "issue_date": "20260101"},
            ],
            "_source": "cache",
        }
        with patch.object(
            app_module, "_read_issuer_partial_from_cache", return_value=(cached, [])
        ), patch.object(app_module, "_schedule_amount_backfill", return_value=False), patch.object(
            app_module, "load_bond_static", return_value=pool
        ):
            response = app_module.app.test_client().get("/api/issuer/测试发行人")
        self.assertEqual(response.status_code, 200)
        bonds = {b["bond_symbol"]: b for b in response.get_json()["bonds"]}
        self.assertFalse(bonds["282849"]["detail_available"])
        self.assertTrue(bonds["000000001"]["detail_available"])

    def test_date_api_bonds_carry_availability(self):
        pool = _pool([f"{i:09d}.IB" for i in range(1200)])
        with tempfile.TemporaryDirectory() as temp:
            cache_path = str(Path(temp) / "cache.db")
            conn = init_cache_db(cache_path)
            conn.execute(
                """
                INSERT INTO bond_deviations (
                    symbol, bond_name, issuer, issue_date, issue_date_rule, raise_mode
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "282849", "26泉投01", "泉州市国有资产投资集团有限责任公司",
                    "20260930", app_module.ISSUE_DATE_RULE_VERSION, "1",
                ),
            )
            conn.commit()
            conn.close()

            def fake_conn():
                new_conn = sqlite3.connect(cache_path)
                new_conn.row_factory = sqlite3.Row
                return new_conn

            with patch.object(app_module, "_get_cache_conn", side_effect=fake_conn), patch.object(
                app_module, "_schedule_amount_backfill", return_value=False
            ), patch.object(app_module, "load_bond_static", return_value=pool):
                response = app_module.app.test_client().get("/api/date/20260930")
        self.assertEqual(response.status_code, 200)
        bonds = response.get_json()["bonds"]
        self.assertEqual(len(bonds), 1)
        self.assertFalse(bonds[0]["detail_available"])


class BondCodeSearchTests(unittest.TestCase):
    def _cache_with_bonds(self, temp):
        cache_path = str(Path(temp) / "cache.db")
        conn = init_cache_db(cache_path)
        conn.execute(
            """
            INSERT INTO bond_deviations (
                symbol, bond_name, issuer, issue_date, issue_date_rule, raise_mode
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "282849", "26泉投01", "泉州市国有资产投资集团有限责任公司",
                "20260603", app_module.ISSUE_DATE_RULE_VERSION, "2",
            ),
        )
        conn.commit()
        conn.close()
        return cache_path

    def test_search_by_exact_code_returns_bond(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = self._cache_with_bonds(temp)

            def fake_conn():
                new_conn = sqlite3.connect(cache_path)
                new_conn.row_factory = sqlite3.Row
                return new_conn

            with patch.object(app_module, "_get_cache_conn", side_effect=fake_conn):
                results = app_module._search_from_cache("282849")
        self.assertTrue(results)
        top = results[0]
        self.assertEqual(top["match_type"], "bond")
        self.assertEqual(top["bond_symbol"], "282849")
        self.assertEqual(top["label"], "26泉投01")


class EmbedModeTests(unittest.TestCase):
    def test_embed_collapses_portal_nav_height(self):
        client = app_module.app.test_client()
        embedded = client.get("/?embed=1&view=issuer&issuer=测试发行人")
        self.assertEqual(embedded.status_code, 200)
        self.assertIn("--portal-nav-height: 0px", embedded.get_data(as_text=True))

    def test_normal_mode_keeps_default_height(self):
        client = app_module.app.test_client()
        normal = client.get("/")
        self.assertEqual(normal.status_code, 200)
        self.assertNotIn("--portal-nav-height: 0px", normal.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
