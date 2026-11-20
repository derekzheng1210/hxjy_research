"""通用表格 xlsx 导出：前端把当前界面筛选后的表头与行 POST 过来，服务端装配工作簿。

供二级择券等客户端筛选型页面使用——筛选/排序逻辑在浏览器里，服务端只负责
把"所见"转成 xlsx，不重复实现筛选。也做防御：行列上限、文件名/工作表名
去非法字符、单元格清洗（openpyxl 遇控制字符会抛异常）。
"""

from __future__ import annotations

import re
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_HEADERS = 200
MAX_ROWS = 50000
MAX_CELL_TEXT = 2000

_HEADER_FONT = Font(bold=True)
_HEADER_FILL = PatternFill("solid", fgColor="EEF2FF")
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center")


def sanitize_filename(name: str, default: str = "导出表格") -> str:
    """去掉路径与 Windows 文件名非法字符，强制 .xlsx 后缀。"""
    name = re.sub(r'[\\/:*?"<>|\r\n]', "_", str(name or "").strip())[:120] or default
    return name if name.lower().endswith(".xlsx") else f"{name}.xlsx"


def sanitize_sheet_name(name: str) -> str:
    """Excel 工作表名限制：无 []:/\\*? 且 ≤31 字符。"""
    sheet = re.sub(r"[\[\]:/\\\\*?]", "", str(name or "").strip())[:31]
    return sheet or "数据"


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return value
    return ILLEGAL_CHARACTERS_RE.sub("", str(value))[:MAX_CELL_TEXT]


def _header_width(text: str) -> float:
    # CJK 按 2 个显示宽度估列宽，再夹在合理区间
    width = sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(text)) + 4
    return max(10, min(48, width))


def build_table_workbook(headers: list[str], rows: list[list], sheet_name: str = "数据") -> BytesIO:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(sheet_name)
    for i, header in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = _header_width(header)
    header_cells = []
    for text in headers:
        cell = WriteOnlyCell(ws, value=str(text))
        cell.font, cell.fill, cell.alignment = _HEADER_FONT, _HEADER_FILL, _HEADER_ALIGNMENT
        header_cells.append(cell)
    ws.append(header_cells)
    width = len(headers)
    for row in rows:
        values = list(row or [])[:width]
        values += [""] * (width - len(values))
        ws.append([_cell(v) for v in values])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
