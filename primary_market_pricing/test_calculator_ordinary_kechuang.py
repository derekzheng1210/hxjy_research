import unittest

import pandas as pd

from primary_market_pricing.calculator import _calculate_single_bond_with_cache


class FakeCache:
    def get_implied_ratings(self, secodes):
        return {str(secode): "AA+" for secode in secodes}

    def get_nearest_curve_date(self, issue_date, curve_code):
        return issue_date

    def get_curve(self, curve_code, curve_date):
        return [(1.0, 2.0), (3.0, 2.0), (5.0, 2.0)]


def outstanding(rows):
    return pd.DataFrame(rows, columns=[
        "SECODE", "SYMBOL", "BONDSNAME", "STARTDATE", "RAISEMODE",
        "CVTBDEXPIREMEMP", "EXCHANGE",
    ])


class OrdinaryKechuangReferenceTests(unittest.TestCase):
    def calculate(self, rows, valuations):
        return _calculate_single_bond_with_cache(
            cache=FakeCache(),
            bond_symbol="TARGET",
            bond_name="26TESTMTN003",
            issuer="TEST_ISSUER",
            coupon_rate=2.5,
            issue_date="20260101",
            maturity_year=3.0,
            rating="AA+",
            raise_mode="PUBLIC",
            outstanding=outstanding(rows),
            valuations=valuations,
            exchange="",
        )

    def test_prefers_ordinary_reference_over_closer_exchange_kechuang_bond(self):
        result = self.calculate(
            [
                ("1", "K_NEAR", "25TESTK1", "20250101", "PUBLIC", None, "001002"),
                ("2", "ORD_FAR", "25TESTMTN001", "20250101", "PUBLIC", None, "001002"),
            ],
            {
                "K_NEAR": {"term": 3.0, "yield": 2.3},
                "ORD_FAR": {"term": 5.0, "yield": 2.8},
            },
        )
        self.assertEqual(result["ref_bond_symbol"], "ORD_FAR")
        self.assertFalse(result["ordinary_fallback_to_exchange_kechuang"])
        self.assertEqual(result["fair_price"], 2.8)

    def test_uses_exchange_kechuang_bond_only_as_fallback_and_adds_5bp(self):
        result = self.calculate(
            [("1", "K_NEAR", "25TESTK1", "20250101", "PUBLIC", None, "001002")],
            {"K_NEAR": {"term": 3.0, "yield": 2.3}},
        )
        self.assertEqual(result["ref_bond_symbol"], "K_NEAR")
        self.assertTrue(result["ordinary_fallback_to_exchange_kechuang"])
        self.assertEqual(result["fair_price"], 2.35)

    def test_special_bond_type_does_not_use_ordinary_kechuang_rule(self):
        result = _calculate_single_bond_with_cache(
            cache=FakeCache(),
            bond_symbol="TARGET",
            bond_name="26TESTY1",
            issuer="TEST_ISSUER",
            coupon_rate=2.5,
            issue_date="20260101",
            maturity_year=3.0,
            rating="AA+",
            raise_mode="PUBLIC",
            outstanding=outstanding([
                ("1", "K_NEAR", "25TESTK1", "20250101", "PUBLIC", "3+N", "001002"),
            ]),
            valuations={"K_NEAR": {"term": 3.0, "yield": 2.3}},
            cvtbd_expire="3+N",
            exchange="",
        )
        self.assertEqual(result["ref_bond_symbol"], "K_NEAR")
        self.assertFalse(result["ordinary_fallback_to_exchange_kechuang"])
        self.assertEqual(result["fair_price"], 2.3)


if __name__ == "__main__":
    unittest.main()
