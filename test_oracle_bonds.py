import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


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
from juyuan_update.fund_index_mysql import refresh_fund_index


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


class FundIndexMysqlTests(unittest.TestCase):
    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, sql, args):
            pass

        def fetchall(self):
            return self._rows

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeConnection:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

        def close(self):
            pass

    _CREDS = patch.dict(
        "os.environ", {"FUND_INDEX_DB_USER": "u", "FUND_INDEX_DB_PASSWORD": "p"}
    )

    def test_refresh_writes_frozen_json(self):
        rows = [
            {"TRADE_DT": "20260730", "S_DQ_CLOSE": 123.45},
            {"TRADE_DT": "20260731", "S_DQ_CLOSE": 123.67},
            {"TRADE_DT": "20260801", "S_DQ_CLOSE": 0},        # 非正收盘价剔除
            {"TRADE_DT": "20260731", "S_DQ_CLOSE": 123.99},   # 同日重复取最后一条
        ]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            fund_json = Path(directory) / "fund.json"
            connection = self._FakeConnection(self._FakeCursor(rows))
            with (
                patch.object(config, "STRATEGY_FUND_PRICES_FROZEN", fund_json),
                patch("pymysql.connect", return_value=connection),
                self._CREDS,
            ):
                result = refresh_fund_index()

            self.assertEqual(result["fund_prices"], 2)
            self.assertEqual(result["fund_start"], "2026-07-30")
            self.assertEqual(result["fund_end"], "2026-07-31")
            import json
            saved = json.loads(fund_json.read_text(encoding="utf-8"))
            self.assertEqual(saved[-1], {"date": "2026-07-31", "close": 123.99})

    def test_missing_credentials_raises_clear_error(self):
        import os

        saved = {k: os.environ.pop(k, None) for k in
                 ("FUND_INDEX_DB_USER", "FUND_INDEX_DB_PASSWORD")}
        try:
            with self.assertRaises(RuntimeError) as ctx:
                refresh_fund_index()
            self.assertIn("FUND_INDEX_DB_USER", str(ctx.exception))
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_empty_result_keeps_previous_cache(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            fund_json = Path(directory) / "fund.json"
            fund_json.write_text('[{"date": "2026-07-30", "close": 1.0}]', encoding="utf-8")
            connection = self._FakeConnection(self._FakeCursor([]))
            with (
                patch.object(config, "STRATEGY_FUND_PRICES_FROZEN", fund_json),
                patch("pymysql.connect", return_value=connection),
                self._CREDS,
            ):
                with self.assertRaises(RuntimeError):
                    refresh_fund_index()
            # 查询为空时保留旧缓存
            self.assertIn("2026-07-30", fund_json.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
