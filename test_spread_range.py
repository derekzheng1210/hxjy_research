"""存量债利差监控·自定义区间查询测试：服务端计算（历史缓存）与 xlsx 导出。"""

import json
import os
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook


TEST_DATA_ROOT = Path(__file__).resolve().parent / ".test_runtime" / uuid.uuid4().hex
os.environ["PORTAL_DATA_ROOT"] = str(TEST_DATA_ROOT)
os.environ["SECRET_KEY"] = "spread-range-test-secret"
os.environ["SITE_PASSWORD"] = "portal-test-password"

import spread_range  # noqa: E402
from app import app  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def snap(yield_, curve, spread):
    return {"yield": yield_, "curve_yield": curve, "spread": spread}


class SpreadRangeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.hist_path = self.dir / "spread_history_cache.json"
        self.data_path = self.dir / "spread_data.js"
        self._orig = (spread_range.HISTORY_PATH, spread_range.DATA_JS_PATH)
        spread_range.HISTORY_PATH = self.hist_path
        spread_range.DATA_JS_PATH = self.data_path
        spread_range._CACHE = None
        # 交易日历：周五 0102、周一 0105、周二 0106（0103/0104 周末无快照）
        history = {
            "generated_at": "2026-01-06 08:46:00",
            "dates": {
                "20260102": {
                    "012500480": snap(2.10, 2.00, 10.0),
                    "012500483": snap(3.20, 3.00, 20.0),
                    "012500484": snap(1.50, 1.40, 10.0),  # 期末缺券：应被剔除
                },
                "20260105": {
                    "012500480": snap(2.30, 2.00, 30.0),   # 走扩 +20
                    "012500483": snap(3.10, 3.00, 10.0),   # 收窄 -10
                    "012500499": snap(4.00, 3.90, 10.0),   # 期初缺券：应被剔除
                },
                "20260106": {
                    "012500480": snap(2.30, 2.00, 30.0),
                    "012500483": snap(3.10, 3.00, 10.0),
                },
            },
        }
        self.hist_path.write_text(json.dumps(history), encoding="utf-8")
        payload = {
            "update_time": "2026-01-06 08:46:00",
            "data": [
                {"code": "012500480.IB", "raw_code": "012500480", "name": "26测试MTN001",
                 "implied_rating": "AA+", "term": 1.5, "curve_name": "中短票AA+", "is_holding": True},
                {"code": "012500483.IB", "raw_code": "012500483", "name": "26测试MTN002",
                 "implied_rating": "AA", "term": 3.0, "curve_name": "中短票AA", "is_holding": False},
                # 012500499 不在当前清单（已到期券）：仍应入表，名称等静态信息为空
            ],
        }
        self.data_path.write_text(
            "var SPREAD_DATA = " + json.dumps(payload, ensure_ascii=False) + ";", encoding="utf-8"
        )

    def tearDown(self):
        spread_range.HISTORY_PATH, spread_range.DATA_JS_PATH = self._orig
        spread_range._CACHE = None
        self._tmp.cleanup()

    def test_range_compute_and_join(self):
        r = spread_range.compute_range("20260102", "20260105")
        self.assertEqual((r["start_used"], r["end_used"]), ("20260102", "20260105"))
        codes = {row["raw_code"] for row in r["rows"]}
        # 两端都有利差的券才入表：012500484（期末缺）、012500499（期初缺）剔除
        self.assertEqual(codes, {"012500480", "012500483"})
        by_code = {row["raw_code"]: row for row in r["rows"]}
        widen = by_code["012500480"]
        self.assertEqual(widen["change_bp"], 20.0)
        self.assertEqual(widen["spread_start"], 10.0)
        self.assertEqual(widen["spread_end"], 30.0)
        self.assertEqual(widen["name"], "26测试MTN001")
        self.assertTrue(widen["is_holding"])
        # 按 |change_bp| 降序
        self.assertEqual([row["raw_code"] for row in r["rows"]], ["012500480", "012500483"])

    def test_weekend_snaps_to_previous_trading_day(self):
        r = spread_range.compute_range("20260104", "20260105")  # 周日请求 → 对齐周五
        self.assertEqual((r["start_used"], r["end_used"]), ("20260102", "20260105"))

    def test_same_trading_day_rejected(self):
        with self.assertRaises(spread_range.RangeError):
            spread_range.compute_range("20260103", "20260104")  # 都对齐到 0102

    def test_parameter_validation(self):
        for s, e in [("2026010", "20260105"), ("20260105", "20260102"), ("20250101", "20260105")]:
            with self.assertRaises(spread_range.RangeError):
                spread_range.compute_range(s, e)

    def test_cache_reloads_on_mtime_change(self):
        spread_range.compute_range("20260102", "20260105")
        history = json.loads(self.hist_path.read_text(encoding="utf-8"))
        history["dates"]["20260107"] = {"012500480": snap(2.4, 2.0, 40.0)}
        self.hist_path.write_text(json.dumps(history), encoding="utf-8")
        # 同秒内两次写入 mtime 可能不变（NTFS 粒度），显式前拨保证失效判定触发
        os.utime(self.hist_path, (946684800, 946684800))
        r = spread_range.compute_range("20260102", "20260107")
        by_code = {row["raw_code"]: row for row in r["rows"]}
        self.assertEqual(by_code["012500480"]["change_bp"], 30.0)

    def test_workbook_export(self):
        result = spread_range.compute_range("20260102", "20260105")
        buf = spread_range.build_range_workbook(result)
        wb = load_workbook(buf)
        ws = wb["区间利差"]
        header = [c.value for c in next(ws.iter_rows(max_row=1))]
        self.assertEqual(header, spread_range.XLSX_HEADERS)
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        self.assertEqual(len(rows), 2)
        first = rows[0]  # |变化|最大：012500480 走扩20bp
        self.assertEqual(first[0], "012500480.IB")
        self.assertEqual(first[1], "26测试MTN001")
        self.assertEqual(first[7], 20.0)
        self.assertEqual(first[10], "是")

    def test_api_range_and_xlsx(self):
        client = app.test_client()
        resp = client.get("/api/spread-monitor/range?start=20260102&end=20260105")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["start_used"], "20260102")
        self.assertEqual(len(body["rows"]), 2)

        bad = client.get("/api/spread-monitor/range?start=20260105&end=20260102")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("error", bad.get_json())

        xresp = client.get("/spread-monitor/range/xlsx?start=20260102&end=20260105")
        self.assertEqual(xresp.status_code, 200)
        self.assertEqual(xresp.mimetype, XLSX_MIME)
        # 中文名经 RFC5987 编码进 filename*（浏览器按它落盘），ASCII 回退名只保留日期段
        from urllib.parse import quote
        self.assertIn(quote("利差区间_20260102-20260105.xlsx"), xresp.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
