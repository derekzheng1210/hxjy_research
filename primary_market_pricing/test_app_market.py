import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from flask import Flask
from primary_market_pricing import app as app_module
from primary_market_pricing.cache_builder import init_cache_db, save_issuer_result


# The production module is a Blueprint registered by the portal application.
# Mount it directly so these endpoint tests exercise its unprefixed API routes.
app_module.app = Flask(__name__)
app_module.app.register_blueprint(app_module.pricing_bp)


class MarketAggregationTests(unittest.TestCase):
    def test_summary_and_rolling_ratios_use_calculable_denominator(self):
        bonds = [
            self._bond("20260102", -5, non_market=True),
            self._bond("20260102", None, no_judgement=True),
            self._bond("20260105", 5, overpriced=True),
            self._bond("20260105", 0),
        ]

        result = app_module._build_market_result(bonds, "20260102", "20260105")

        self.assertEqual(result["summary"]["total_bonds"], 4)
        self.assertEqual(result["summary"]["calculated_bonds"], 3)
        self.assertEqual(result["summary"]["calculable_ratio"], 0.75)
        self.assertEqual(result["summary"]["median_deviation_bp"], 0)
        self.assertIsNone(result["summary"]["issue_amount_yi"])
        self.assertEqual(result["daily"][1]["rolling_non_market_ratio"], 0.3333)
        self.assertEqual(result["daily"][1]["rolling_overpriced_ratio"], 0.3333)

    def test_market_endpoint_matches_cache_row_count(self):
        client = app_module.app.test_client()
        response = client.get("/api/market?start_date=20260701&end_date=20260710")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        with sqlite3.connect(app_module.CACHE_DB_PATH) as conn:
            expected = conn.execute(
                """
                SELECT COUNT(*) FROM bond_deviations
                WHERE issue_date BETWEEN ? AND ? AND issue_date_rule = ?
                """,
                ("20260701", "20260710", app_module.ISSUE_DATE_RULE_VERSION),
            ).fetchone()[0]

        self.assertEqual(data["summary"]["total_bonds"], expected)
        self.assertEqual(len(data["daily"]), 8)

    def test_date_history_stops_before_query_date(self):
        client = app_module.app.test_client()
        response = client.get("/api/date/20260710")
        self.assertEqual(response.status_code, 200)
        issuer_row = response.get_json()["issuers"][0]

        with sqlite3.connect(app_module.CACHE_DB_PATH) as conn:
            expected = conn.execute(
                """
                SELECT COUNT(*) FROM bond_deviations
                WHERE issuer = ? AND issue_date_rule = ?
                  AND issue_date >= ? AND issue_date <= ?
                  AND COALESCE(is_no_judgement, 0) = 0
                """,
                (
                    issuer_row["issuer"],
                    app_module.ISSUE_DATE_RULE_VERSION,
                    app_module.HISTORY_START_DATE,
                    "20260709",
                ),
            ).fetchone()[0]

        self.assertEqual(issuer_row["history_calculated_bonds"], expected)

    def test_invalid_market_range_returns_400(self):
        response = app_module.app.test_client().get(
            "/api/market?start_date=20260710&end_date=20260701"
        )
        self.assertEqual(response.status_code, 400)

    def test_search_by_bond_name_precedes_same_named_issuer(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = str(Path(temp) / "cache.db")
            conn = init_cache_db(cache_path)
            save_issuer_result(conn, {
                "issuer": "测试发行人",
                "total_bonds": 1,
                "calculated_bonds": 0,
                "non_market_count": 0,
                "overpriced_count": 0,
                "avg_deviation_bp": 0,
                "bonds": [{
                    "bond_symbol": "TEST001",
                    "bond_name": "25测试债",
                    "issuer": "测试发行人",
                    "issue_date": "20260710",
                    "is_no_judgement": True,
                }],
            }, "20260710", "20260710")
            # Simulate the stale empty issuer cache row created by the former raw-input fallback.
            conn.execute(
                """
                INSERT INTO issuer_summary (issuer, issue_date_rule, start_date, end_date)
                VALUES (?, ?, ?, ?)
                """,
                ("25测试债", app_module.ISSUE_DATE_RULE_VERSION, "20260710", "20260710"),
            )
            conn.commit()
            conn.close()

            with patch.object(app_module, "CACHE_DB_PATH", cache_path):
                response = app_module.app.test_client().get("/api/search?q=测试债")

        self.assertEqual(response.status_code, 200)
        results = response.get_json()["results"]
        self.assertEqual(results[0], {
            "issuer": "测试发行人",
            "label": "25测试债",
            "match_type": "bond",
        })
        self.assertIn({
            "issuer": "25测试债",
            "label": "25测试债",
            "match_type": "issuer",
        }, results)

    def test_market_cache_miss_returns_empty_without_waiting(self):
        with patch.object(app_module, "_read_bonds_from_cache", return_value=None), patch.object(
            app_module, "_schedule_background_job", return_value=True
        ):
            response = app_module.app.test_client().get(
                "/api/market?start_date=20260701&end_date=20260710"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["summary"]["total_bonds"], 0)
        self.assertTrue(response.get_json()["_background_refresh"])

    def test_issuer_summary_uses_calculable_denominator_and_absolute_deviation(self):
        bonds = [
            {"issuer": "发行人A", "issue_date": "20260102", "deviation_bp": -5, "is_non_market": True, "is_overpriced": False, "is_no_judgement": False},
            {"issuer": "发行人A", "issue_date": "20260103", "deviation_bp": 7, "is_non_market": False, "is_overpriced": True, "is_no_judgement": False},
            {"issuer": "发行人A", "issue_date": "20260104", "deviation_bp": None, "is_non_market": False, "is_overpriced": False, "is_no_judgement": True},
        ]

        result = app_module._build_issuer_summary_result(bonds, "20260101", "20260131")
        row = result["issuers"][0]

        self.assertEqual(row["total_bonds"], 3)
        self.assertEqual(row["calculated_bonds"], 2)
        self.assertEqual(row["non_market_ratio"], 0.5)
        self.assertEqual(row["overpriced_ratio"], 0.5)
        self.assertEqual(row["avg_deviation_bp"], 1.0)
        self.assertEqual(row["avg_abs_deviation_bp"], 6.0)

        no_sample = app_module._build_issuer_summary_result([
            {"issuer": "发行人B", "issue_date": "20260102", "deviation_bp": None, "is_non_market": False, "is_overpriced": False, "is_no_judgement": True},
        ], "20260101", "20260131")["issuers"][0]
        self.assertIsNone(no_sample["avg_deviation_bp"])
        self.assertIsNone(no_sample["avg_abs_deviation_bp"])

    def test_issuer_summary_endpoint_returns_all_cached_issuers(self):
        response = app_module.app.test_client().get(
            "/api/issuer-summary?start_date=20260701&end_date=20260710"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["summary"]["issuer_count"], len(data["issuers"]))

    def test_date_cache_miss_returns_empty_and_schedules_refresh(self):
        with patch.object(app_module, "_read_bonds_from_cache", return_value=None), patch.object(
            app_module, "_schedule_background_job", return_value=True
        ) as schedule:
            response = app_module.app.test_client().get("/api/date/20260711")
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total_bonds"], 0)
        self.assertTrue(data["_background_refresh"])
        schedule.assert_called_once()

    def test_cache_schema_and_save_include_issue_amount(self):
        with tempfile.TemporaryDirectory() as temp:
            conn = init_cache_db(str(Path(temp) / "cache.db"))
            result = {
                "issuer": "测试发行人",
                "total_bonds": 1,
                "calculated_bonds": 0,
                "non_market_count": 0,
                "overpriced_count": 0,
                "avg_deviation_bp": 0,
                "bonds": [{
                    "bond_symbol": "TEST001",
                    "bond_name": "测试债",
                    "issuer": "测试发行人",
                    "issue_date": "20260710",
                    "issue_amount_wan": 25000,
                    "is_no_judgement": True,
                }],
            }
            save_issuer_result(conn, result, "20260710", "20260710")
            value = conn.execute(
                "SELECT issue_amount_wan FROM bond_deviations WHERE symbol = ?",
                ("TEST001",),
            ).fetchone()[0]
            conn.close()
        self.assertEqual(value, 25000)

    def test_issue_amount_backfill_does_not_create_unpriced_cache_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = str(Path(temp) / "cache.db")
            conn = init_cache_db(cache_path)
            conn.execute(
                """
                INSERT INTO bond_deviations (
                    symbol, issue_date_rule, issue_amount_wan, is_no_judgement
                ) VALUES (?, ?, ?, ?)
                """,
                ("EXISTING", app_module.ISSUE_DATE_RULE_VERSION, None, 1),
            )
            conn.commit()
            conn.close()

            issues = pd.DataFrame([
                {"SYMBOL": "EXISTING", "ISSUE_AMOUNT_WAN": 12000},
                {"SYMBOL": "MISSING", "ISSUE_AMOUNT_WAN": 34000},
            ])
            oracle_conn = MagicMock()
            context = MagicMock()
            context.__enter__.return_value = oracle_conn
            context.__exit__.return_value = False
            with patch.object(app_module, "CACHE_DB_PATH", cache_path), patch.object(
                app_module, "get_connection", return_value=context
            ), patch.object(app_module, "fetch_new_issues", return_value=issues):
                app_module._backfill_issue_amounts("20260710", "20260710")

            conn = sqlite3.connect(cache_path)
            try:
                existing_amount = conn.execute(
                    "SELECT issue_amount_wan FROM bond_deviations WHERE symbol = ?",
                    ("EXISTING",),
                ).fetchone()[0]
                missing_count = conn.execute(
                    "SELECT COUNT(*) FROM bond_deviations WHERE symbol = ?",
                    ("MISSING",),
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(existing_amount, 12000)
        self.assertEqual(missing_count, 0)

    @staticmethod
    def _bond(issue_date, deviation, non_market=False, overpriced=False, no_judgement=False):
        return {
            "issue_date": issue_date,
            "issuer": f"发行人-{issue_date}",
            "deviation_bp": deviation,
            "is_non_market": non_market,
            "is_overpriced": overpriced,
            "is_no_judgement": no_judgement,
        }


if __name__ == "__main__":
    unittest.main()
