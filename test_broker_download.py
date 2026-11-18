"""经纪商报价快照下载接口测试：xlsx 导出（最新/指定历史时点）与历史时点列表。"""

import json
import os
import unittest
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from openpyxl import load_workbook


TEST_DATA_ROOT = Path(__file__).resolve().parent / ".test_runtime" / uuid.uuid4().hex
os.environ["PORTAL_DATA_ROOT"] = str(TEST_DATA_ROOT)
os.environ["SECRET_KEY"] = "broker-download-test-secret"
os.environ["SITE_PASSWORD"] = "portal-test-password"

from app import app  # noqa: E402
from broker_market import storage  # noqa: E402
from broker_market.export_xlsx import build_quote_workbook, build_range_workbook, snapshot_key  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def make_quote(code, name, bid_yield=None, ofr_yield=2.0, bid_volume=None, ofr_volume=1000.0):
    return {
        "code": code,
        "name": name,
        "bid_yield": bid_yield,
        "ofr_yield": ofr_yield,
        "bid_volume_value": bid_volume,
        "ofr_volume_value": ofr_volume,
        "bid_volume_text": "--",
        "ofr_volume_text": str(int(ofr_volume)),
        "bid_broker": "国际",
        "ofr_broker": "国利",
        "bid_time": "2026-11-13 15:01:01.000",
        "ofr_time": "2026-11-13 15:01:02.000",
        "quote_time": "2026-11-13 15:01:02.000",
        "has_bid": bid_yield is not None,
        "has_offer": ofr_yield is not None,
        "two_sided": bid_yield is not None and ofr_yield is not None,
    }


def snapshot_payload(generated_at, quotes):
    return {"version": "v2", "generated_at": generated_at, "quote_count": len(quotes), "quotes": quotes}


class BrokerDownloadTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        storage.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        storage.MARKET_DIR.mkdir(parents=True, exist_ok=True)

        self.latest = snapshot_payload("2026-11-16 10:01:12", [
            make_quote("132680003.IB", "26山西建投GN001(科创债)", bid_yield=1.9),
            make_quote("240205.IB", "24国开05", bid_yield=None),
        ])
        storage.SNAPSHOT_PATH.write_text(json.dumps(self.latest, ensure_ascii=False), encoding="utf-8")

        self.early = snapshot_payload("2026-11-13 15:01:05", [
            make_quote("132680003.IB", "26山西建投GN001(科创债)", bid_yield=1.9),
        ])
        self.late = snapshot_payload("2026-11-13 16:01:03", [
            make_quote("132680003.IB", "26山西建投GN001(科创债)", bid_yield=1.9),
            make_quote("240205.IB", "24国开05"),
            make_quote("230210.IB", "23国开10", bid_yield=2.1, ofr_yield=2.2),
        ])
        self.prev_early = snapshot_payload("2026-11-12 15:00:00", [
            make_quote("132680003.IB", "26山西建投GN001(科创债)", bid_yield=1.9),
        ])
        self.prev_late = snapshot_payload("2026-11-12 16:00:00", [
            make_quote("132680003.IB", "26山西建投GN001(科创债)", bid_yield=1.9),
            make_quote("240205.IB", "24国开05"),
        ])
        for name, payload in [
            ("20261112_150000.json", self.prev_early),
            ("20261112_160000.json", self.prev_late),
            ("20261113_150105.json", self.early),
            ("20261113_160103.json", self.late),
        ]:
            (storage.HISTORY_DIR / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        self.client = app.test_client()

    def tearDown(self):
        for path in [storage.SNAPSHOT_PATH, *storage.HISTORY_DIR.glob("*.json")]:
            path.unlink(missing_ok=True)
        storage._json_cache.clear()

    def open_workbook(self, response):
        return load_workbook(BytesIO(response.data))

    def assert_download_name(self, response, filename):
        # Flask 对中文文件名按 RFC 2231 输出 filename*=UTF-8''<quoted>
        self.assertIn(f"filename*=UTF-8''{quote(filename)}", response.headers["Content-Disposition"])

    def test_latest_snapshot_download(self):
        response = self.client.get("/broker-data/download/xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, XLSX_MIME)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assert_download_name(response, "经纪商报价_20261116_100112.xlsx")
        wb = self.open_workbook(response)
        ws = wb["经纪商报价"]
        self.assertEqual([cell.value for cell in ws[1]],
                         ["债券代码", "债券简称", "Bid收益率(%)", "Bid量(万)", "Bid经纪商", "Bid时间",
                          "Ofr收益率(%)", "Ofr量(万)", "Ofr经纪商", "Ofr时间", "双边报价"])
        self.assertEqual(ws.max_row, 3)  # 表头 + 2 条报价
        self.assertEqual(ws.cell(2, 1).value, "132680003.IB")
        self.assertEqual(ws.cell(2, 3).value, 1.9)
        self.assertEqual(ws.cell(3, 3).value, None)  # 单边券 Bid 留空
        self.assertEqual(ws.cell(2, 11).value, "是")
        self.assertEqual(ws.cell(3, 11).value, "否")
        note = wb["说明"]
        facts = {note.cell(r, 1).value: note.cell(r, 2).value for r in range(2, note.max_row + 1)}
        self.assertEqual(facts["快照时间"], "2026-11-16 10:01:12")
        self.assertEqual(facts["报价条数"], 2)
        self.assertEqual(facts["双边报价条数"], 1)

    def test_download_history_point(self):
        response = self.client.get("/broker-data/download/xlsx?at=20261113_150105")
        self.assertEqual(response.status_code, 200)
        self.assert_download_name(response, "经纪商报价_20261113_150105.xlsx")
        wb = self.open_workbook(response)
        self.assertEqual(wb["经纪商报价"].max_row, 2)  # 表头 + 1 条

    def test_download_date_takes_last_point_of_day(self):
        response = self.client.get("/broker-data/download/xlsx?date=20261113")
        self.assertEqual(response.status_code, 200)
        self.assert_download_name(response, "经纪商报价_20261113_160103.xlsx")
        wb = self.open_workbook(response)
        self.assertEqual(wb["经纪商报价"].max_row, 4)  # 表头 + 3 条

    def test_download_unknown_or_invalid_params(self):
        self.assertEqual(self.client.get("/broker-data/download/xlsx?at=20990101_000000").status_code, 404)
        self.assertEqual(self.client.get("/broker-data/download/xlsx?at=bad").status_code, 404)
        self.assertEqual(self.client.get("/broker-data/download/xlsx?date=20990101").status_code, 404)
        self.assertEqual(self.client.get("/broker-data/download/xlsx?date=bad").status_code, 400)

    def test_download_without_data(self):
        storage.SNAPSHOT_PATH.write_text(
            json.dumps(snapshot_payload("", []), ensure_ascii=False), encoding="utf-8")
        storage._json_cache.clear()
        response = self.client.get("/broker-data/download/xlsx")
        self.assertEqual(response.status_code, 404)

    def test_history_listing(self):
        response = self.client.get("/api/broker-download/history")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["dates"], ["2026-11-13", "2026-11-12"])
        self.assertEqual(payload["by_date"], {
            "2026-11-13": ["15:01:05", "16:01:03"],
            "2026-11-12": ["15:00:00", "16:00:00"],
        })

    def test_download_range_merges_daily_last(self):
        response = self.client.get("/broker-data/download/xlsx?start=20261112&end=20261113")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, XLSX_MIME)
        self.assert_download_name(response, "经纪商报价区间_20261112_20261113.xlsx")
        wb = self.open_workbook(response)
        ws = wb["经纪商报价"]
        self.assertEqual([cell.value for cell in ws[1]],
                         ["快照时间", "债券代码", "债券简称", "Bid收益率(%)", "Bid量(万)", "Bid经纪商", "Bid时间",
                          "Ofr收益率(%)", "Ofr量(万)", "Ofr经纪商", "Ofr时间", "双边报价"])
        self.assertEqual(ws.max_row, 6)  # 表头 + 11-12最后2条 + 11-13最后3条
        self.assertEqual(ws.cell(2, 1).value, "2026-11-12 16:00:00")
        self.assertEqual(ws.cell(4, 1).value, "2026-11-13 16:01:03")
        self.assertEqual(ws.cell(2, 2).value, "132680003.IB")
        self.assertEqual(ws.cell(2, 4).value, 1.9)
        note = wb["说明"]
        facts = {note.cell(r, 1).value: note.cell(r, 2).value for r in range(2, 10)}
        self.assertEqual(facts["区间范围"], "2026-11-12 至 2026-11-13")
        self.assertEqual(facts["包含快照数"], 2)
        self.assertEqual(facts["报价条数"], 5)
        tail = [(note.cell(r, 1).value, note.cell(r, 2).value) for r in range(11, note.max_row + 1)]
        self.assertEqual(tail, [("2026-11-12 16:00:00", 2), ("2026-11-13 16:01:03", 3)])

    def test_download_range_validation(self):
        base = "/broker-data/download/xlsx?{}"
        self.assertEqual(self.client.get(base.format("start=20261112")).status_code, 400)  # 缺 end
        self.assertEqual(self.client.get(base.format("start=20261112&end=bad")).status_code, 400)
        self.assertEqual(self.client.get(base.format("start=bad&end=20261113")).status_code, 400)
        self.assertEqual(self.client.get(base.format("start=20261113&end=20261112")).status_code, 400)
        self.assertEqual(self.client.get(base.format("start=20261112&end=20261213")).status_code, 400)  # 跨度>31天
        self.assertEqual(self.client.get(base.format("start=20261010&end=20261110")).status_code, 400)  # 跨度31天仍超限
        self.assertEqual(self.client.get(base.format("start=20261101&end=20261130")).status_code, 200)  # 跨度30天且有快照
        self.assertEqual(self.client.get(base.format("start=20260101&end=20260131")).status_code, 404)  # 区间无快照


class ExportWorkbookTests(unittest.TestCase):
    def test_snapshot_key_from_generated_at(self):
        self.assertEqual(snapshot_key({"generated_at": "2026-11-16 10:01:12"}), "20261116_100112")
        self.assertRegex(snapshot_key({"generated_at": "bad"}), r"^\d{8}_\d{6}$")

    def test_build_quote_workbook_numeric_cells(self):
        wb = build_quote_workbook(snapshot_payload("2026-11-16 10:01:12", [
            make_quote("240205.IB", "24国开05", bid_yield=1.95, ofr_yield=2.05, bid_volume=2000.0),
        ]))
        ws = wb["经纪商报价"]
        self.assertEqual(ws.cell(2, 3).value, 1.95)
        self.assertEqual(ws.cell(2, 4).value, 2000.0)
        self.assertEqual(ws.cell(2, 7).value, 2.05)
        self.assertEqual(ws.cell(2, 8).value, 1000.0)
        self.assertEqual(ws.auto_filter.ref, ws.dimensions)

    def test_build_range_workbook_streaming(self):
        wb = build_range_workbook([
            snapshot_payload("2026-11-12 16:00:00", [make_quote("240205.IB", "24国开05")]),
            snapshot_payload("2026-11-13 16:01:03", [make_quote("230210.IB", "23国开10", bid_yield=2.1),
                                                     make_quote("240205.IB", "24国开05")]),
        ])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        wb2 = load_workbook(buf)
        ws = wb2["经纪商报价"]
        self.assertEqual(wb2.sheetnames, ["经纪商报价", "说明"])
        self.assertEqual(ws.max_row, 4)
        self.assertEqual(ws.cell(2, 1).value, "2026-11-12 16:00:00")
        self.assertEqual(ws.cell(3, 12).value, "是")  # 230210.IB 双边
        self.assertEqual(ws.cell(4, 12).value, "否")  # 240205.IB 单边
        self.assertEqual(ws.auto_filter.ref, "A1:L4")


if __name__ == "__main__":
    unittest.main()
