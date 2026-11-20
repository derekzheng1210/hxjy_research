"""存量债利差监控·自定义区间查询：服务端计算，历史缓存绝不下发浏览器。

数据源（paths.SPREAD_DIR）：
- spread_history_cache.json  按日全市场快照 {"dates": {YYYYMMDD: {raw_code: {yield, curve_yield, spread}}}}
  （数百 MB，仅服务端持有；每日利差更新任务重写，按 mtime 失效进程内缓存）
- spread_data.js             页面载荷 "var SPREAD_DATA = {...};"，借其债券静态信息
  （名称/隐含评级/期限/曲线/是否重点债券），与页面展示口径一致。

日期语义：请求日期自动回退到历史缓存中 ≤ 该日期的最近交易日（周末/节假日请求
落在前一交易日），响应带 start_used/end_used 告知实际使用日期。
"""

from __future__ import annotations

import bisect
import json
import re
import threading
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from paths import SPREAD_DIR

HISTORY_PATH = SPREAD_DIR / "spread_history_cache.json"
DATA_JS_PATH = SPREAD_DIR / "spread_data.js"

_LOCK = threading.Lock()
_CACHE: dict | None = None  # {"mtime", "dates", "date_keys", "bond_info"}

XLSX_HEADERS = [
    "证券代码", "证券名称", "隐含评级", "期限(年)", "对应曲线",
    "期初利差(bp)", "期末利差(bp)", "区间变化(bp)",
    "期初估值(%)", "期末估值(%)", "是否重点债券",
]
_XLSX_WIDTHS = [14, 22, 10, 9, 14, 12, 12, 12, 12, 12, 12]


class RangeError(ValueError):
    """区间参数或数据不满足（HTTP 400 语义）。"""


def _parse_data_js(text: str) -> dict:
    m = re.search(r"var\s+SPREAD_DATA\s*=\s*(\{.*\})\s*;?\s*$", text, re.S)
    if not m:
        raise RangeError("spread_data.js 格式异常（未找到 var SPREAD_DATA = {...}）")
    return json.loads(m.group(1))


def _load() -> dict:
    """惰性加载并缓存；历史缓存 mtime 变化（每日更新）后自动重载。线程安全。"""
    global _CACHE
    with _LOCK:
        if not HISTORY_PATH.exists() or not DATA_JS_PATH.exists():
            raise RangeError("利差历史缓存未生成，请等待每日更新完成后重试")
        mtime = HISTORY_PATH.stat().st_mtime
        if _CACHE and _CACHE["mtime"] == mtime:
            return _CACHE
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        dates = history.get("dates") or {}
        if not dates:
            raise RangeError("利差历史缓存为空")
        date_keys = sorted(dates)
        bond_info: dict[str, dict] = {}
        payload = _parse_data_js(DATA_JS_PATH.read_text(encoding="utf-8"))
        for row in payload.get("data") or []:
            raw = str(row.get("raw_code") or "").strip()
            if raw:
                bond_info[raw] = {
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "implied_rating": row.get("implied_rating"),
                    "term": row.get("term"),
                    "curve_name": row.get("curve_name"),
                    "is_holding": bool(row.get("is_holding")),
                }
        _CACHE = {"mtime": mtime, "dates": dates, "date_keys": date_keys, "bond_info": bond_info}
        return _CACHE


def _resolve_date(cache: dict, ymd: str) -> str:
    """≤ ymd 的最近缓存交易日；早于最早日期则报错。"""
    keys = cache["date_keys"]
    i = bisect.bisect_right(keys, ymd) - 1
    if i < 0:
        raise RangeError(f"{ymd} 早于历史缓存最早日期 {keys[0]}，无法作为期初")
    return keys[i]


def compute_range(start: str, end: str) -> dict:
    """期初/期末利差与区间变化（bp，正=走扩）。两端都有利差的券才入表。"""
    if not re.fullmatch(r"\d{8}", start or "") or not re.fullmatch(r"\d{8}", end or ""):
        raise RangeError("日期格式应为 YYYYMMDD")
    if start > end:
        raise RangeError("起始日期不能晚于结束日期")
    cache = _load()
    start_used = _resolve_date(cache, start)
    end_used = _resolve_date(cache, end)
    if start_used == end_used:
        raise RangeError(f"区间两端落在同一交易日（{start_used}），请扩大日期范围")

    start_slice = cache["dates"][start_used]
    end_slice = cache["dates"][end_used]
    bond_info = cache["bond_info"]
    rows = []
    for raw, s in start_slice.items():
        e = end_slice.get(raw)
        if s is None or e is None:
            continue
        s_spread, e_spread = s.get("spread"), e.get("spread")
        if s_spread is None or e_spread is None:
            continue
        rows.append({
            "raw_code": raw,
            **bond_info.get(raw, {}),
            "yield_start": s.get("yield"),
            "yield_end": e.get("yield"),
            "spread_start": s_spread,
            "spread_end": e_spread,
            "change_bp": round(e_spread - s_spread, 2),
        })
    rows.sort(key=lambda r: abs(r["change_bp"]), reverse=True)
    return {
        "start_requested": start,
        "end_requested": end,
        "start_used": start_used,
        "end_used": end_used,
        "rows": rows,
    }


def build_range_workbook(result: dict) -> BytesIO:
    """区间查询结果导出 xlsx（与经纪商快照导出同风格：写Only、表头底色居中）。"""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("区间利差")
    for i, w in enumerate(_XLSX_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    header_font, header_fill = Font(bold=True), PatternFill("solid", fgColor="EEF2FF")
    header_align = Alignment(horizontal="center", vertical="center")
    header_cells = []
    for text in XLSX_HEADERS:
        cell = WriteOnlyCell(ws, value=text)
        cell.font, cell.fill, cell.alignment = header_font, header_fill, header_align
        header_cells.append(cell)
    ws.append(header_cells)
    for r in result["rows"]:
        ws.append([
            r.get("code") or r["raw_code"],
            r.get("name") or "",
            r.get("implied_rating") or "",
            r.get("term"),
            r.get("curve_name") or "",
            r.get("spread_start"),
            r.get("spread_end"),
            r["change_bp"],
            r.get("yield_start"),
            r.get("yield_end"),
            "是" if r.get("is_holding") else "否",
        ])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
