"""经纪商报价快照导出为 Excel，供同事经门户下载链接直接获取。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from broker_market.storage import finite_number

HEADERS = [
    "债券代码", "债券简称",
    "Bid收益率(%)", "Bid量(万)", "Bid经纪商", "Bid时间",
    "Ofr收益率(%)", "Ofr量(万)", "Ofr经纪商", "Ofr时间",
    "双边报价",
]

RANGE_HEADERS = ["快照时间", *HEADERS]

_COLUMN_WIDTHS = [14, 22, 12, 10, 10, 20, 12, 10, 10, 20, 8]
_RANGE_COLUMN_WIDTHS = [19, *_COLUMN_WIDTHS]

_HEADER_FONT = Font(bold=True)
_HEADER_FILL = PatternFill("solid", fgColor="EEF2FF")
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center")


def snapshot_key(payload: dict[str, Any]) -> str:
    """快照抓取时刻键（YYYYMMDD_HHMMSS），用于下载文件名；解析失败回落当前时刻。"""
    generated_at = str(payload.get("generated_at") or "")
    try:
        return datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d_%H%M%S")
    except ValueError:
        return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_quote_workbook(payload: dict[str, Any]) -> Workbook:
    """把快照 payload（version/generated_at/quote_count/quotes）构建为双 sheet 工作簿。"""
    quotes = payload.get("quotes") or []
    generated_at = str(payload.get("generated_at") or "")
    two_sided = sum(1 for quote in quotes if quote.get("two_sided"))

    wb = Workbook()
    ws = wb.active
    ws.title = "经纪商报价"
    ws.append(HEADERS)
    for quote in quotes:
        ws.append([
            quote.get("code") or "",
            quote.get("name") or "",
            finite_number(quote.get("bid_yield")),
            finite_number(quote.get("bid_volume_value")),
            quote.get("bid_broker") or "",
            quote.get("bid_time") or "",
            finite_number(quote.get("ofr_yield")),
            finite_number(quote.get("ofr_volume_value")),
            quote.get("ofr_broker") or "",
            quote.get("ofr_time") or "",
            "是" if quote.get("two_sided") else "否",
        ])
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGNMENT
    for idx, width in enumerate(_COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    note = wb.create_sheet("说明")
    note.append(["项目", "内容"])
    note_rows = [
        ("快照时间", generated_at or "-"),
        ("报价条数", len(quotes)),
        ("双边报价条数", two_sided),
        ("数据来源", "DM经纪商行情快照（门户定时抓取）"),
        ("字段口径", "收益率单位%；量单位万元，空白表示该边无有效报价"),
        ("生成方式", "门户「经纪商数据」页下载，支持最新与历史快照"),
    ]
    for row in note_rows:
        note.append(list(row))
    for cell in note[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
    note.column_dimensions["A"].width = 16
    note.column_dimensions["B"].width = 60
    return wb


def build_range_workbook(snapshots: list[dict[str, Any]]) -> Workbook:
    """多日快照堆叠导出（write_only 流式写，几十万行级），每日取最后快照由调用方组装。

    snapshots 需按时间升序；首列为快照时间便于筛选透视。
    """
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("经纪商报价")
    ws.append([_styled_cell(ws, value) for value in RANGE_HEADERS])
    two_sided = 0
    total = 0
    for payload in snapshots:
        observed = str(payload.get("generated_at") or "")
        for quote in payload.get("quotes") or []:
            ws.append([
                observed,
                quote.get("code") or "",
                quote.get("name") or "",
                finite_number(quote.get("bid_yield")),
                finite_number(quote.get("bid_volume_value")),
                quote.get("bid_broker") or "",
                quote.get("bid_time") or "",
                finite_number(quote.get("ofr_yield")),
                finite_number(quote.get("ofr_volume_value")),
                quote.get("ofr_broker") or "",
                quote.get("ofr_time") or "",
                "是" if quote.get("two_sided") else "否",
            ])
            total += 1
            two_sided += 1 if quote.get("two_sided") else 0
    for idx, width in enumerate(_RANGE_COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(RANGE_HEADERS))}{total + 1}"

    note = wb.create_sheet("说明")
    note.append([_styled_cell(note, value) for value in ("项目", "内容")])
    days = sorted({str(p.get("generated_at") or "")[:10] for p in snapshots})
    rows = [
        ("区间范围", f"{days[0]} 至 {days[-1]}" if days else "-"),
        ("包含快照数", len(snapshots)),
        ("报价条数", total),
        ("双边报价条数", two_sided),
        ("取数口径", "每个交易日取当日最后一次快照（每日条数见下）"),
        ("数据来源", "DM经纪商行情快照（门户定时抓取）"),
        ("字段口径", "快照时间为该行所属抓取时刻；收益率单位%；量单位万元，空白表示该边无有效报价"),
    ]
    for label, value in rows:
        note.append([label, value])
    note.append([])
    note.append([_styled_cell(note, value) for value in ("快照时点", "报价条数")])
    for payload in snapshots:
        quotes = payload.get("quotes") or []
        note.append([str(payload.get("generated_at") or ""), len(quotes)])
    note.column_dimensions["A"].width = 20
    note.column_dimensions["B"].width = 60
    return wb


def _styled_cell(worksheet, value):
    cell = WriteOnlyCell(worksheet, value=value)
    cell.font = _HEADER_FONT
    cell.fill = _HEADER_FILL
    cell.alignment = _HEADER_ALIGNMENT
    return cell
