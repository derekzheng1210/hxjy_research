# -*- coding: utf-8 -*-
"""二级择券单券持仓占比：信评持仓（亿元）向下取千万整数后 / 债券余额（亿元）。"""
import unittest

from app import holding_ratio_fields


class HoldingRatioFieldsTests(unittest.TestCase):
    def test_floors_to_ten_million(self):
        self.assertEqual(holding_ratio_fields(2.06, 15.0), (2.0, 15.0, 13.33))
        self.assertEqual(holding_ratio_fields(13.88, 600.0), (13.8, 600.0, 2.3))
        self.assertEqual(holding_ratio_fields(0.3, 5.0), (0.3, 5.0, 6.0))

    def test_float_epsilon_does_not_over_floor(self):
        # 0.3*10 的二进制浮点误差不得把 0.3 多砍一档成 0.2
        self.assertEqual(holding_ratio_fields(0.3, 5.0)[0], 0.3)
        self.assertEqual(holding_ratio_fields(2.0, 5.0)[0], 2.0)
        self.assertEqual(holding_ratio_fields(7.19, 100.0)[0], 7.1)

    def test_below_ten_million_floors_to_zero(self):
        holding, outstanding, ratio = holding_ratio_fields(0.05, 4.0)
        self.assertEqual(holding, 0.0)
        self.assertEqual(ratio, 0.0)

    def test_no_holding_when_missing_zero_or_negative(self):
        self.assertEqual(holding_ratio_fields(None, 5.0), (None, 5.0, None))
        self.assertEqual(holding_ratio_fields(0, 5.0), (None, 5.0, None))
        self.assertEqual(holding_ratio_fields(-0.5, 5.0), (None, 5.0, None))

    def test_missing_outstanding_keeps_holding_without_ratio(self):
        self.assertEqual(holding_ratio_fields(2.06, None), (2.0, None, None))
        self.assertEqual(holding_ratio_fields(2.06, 0), (2.0, 0, None))


if __name__ == "__main__":
    unittest.main()
