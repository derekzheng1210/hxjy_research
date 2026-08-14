import unittest
import sqlite3

import pandas as pd

from primary_market_pricing.calculator import (
    _calculate_single_bond_with_cache,
    is_broker_subordinated_bond,
)
from primary_market_pricing.migrate_cache_v6_to_v7_broker_subordinated import (
    _add_cached_type_mismatches,
)


class FakeCache:
    def get_implied_ratings(self, secodes):
        return {str(secode): "AA+" for secode in secodes}

    def get_nearest_curve_date(self, issue_date, curve_code):
        return issue_date

    def get_curve(self, curve_code, curve_date):
        return [(1.0, 2.0), (3.0, 2.0), (5.0, 2.0)]


def outstanding(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "SECODE", "SYMBOL", "BONDSNAME", "STARTDATE", "RAISEMODE",
            "CVTBDEXPIREMEMP", "EXCHANGE",
        ],
    )


def calculate(issuer, bond_name, rows, valuations):
    return _calculate_single_bond_with_cache(
        cache=FakeCache(),
        bond_symbol="TARGET",
        bond_name=bond_name,
        issuer=issuer,
        coupon_rate=2.5,
        issue_date="20260101",
        maturity_year=3.0,
        rating="AA+",
        raise_mode="public",
        outstanding=outstanding(rows),
        valuations=valuations,
        exchange="001006",  # Deliberately non-exchange: broker classification must not use it.
    )


class BrokerSubordinatedReferenceRuleTests(unittest.TestCase):
    def test_migration_repairs_cached_ordinary_c_bond_despite_legacy_exchange_match(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE bond_deviations (
                symbol TEXT, bond_name TEXT, issuer TEXT, bond_type TEXT,
                issue_date_rule TEXT
            )
        """)
        conn.execute(
            "INSERT INTO bond_deviations VALUES (?, ?, ?, ?, ?)",
            ("245641", "26兴业C2", "兴业证券股份有限公司", "ordinary", "v7"),
        )
        metadata = {
            "245641": {
                "symbol": "245641", "name": "26兴业C2",
                "issuer": "兴业证券股份有限公司", "raise_modes": {"1"},
                "start_date": "20260715", "maturity_date": "20290715",
                "old": True, "new": True, "exchange": "001002",
            }
        }
        changed = {}

        mismatches = _add_cached_type_mismatches(conn, "v7", metadata, changed)

        self.assertEqual(mismatches, 1)
        self.assertIn("245641", changed)
        conn.close()

    def test_security_issuer_terminal_c_number_is_broker_subordinated(self):
        self.assertTrue(is_broker_subordinated_bond("兴业证券股份有限公司", "26兴业C1"))
        self.assertTrue(is_broker_subordinated_bond("兴业证券股份有限公司", "26兴业c12"))

    def test_cp_and_non_security_issuer_are_not_broker_subordinated(self):
        self.assertFalse(is_broker_subordinated_bond("兴业证券股份有限公司", "26兴业CP001"))
        self.assertFalse(is_broker_subordinated_bond("测试投资有限公司", "26测试C1"))
        self.assertFalse(is_broker_subordinated_bond("兴业证券股份有限公司", "26兴业C1A"))

    def test_broker_subordinated_bond_only_uses_another_broker_subordinated_bond(self):
        result = calculate(
            "测试证券股份有限公司",
            "26测试C2",
            [
                ("1", "C_FAR", "25测试C1", "20250101", "public", None, "001006"),
                ("2", "ORD_NEAR", "25测试01", "20250101", "public", None, "001002"),
            ],
            {
                "C_FAR": {"term": 5.0, "yield": 2.3},
                "ORD_NEAR": {"term": 3.0, "yield": 2.8},
            },
        )
        self.assertEqual(result["bond_type"], "broker_subordinated")
        self.assertEqual(result["ref_bond_symbol"], "C_FAR")

    def test_broker_subordinated_bond_without_peer_has_no_reference(self):
        result = calculate(
            "测试证券股份有限公司",
            "26测试C2",
            [("1", "ORD_NEAR", "25测试01", "20250101", "public", None, "001002")],
            {"ORD_NEAR": {"term": 3.0, "yield": 2.8}},
        )
        self.assertIsNone(result)

    def test_ordinary_bond_excludes_broker_subordinated_candidate(self):
        result = calculate(
            "测试证券股份有限公司",
            "26测试01",
            [
                ("1", "C_NEAR", "25测试C1", "20250101", "public", None, "001006"),
                ("2", "ORD_FAR", "25测试02", "20250101", "public", None, "001002"),
            ],
            {
                "C_NEAR": {"term": 3.0, "yield": 2.3},
                "ORD_FAR": {"term": 5.0, "yield": 2.8},
            },
        )
        self.assertEqual(result["bond_type"], "ordinary")
        self.assertEqual(result["ref_bond_symbol"], "ORD_FAR")


if __name__ == "__main__":
    unittest.main()
