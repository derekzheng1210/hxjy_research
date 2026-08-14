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


def make_outstanding(rows):
    return pd.DataFrame(rows, columns=[
        "SECODE", "SYMBOL", "BONDSNAME", "STARTDATE", "RAISEMODE",
        "CVTBDEXPIREMEMP", "EXCHANGE",
    ])


class KechuangReferenceRuleTests(unittest.TestCase):
    def calculate(self, outstanding, valuations):
        return _calculate_single_bond_with_cache(
            cache=FakeCache(), bond_symbol="TARGET", bond_name="26测试K1",
            issuer="测试发行人", coupon_rate=2.5, issue_date="20260101",
            maturity_year=3.0, rating="AA+", raise_mode="公募",
            outstanding=outstanding, valuations=valuations, exchange="001002",
        )

    def test_prefers_nearby_exchange_kechuang_bond(self):
        outstanding = make_outstanding([
            ("1", "K_NEAR", "25测试K1", "20250101", "公募", None, "001002"),
            ("2", "ORD_NEAR", "25测试债", "20250101", "公募", None, "001002"),
        ])
        result = self.calculate(outstanding, {
            "K_NEAR": {"term": 2.0, "yield": 2.3},
            "ORD_NEAR": {"term": 3.0, "yield": 2.8},
        })
        self.assertEqual(result["ref_bond_symbol"], "K_NEAR")
        self.assertFalse(result["kechuang_fallback_to_ordinary"])
        self.assertEqual(result["fair_price"], 2.3)

    def test_falls_back_to_ordinary_bond_and_lowers_fair_price_5bp(self):
        outstanding = make_outstanding([
            ("1", "K_FAR", "25测试K1", "20250101", "公募", None, "001002"),
            ("2", "ORD_NEAR", "25测试债", "20250101", "公募", None, "001002"),
        ])
        result = self.calculate(outstanding, {
            "K_FAR": {"term": 5.1, "yield": 2.3},
            "ORD_NEAR": {"term": 3.0, "yield": 2.8},
        })
        self.assertEqual(result["ref_bond_symbol"], "ORD_NEAR")
        self.assertTrue(result["kechuang_fallback_to_ordinary"])
        self.assertEqual(result["fair_price"], 2.75)

    def test_uses_far_kechuang_bond_without_5bp_adjustment_when_it_is_closest(self):
        outstanding = make_outstanding([
            ("1", "K_FAR", "25测试K1", "20250101", "公募", None, "001002"),
            ("2", "ORD_FARTHER", "25测试债", "20250101", "公募", None, "001002"),
        ])
        result = self.calculate(outstanding, {
            "K_FAR": {"term": 1.0, "yield": 2.3},
            "ORD_FARTHER": {"term": 6.0, "yield": 2.8},
        })
        self.assertEqual(result["ref_bond_symbol"], "K_FAR")
        self.assertFalse(result["kechuang_fallback_to_ordinary"])
        self.assertEqual(result["fair_price"], 2.3)

    def test_private_bond_prefers_nearby_private_kechuang_reference(self):
        outstanding = make_outstanding([
            ("1", "K_NEAR", "25测试K1", "20250101", "2", None, "001002"),
            ("2", "ORD_NEAR", "25测试债", "20250101", "2", None, "001002"),
        ])
        result = _calculate_single_bond_with_cache(
            cache=FakeCache(), bond_symbol="TARGET", bond_name="26测试K1",
            issuer="测试发行人", coupon_rate=2.5, issue_date="20260101",
            maturity_year=3.0, rating="AA+", raise_mode="2",
            outstanding=outstanding,
            valuations={
                "K_NEAR": {"term": 2.0, "yield": 2.3},
                "ORD_NEAR": {"term": 3.0, "yield": 2.8},
            },
            exchange="001002",
        )
        self.assertEqual(result["ref_bond_symbol"], "K_NEAR")
        self.assertFalse(result["kechuang_fallback_to_ordinary"])
        self.assertEqual(result["fair_price"], 2.3)

    def test_private_bond_falls_back_to_ordinary_and_lowers_fair_price_5bp(self):
        outstanding = make_outstanding([
            ("1", "K_FAR", "25测试K1", "20250101", "2", None, "001002"),
            ("2", "ORD_NEAR", "25测试债", "20250101", "2", None, "001002"),
        ])
        result = _calculate_single_bond_with_cache(
            cache=FakeCache(), bond_symbol="TARGET", bond_name="26测试K1",
            issuer="测试发行人", coupon_rate=2.5, issue_date="20260101",
            maturity_year=3.0, rating="AA+", raise_mode="2",
            outstanding=outstanding,
            valuations={
                "K_FAR": {"term": 5.1, "yield": 2.3},
                "ORD_NEAR": {"term": 3.0, "yield": 2.8},
            },
            exchange="001002",
        )
        self.assertEqual(result["ref_bond_symbol"], "ORD_NEAR")
        self.assertTrue(result["kechuang_fallback_to_ordinary"])
        self.assertEqual(result["fair_price"], 2.75)

    def test_private_bond_without_ordinary_reference_is_unpriced(self):
        outstanding = make_outstanding([
            ("1", "K_FAR", "25测试K1", "20250101", "2", None, "001002"),
        ])
        result = _calculate_single_bond_with_cache(
            cache=FakeCache(), bond_symbol="TARGET", bond_name="26测试K1",
            issuer="测试发行人", coupon_rate=2.5, issue_date="20260101",
            maturity_year=3.0, rating="AA+", raise_mode="2",
            outstanding=outstanding,
            valuations={"K_FAR": {"term": 5.1, "yield": 2.3}},
            exchange="001002",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
