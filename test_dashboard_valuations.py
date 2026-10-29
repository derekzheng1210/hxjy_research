"""juyuan_update.dashboard_valuations 单元测试（聚源中债估值回退源与补桥）。

覆盖：代码清洗、看板券单收集、valuations.json 补桥（只填空位/新代码接管跳过/
DM 值不动/估值日期切换）、回退文件生成（Oracle 用仿件替身，不连库）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from juyuan_update import dashboard_valuations as dv


class CleanCodeTests(unittest.TestCase):
    def test_clean_code_variants(self):
        self.assertEqual(dv._clean_code("102682456.IB"), "102682456.IB")
        self.assertEqual(dv._clean_code("524876.sz"), "524876.SZ")
        self.assertEqual(dv._clean_code("102682456"), "102682456.IB")
        self.assertEqual(dv._clean_code("245600"), "245600.SH")
        self.assertIsNone(dv._clean_code(None))
        self.assertIsNone(dv._clean_code("  "))


class _TempDataRootMixin:
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.patches = [
            mock.patch.object(dv, "BOND_DASHBOARD_DIR", self.root),
            mock.patch.object(dv, "FALLBACK_PATH", self.root / "oracle_cb_fallback.json"),
            mock.patch.object(dv, "VALUATIONS_PATH", self.root / "valuations.json"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(self._tmp.cleanup)

    def write_json(self, rel: str, payload) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class DashboardBondCodesTests(_TempDataRootMixin, unittest.TestCase):
    def test_reads_bids_and_recommended_with_name_dedup(self):
        self.write_json("store/bids.json", [
            {"bondName": "26长江02", "securityId": "524876.SZ"},
            {"bondName": "26四川路桥MTN002", "securityId": None},
            {"bondName": "无代码券"},
        ])
        self.write_json("recommended.json", [
            {"name": "26四川路桥MTN002", "code": "102682456.IB"},
            {"name": "26长江02", "code": "999999.SH"},  # 同名首见优先，不覆盖
            {"name": "", "code": "102682456.IB"},  # 无名跳过
        ])
        bonds = dv._dashboard_bond_codes()
        self.assertEqual(bonds, {
            "26长江02": "524876.SZ",
            "26四川路桥MTN002": "102682456.IB",
            "无代码券": None,
        })

    def test_missing_files_yield_empty(self):
        self.assertEqual(dv._dashboard_bond_codes(), {})


class ApplyFallbackTests(_TempDataRootMixin, unittest.TestCase):
    def _seed(self, *, meta=None, bonds=None, yields=None):
        self.write_json("valuations.json", {
            "meta": meta if meta is not None else {"date": "2026-10-23"},
            "bonds": bonds if bonds is not None else {},
        })
        self.write_json("oracle_cb_fallback.json", {
            "meta": {"date": "2026-10-22", "source": "聚源"},
            "yields": yields if yields is not None else {},
        })

    def test_fills_only_null_cb_and_marks_basis(self):
        self._seed(bonds={
            "A": {"security_id": "100001.IB", "cb": None, "cb_basis": None, "cs": None, "market": "银行间市场"},
            "B": {"security_id": "200002.SH", "cb": None, "cb_basis": None, "cs": None, "market": "上海证券交易所"},
            "C": {"security_id": "300003.IB", "cb": 1.5, "cb_basis": "行权", "cs": None, "market": "银行间市场"},
        }, yields={"100001.IB": 1.62, "200002.SH": 2.0, "999999.IB": 9.9})
        result = dv.apply_cb_fallback_to_valuations()
        self.assertEqual(result["filled"], 2)
        bonds = json.loads(dv.VALUATIONS_PATH.read_text(encoding="utf-8"))["bonds"]
        self.assertEqual(bonds["A"]["cb"], 1.62)
        self.assertEqual(bonds["A"]["cb_basis"], "聚源到期")
        self.assertEqual(bonds["B"]["cb"], 2.0)
        self.assertEqual(bonds["C"]["cb"], 1.5)  # DM 行权值不动
        self.assertEqual(bonds["C"]["cb_basis"], "行权")
        meta = json.loads(dv.VALUATIONS_PATH.read_text(encoding="utf-8"))["meta"]
        self.assertEqual(meta["cb_fallback"]["filled"], 2)
        self.assertEqual(meta["cb_fallback"]["date"], "2026-10-22")

    def test_dm_present_keeps_meta_date(self):
        self._seed(meta={"date": "2026-10-26"}, bonds={
            "A": {"security_id": "100001.IB", "cb": 1.5, "cb_basis": "到期", "cs": None, "market": "银行间市场"},
            "B": {"security_id": "200002.SH", "cb": None, "cb_basis": None, "cs": None, "market": "上海证券交易所"},
        }, yields={"200002.SH": 2.0})
        result = dv.apply_cb_fallback_to_valuations()
        self.assertEqual(result, {"filled": 1, "dm_valued": 1, "date": "2026-10-26"})
        meta = json.loads(dv.VALUATIONS_PATH.read_text(encoding="utf-8"))["meta"]
        self.assertEqual(meta["date"], "2026-10-26")  # DM 有值时估值日期保持 DM 口径

    def test_dm_all_null_switches_date_to_oracle(self):
        self._seed(meta={"date": "2026-10-23"}, bonds={
            "A": {"security_id": "100001.IB", "cb": None, "cb_basis": None, "cs": None, "market": "银行间市场"},
        }, yields={"100001.IB": 1.62})
        result = dv.apply_cb_fallback_to_valuations()
        self.assertEqual(result["dm_valued"], 0)
        meta = json.loads(dv.VALUATIONS_PATH.read_text(encoding="utf-8"))["meta"]
        self.assertEqual(meta["date"], "2026-10-22")

    def test_skips_when_new_code_took_over(self):
        self._seed(meta={"date": "2026-10-23", "cb_fallback": {"filled": 200}}, bonds={
            "A": {"security_id": "100001.IB", "cb": 1.62, "cb_basis": "聚源到期", "cs": None, "market": "银行间市场"},
        }, yields={"100001.IB": 1.62})
        result = dv.apply_cb_fallback_to_valuations()
        self.assertIn("skipped", str(result))
        # 跳过时不得写文件：内容保持原样（无新增字段）
        data = json.loads(dv.VALUATIONS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["meta"]["cb_fallback"], {"filled": 200})

    def test_skips_without_inputs(self):
        self.assertEqual(dv.apply_cb_fallback_to_valuations().get("skipped"), "valuations.json 缺失或为空")
        self._seed(bonds={}, yields={"100001.IB": 1.62})
        self.assertEqual(dv.apply_cb_fallback_to_valuations().get("skipped"), "valuations.json 缺失或为空")
        self._seed(bonds={"A": {"security_id": "100001.IB", "cb": 1.5, "cb_basis": "到期", "cs": None, "market": ""}},
                   yields={})
        self.assertEqual(dv.apply_cb_fallback_to_valuations().get("skipped"), "oracle_cb_fallback.json 缺失或为空")


class RefreshFallbackTests(_TempDataRootMixin, unittest.TestCase):
    def test_skips_when_dashboard_dir_missing(self):
        with mock.patch.object(dv, "BOND_DASHBOARD_DIR", self.root / "nonexistent"):
            result = dv.refresh_dashboard_cb_fallback()
        self.assertIn("skipped", result)

    def test_skips_when_bond_list_empty(self):
        result = dv.refresh_dashboard_cb_fallback()
        self.assertIn("skipped", result)

    def test_writes_fallback_file_from_oracle(self):
        self.write_json("store/bids.json", [
            {"bondName": "26长江02", "securityId": "524876.SZ"},
            {"bondName": "26四川路桥MTN002", "securityId": "102682456.IB"},
            {"bondName": "无代码券"},
        ])
        self.write_json("recommended.json", [{"name": "26皖能股MTN003", "code": "102682482.IB"}])

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(dv, "connect", return_value=_Conn()), \
                mock.patch.object(dv, "latest_cnbd_valuation_date", return_value="20261023"), \
                mock.patch.object(
                    dv, "fetch_cnbd_yields_by_symbol",
                    side_effect=lambda conn, codes, trade_date: {
                        c: 1.5 + i / 10 for i, c in enumerate(codes)
                    },
                ):
            result = dv.refresh_dashboard_cb_fallback()
        self.assertEqual(result, {"bonds": 4, "valued": 3, "date": "2026-10-23"})
        payload = json.loads(dv.FALLBACK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["meta"]["date"], "2026-10-23")
        self.assertEqual(payload["meta"]["bond_count"], 4)
        self.assertEqual(len(payload["yields"]), 3)


if __name__ == "__main__":
    unittest.main()
