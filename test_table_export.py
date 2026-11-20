"""通用表格 xlsx 导出端点测试：/api/export/table-xlsx（前端 POST 当前筛选结果装配工作簿）。"""

import os
import unittest
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from openpyxl import load_workbook


TEST_DATA_ROOT = Path(__file__).resolve().parent / ".test_runtime" / uuid.uuid4().hex
os.environ["PORTAL_DATA_ROOT"] = str(TEST_DATA_ROOT)
os.environ["SECRET_KEY"] = "table-export-test-secret"
os.environ["SITE_PASSWORD"] = "portal-test-password"

from app import app  # noqa: E402
from table_export import sanitize_filename, sanitize_sheet_name  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TableExportHelperTests(unittest.TestCase):
    def test_filename_and_sheet_sanitize(self):
        self.assertEqual(sanitize_filename("a/b:*?.xlsx"), "a_b___.xlsx")
        self.assertEqual(sanitize_filename("择券"), "择券.xlsx")
        self.assertEqual(sanitize_filename("  "), "导出表格.xlsx")
        self.assertEqual(sanitize_sheet_name("[持仓:数据*?]"), "持仓数据")
        self.assertEqual(sanitize_sheet_name(""), "数据")


class TableExportEndpointTests(unittest.TestCase):
    def post(self, payload):
        return app.test_client().post("/api/export/table-xlsx", json=payload)

    def test_roundtrip_values(self):
        resp = self.post({
            "filename": "择券导出",
            "sheet": "持仓",
            "headers": ["代码", "简称", "中债-Bid(BP)", "数量"],
            "rows": [
                ["250210.IB", "25测试MTN001", 10.0, 3],
                [None, True, "a,b", None],
            ],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, XLSX_MIME)
        # 中文名经 RFC5987 编码进 filename*（浏览器按它落盘）
        self.assertIn(quote("择券导出.xlsx"), resp.headers["Content-Disposition"])
        ws = load_workbook(BytesIO(resp.data))["持仓"]
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(rows[0], ("代码", "简称", "中债-Bid(BP)", "数量"))
        self.assertEqual(rows[1][0], "250210.IB")
        self.assertEqual(rows[1][2], 10.0)  # 数字保真（可再计算）
        self.assertEqual(rows[2][1], "是")  # bool → 是/否
        self.assertEqual(rows[2][2], "a,b")

    def test_short_row_padded_long_row_trimmed(self):
        resp = self.post({
            "headers": ["A", "B", "C"],
            "rows": [["x"], ["x", "y", "z", "多余"]],
        })
        self.assertEqual(resp.status_code, 200)
        ws = load_workbook(BytesIO(resp.data)).active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        # 空串补位在 xlsx 中读回为 None（空单元格），与导出语义等价
        self.assertEqual(rows[0], ("x", None, None))
        self.assertEqual(rows[1], ("x", "y", "z"))

    def test_control_characters_removed(self):
        resp = self.post({"headers": ["A"], "rows": [["a\x00b\x1fc"]]})
        self.assertEqual(resp.status_code, 200)
        ws = load_workbook(BytesIO(resp.data)).active
        self.assertEqual(ws.cell(row=2, column=1).value, "abc")

    def test_rejections(self):
        cases = [
            {"headers": [], "rows": []},
            {"headers": "代码", "rows": []},           # 非列表
            {"headers": ["A", ""], "rows": []},         # 空表头
            {"headers": ["A"], "rows": "not-a-list"},
            {"headers": [1, 2], "rows": []},            # 表头非字符串
        ]
        for payload in cases:
            resp = self.post(payload)
            self.assertEqual(resp.status_code, 400, payload)
            self.assertIn("error", resp.get_json())


if __name__ == "__main__":
    unittest.main()
