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
    build_incremental_oracle_universe,
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

    def test_passed_exercise_rolls_to_final_maturity(self):
        """3+2 行权日已过但未行权（仍有余额）：期限落到最终到期日，保留在池内。"""
        effective, source = effective_maturity_date(
            as_of=date(2026, 9, 16),
            start_date="20230310",
            maturity_date="20280310",
            option_dates=["20260310"],
            option_memo="3+2",
        )
        self.assertEqual(effective, date(2028, 3, 10))
        self.assertEqual(source, "maturity_date")

    def test_passed_exercise_rolls_to_next_future_option_date(self):
        """多行权窗口（如 3+2+2）：首窗已过则推进到下一个未到的行权日。"""
        effective, source = effective_maturity_date(
            as_of=date(2026, 9, 16),
            start_date="20210310",
            maturity_date="20310310",
            option_dates=["20240310", "20270310"],
            option_memo="3+2+2",
        )
        self.assertEqual(effective, date(2027, 3, 10))
        self.assertEqual(source, "oracle_exercise_date")

    def test_row_with_passed_put_keeps_maturity_term(self):
        row = (
            "115010", "001002", "SEC3", "23测试01", "测试公司",
            "621", "20230310", "20280310", "3+2", "回售",
            ("20260310",), None, 0, 0, "", None, 1, "1", "20",
            "COMP1", 8.5,
        )
        bond, reason = _row_to_bond(row, "AA", date(2026, 9, 16))
        self.assertIsNone(reason)
        self.assertEqual(bond["term_source"], "maturity_date")
        self.assertEqual(bond["outstanding_amount"], 8.5)
        self.assertEqual(bond["effective_maturity_date"], "2028-03-10")
        self.assertGreater(bond["term"], 1.0)
        self.assertLess(bond["term"], 2.0)

    def test_plus_n_and_named_perpetual_are_excluded(self):
        self.assertTrue(is_perpetual_bond(option_memo="3+N"))
        self.assertTrue(is_perpetual_bond(name="26某银行永续债01"))
        self.assertFalse(is_perpetual_bond(option_memo="3+2"))

    def test_filtered_row_contains_oracle_exercise_term(self):
        row = (
            "102600001", "001005", "SEC1", "26测试MTN001", "测试公司",
            "641", "20250101", "20300101", "3+2", "回售",
            None, None, 0, 1, "", None, 1, "1", "20",
            "COMP1", 15.0,
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
            "COMP1", 5.0,
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

    def test_force_switch_applies_large_diff(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            bond_json = root / "bond.json"
            report_json = root / "report.json"
            bond_json.write_text(
                '{"source_file":"excel:old","bonds":[{"code":"A.IB"},{"code":"B.IB"}]}',
                encoding="utf-8",
            )
            new_bonds = [{"code": "C.IB"}]
            with (
                patch.object(config, "BOND_STATIC_JSON", bond_json),
                patch.object(config, "ORACLE_BOND_CANDIDATE_JSON", root / "candidate.json"),
                patch.object(config, "ORACLE_BOND_RECONCILIATION_JSON", report_json),
                patch("juyuan_update.oracle_bonds.build_full_oracle_universe", return_value=(new_bonds, {"selected": 1})),
                patch("juyuan_update.oracle_bonds.FORCE_SWITCH", False),
                patch("juyuan_update.oracle_bonds.MAX_SWITCH_DIFF_RATIO", 0.1),
            ):
                result = refresh_oracle_bond_universe(object(), "20260731", force=True)
            self.assertTrue(result["applied"])
            self.assertTrue(result["forced"])
            self.assertIn("C.IB", bond_json.read_text(encoding="utf-8"))

    def test_incremental_reprocesses_passed_exercise_bonds(self):
        """存量券行权窗口已过：重新推导到下一里程碑，而不是按过期行权日剔除。"""
        current = {
            "oracle_watermark": "2026-09-16 08:30:00",
            "bonds": [{
                "code": "115010.SH", "secode": "SEC3", "name": "23测试01",
                "effective_maturity_date": "2026-03-10", "term": -0.18,
                "term_source": "oracle_exercise_date",
            }],
        }
        row = (
            "115010", "001002", "SEC3", "23测试01", "测试公司",
            "621", "20230310", "20280310", "3+2", "回售",
            ("20260310",), None, 0, 0, "", None, 1, "1", "20",
            "COMP1", 8.5,
        )
        with (
            patch("juyuan_update.oracle_bonds._fetch_changed_secodes", return_value=set()),
            patch("juyuan_update.oracle_bonds._fetch_rows_by_secode", return_value={"SEC3": row}),
            patch("juyuan_update.oracle_bonds._fetch_latest_ratings", return_value={"SEC3": "AA"}),
            patch("juyuan_update.oracle_bonds._fetch_entity_types", return_value={}),
        ):
            bonds, counts = build_incremental_oracle_universe(object(), "20260916", current)
        self.assertEqual(counts.get("incremental_passed_exercise"), 1)
        self.assertEqual([bond["code"] for bond in bonds], ["115010.SH"])
        self.assertEqual(bonds[0]["term_source"], "maturity_date")
        self.assertGreater(bonds[0]["term"], 1.0)

    def test_incremental_removes_fully_matured_bond(self):
        """已到期兑付的券在重推导后被移除，不再滞留池内。"""
        current = {
            "oracle_watermark": "2026-09-16 08:30:00",
            "bonds": [{
                "code": "115010.SH", "secode": "SEC3", "name": "23测试01",
                "effective_maturity_date": "2026-03-10", "term": -0.18,
                "term_source": "oracle_exercise_date",
            }],
        }
        row = (
            "115010", "001002", "SEC3", "23测试01", "测试公司",
            "621", "20230310", "20260901", "", "",
            ("20260310",), None, 0, 0, "", None, 1, "1", "20",
            "COMP1", 8.5,
        )
        with (
            patch("juyuan_update.oracle_bonds._fetch_changed_secodes", return_value=set()),
            patch("juyuan_update.oracle_bonds._fetch_rows_by_secode", return_value={"SEC3": row}),
            patch("juyuan_update.oracle_bonds._fetch_latest_ratings", return_value={"SEC3": "AA"}),
            patch("juyuan_update.oracle_bonds._fetch_entity_types", return_value={}),
        ):
            bonds, counts = build_incremental_oracle_universe(object(), "20260916", current)
        self.assertEqual(bonds, [])
        self.assertEqual(counts.get("term_below_minimum"), 1)


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
