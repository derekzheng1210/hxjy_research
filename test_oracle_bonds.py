import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from juyuan_update import config
from juyuan_update.db import _select_cnbd_yield
from juyuan_update.oracle_bonds import (
    INCLUDED_BOND_TYPE2,
    _row_to_bond,
    compare_bond_universes,
    effective_maturity_date,
    is_perpetual_bond,
    remaining_term,
    refresh_oracle_bond_universe,
)
from juyuan_update.unified_excel import import_unified_excel


class ExerciseTermTests(unittest.TestCase):
    def test_expanded_types_exclude_only_project_revenue_note(self):
        self.assertTrue({"311", "321", "611", "612", "621"}.issubset(INCLUDED_BOND_TYPE2))
        self.assertNotIn("1011", INCLUDED_BOND_TYPE2)

    def test_three_plus_two_uses_first_exercise_leg(self):
        effective, source = effective_maturity_date(
            as_of=date(2026, 7, 31),
            start_date="20250101",
            maturity_date="20300101",
            option_memo="3+2",
        )
        self.assertEqual(effective, date(2028, 1, 1))
        self.assertEqual(source, "option_memo_first_leg")
        self.assertAlmostEqual(remaining_term(effective, date(2026, 7, 31)), 1.4219, places=4)

    def test_oracle_exercise_date_wins_over_final_maturity(self):
        effective, source = effective_maturity_date(
            as_of=date(2026, 7, 31),
            start_date="20250101",
            maturity_date="20300101",
            put_date="20280101",
            redeem_date="20290101",
            option_memo="3+2",
        )
        self.assertEqual(effective, date(2028, 1, 1))
        self.assertEqual(source, "oracle_exercise_date")

    def test_plus_n_and_named_perpetual_are_excluded(self):
        self.assertTrue(is_perpetual_bond(option_memo="3+N"))
        self.assertTrue(is_perpetual_bond(name="26某银行永续债01"))
        self.assertFalse(is_perpetual_bond(option_memo="3+2"))

    def test_filtered_row_contains_oracle_exercise_term(self):
        row = (
            "102600001", "001005", "SEC1", "26测试MTN001", "测试公司",
            "641", "20250101", "20300101", "3+2", "回售",
            None, None, 0, 1, "", None, 1, "1", "20",
            "COMP1",
        )
        bond, reason = _row_to_bond(row, "AA+", date(2026, 7, 31))
        self.assertIsNone(reason)
        self.assertEqual(bond["code"], "102600001.IB")
        self.assertEqual(bond["term_source"], "option_memo_first_leg")
        self.assertEqual(bond["ct"], "是")

    def test_future_issue_is_excluded_from_historical_snapshot(self):
        row = (
            "102600002", "001005", "SEC2", "26测试MTN002", "测试公司",
            "641", "20260801", "20300101", "", "",
            None, None, 0, 0, "", None, 1, "1", "20",
            "COMP1",
        )
        bond, reason = _row_to_bond(row, "AA+", date(2026, 7, 31))
        self.assertIsNone(bond)
        self.assertEqual(reason, "not_issued")

    def test_cnbd_valuation_prefers_datasource_one_type_one(self):
        rows = [
            ("001005", "1", "2", 2.50),
            ("001005", "1", "1", 2.45),
            ("001005", "5", "1", 2.40),
        ]
        self.assertEqual(_select_cnbd_yield(rows), 2.45)


class ReconciliationTests(unittest.TestCase):
    def test_comparison_surfaces_large_universe_difference(self):
        result = compare_bond_universes(
            [{"code": "A"}, {"code": "B"}, {"code": "C"}],
            [{"code": "B"}, {"code": "C"}, {"code": "D"}],
        )
        self.assertEqual(result["intersection"], 2)
        self.assertEqual(result["old_only"], 1)
        self.assertEqual(result["new_only"], 1)
        self.assertGreater(result["symmetric_diff_ratio"], 0.1)

    def test_large_first_cutover_writes_candidate_without_overwriting_old_pool(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            bond_json = root / "bond.json"
            candidate_json = root / "candidate.json"
            report_json = root / "report.json"
            bond_json.write_text(
                '{"source_file":"excel:old","bonds":[{"code":"A.IB"},{"code":"B.IB"}]}',
                encoding="utf-8",
            )
            new_bonds = [{"code": "C.IB"}]
            with (
                patch.object(config, "BOND_STATIC_JSON", bond_json),
                patch.object(config, "ORACLE_BOND_CANDIDATE_JSON", candidate_json),
                patch.object(config, "ORACLE_BOND_RECONCILIATION_JSON", report_json),
                patch("juyuan_update.oracle_bonds.build_full_oracle_universe", return_value=(new_bonds, {"selected": 1})),
                patch("juyuan_update.oracle_bonds.FORCE_SWITCH", False),
                patch("juyuan_update.oracle_bonds.MAX_SWITCH_DIFF_RATIO", 0.1),
            ):
                result = refresh_oracle_bond_universe(object(), "20260731")
            self.assertFalse(result["applied"])
            self.assertIn("A.IB", bond_json.read_text(encoding="utf-8"))
            self.assertIn("C.IB", candidate_json.read_text(encoding="utf-8"))
            self.assertTrue(report_json.exists())


class FundOnlyExcelTests(unittest.TestCase):
    def test_excel_with_only_sheet1_updates_fund_index(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            workbook_path = root / "fund.xlsx"
            fund_json = root / "fund.json"
            bond_json = root / "bond.json"
            bond_json.write_text('{"bonds":[{"code":"KEEP.IB"}]}', encoding="utf-8")
            wb = Workbook()
            ws = wb.active
            ws.title = "Sheet1"
            ws.append([None, "日期", "收盘价"])
            ws.append([None, "2026-07-30", 123.45])
            ws.append([None, "2026-07-31", 123.67])
            wb.save(workbook_path)

            with (
                patch.object(config, "STRATEGY_FUND_PRICES_FROZEN", fund_json),
                patch.object(config, "BOND_STATIC_JSON", bond_json),
            ):
                result = import_unified_excel(workbook_path)

            self.assertEqual(result["fund_prices"], 2)
            self.assertIn("KEEP.IB", bond_json.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
