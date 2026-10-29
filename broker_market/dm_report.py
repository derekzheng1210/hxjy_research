# -*- coding: utf-8 -*-
"""DM 信用债盘面日报：机构行为-经纪商数据的成交笔数/TKN/GVN 权威来源。

日报每个交易日约 17:30 发布在 DM 资讯平台（innodealing 情绪资讯流），
附件 Excel 含经纪商与平台两来源、按券种细分 × 剩余期限的成交笔数与
TKN/GVN 占比。本模块只取「经纪商」来源，与页面经纪商口径一致。

两种附件格式（按 sheet 名自动识别）：
- 旧格式：独立「成交情绪」sheet，行块为 全部/银行二永债/商金债/券商债/
  城投债/产业债，每块 成交笔数/TKN(%)/GVN(%) 三行；
- 新格式（2026 年起）：并入「全景图」sheet，经纪商来源各券种块含
  收益率/日变动/成交笔数/TKN(%)/GVN(%) 五行。

历史落盘 ``PORTAL_DATA_ROOT/data/broker_market/daily_report_history.json``：
``{version, updated_at, days: {YYYY-MM-DD: {num, tkn, gvn, sectors, file}}}``，
原始 Excel 保留在 ``dm_reports/<年份>/`` 便于追溯重解析。更新幂等：已有
日期跳过；调度器在每日 9:10 首次经纪商抓取后顺带补齐昨日及缺失日期。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from paths import DATA_DIR

from . import dm_accounts
from .storage import MARKET_DIR, atomic_write_json, load_json

SEARCH_API = "/onshore-sentiment-service-apple/api/v1/web/restructure/sentiment/news/paging"
DETAIL_API = "/onshore-sentiment-service-apple/api/v1/web/news/info"
KEYWORD = "信用债盘面日报"
SEARCH_PAGE_SIZE = 50

REPORTS_DIR = MARKET_DIR / "dm_reports"
HISTORY_PATH = MARKET_DIR / "daily_report_history.json"

# 经纪商来源的五个券种细分（与日报全景图/成交情绪 sheet 一致）。
SECTORS = ("银行二永债", "商金债", "券商债", "城投债", "产业债")
TIER2_SECTOR = "银行二永债"
MATURITY_BUCKETS = ("<1Y", "1-3Y", "3-5Y", "5-7Y", "7-10Y", ">10Y")

# 日报最早一期（平台检索下限，再往前无文章）。
FIRST_REPORT_DATE = "2025-10-29"


def load_history() -> dict[str, Any]:
    payload = load_json(HISTORY_PATH, {"version": 1, "days": {}})
    if not isinstance(payload, dict) or not isinstance(payload.get("days"), dict):
        return {"version": 1, "days": {}}
    return payload


def save_history(payload: dict[str, Any]) -> None:
    payload["version"] = 1
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    atomic_write_json(HISTORY_PATH, payload)


# ---------------------------------------------------------------- 附件解析

def _num(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if number < 0 or not number.is_integer():
        return None
    return int(number)


def _pct(value: Any) -> float | None:
    """日报 TKN/GVN 单元格为小数占比（0.597=59.7%），转百分点。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 1:
        return None
    return round(number * 100, 2)


def _maturity_columns(ws) -> dict[str, int] | None:
    """在表头行定位各期限档所在列（<1Y..>10Y）。"""
    for row in range(1, min(ws.max_row, 10) + 1):
        columns = {}
        for col in range(1, ws.max_column + 1):
            text = str(ws.cell(row, col).value or "").strip()
            if text in MATURITY_BUCKETS:
                columns[text] = col
        if len(columns) == len(MATURITY_BUCKETS):
            return columns
    return None


def _bucket_cells(ws, row: int, columns: dict[str, int]) -> dict[str, dict[str, Any]]:
    bucket = {}
    for label, col in columns.items():
        number = _num(ws.cell(row, col).value)
        if number is None:
            continue
        bucket[label] = {"num": number}
    return bucket


def _parse_panorama(ws) -> dict[str, dict[str, Any]] | None:
    """新格式：全景图 sheet，券种在 B 列、指标在 C 列、来源在 A 列。"""
    columns = _maturity_columns(ws)
    if columns is None:
        return None
    sectors: dict[str, dict[str, Any]] = {}
    source = sector = None
    pending: list[tuple[str, str, int]] = []
    for row in range(1, ws.max_row + 1):
        col_a = str(ws.cell(row, 1).value or "").strip()
        col_b = str(ws.cell(row, 2).value or "").strip()
        col_c = str(ws.cell(row, 3).value or "").strip()
        if col_a:
            source = col_a
        if col_b:
            sector = col_b
        metric = col_c if col_c in ("成交笔数", "TKN(%)", "GVN(%)") else None
        if source != "经纪商" or sector not in SECTORS or metric is None:
            continue
        if metric == "成交笔数":
            bucket = _bucket_cells(ws, row, columns)
            if bucket:
                sectors.setdefault(sector, {"maturity": {}})["maturity"].update(bucket)
        else:
            pending.append((sector, metric, row))
    for sector, metric, row in pending:
        maturity = (sectors.get(sector) or {}).get("maturity") or {}
        for label, col in columns.items():
            bucket = maturity.get(label)
            if bucket is None:
                continue
            value = _pct(ws.cell(row, col).value)
            if value is not None:
                bucket["tkn_pct" if metric == "TKN(%)" else "gvn_pct"] = value
    return sectors or None


def _parse_emotion(ws) -> dict[str, dict[str, Any]] | None:
    """旧格式：成交情绪 sheet，券种块标签在 A 列、指标在 B 列。"""
    columns = _maturity_columns(ws)
    if columns is None:
        return None
    sectors: dict[str, dict[str, Any]] = {}
    sector = None
    pending: list[tuple[str, str, int]] = []
    for row in range(1, ws.max_row + 1):
        col_a = str(ws.cell(row, 1).value or "").strip()
        col_b = str(ws.cell(row, 2).value or "").strip()
        if col_a in SECTORS:
            sector = col_a
        metric = col_b if col_b in ("成交笔数", "TKN(%)", "GVN(%)") else None
        if sector not in SECTORS or metric is None:
            continue
        if metric == "成交笔数":
            bucket = _bucket_cells(ws, row, columns)
            if bucket:
                sectors.setdefault(sector, {"maturity": {}})["maturity"].update(bucket)
        else:
            pending.append((sector, metric, row))
    for sector, metric, row in pending:
        maturity = (sectors.get(sector) or {}).get("maturity") or {}
        for label, col in columns.items():
            bucket = maturity.get(label)
            if bucket is None:
                continue
            value = _pct(ws.cell(row, col).value)
            if value is not None:
                bucket["tkn_pct" if metric == "TKN(%)" else "gvn_pct"] = value
    return sectors or None


def _report_date_from_sheet(ws, fallback: str) -> str:
    """全景图/成交情绪标题行含「10月28日」，优先以它确定报告日。"""
    for row in range(1, min(ws.max_row, 4) + 1):
        for col in range(1, min(ws.max_column, 6) + 1):
            text = str(ws.cell(row, col).value or "")
            match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
            if match:
                try:
                    day = date(int(fallback[:4]), int(match.group(1)), int(match.group(2)))
                    return day.isoformat()
                except ValueError:
                    break
    return fallback


def _summarize_sectors(sectors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把期限档占比换算为笔数，汇总到券种与全市场。"""
    result: dict[str, Any] = {"num": 0, "tkn": 0, "gvn": 0, "trd": 0}
    for name, block in sectors.items():
        num = tkn = gvn = 0
        for bucket in (block.get("maturity") or {}).values():
            count = int(bucket.get("num") or 0)
            num += count
            tkn += round(count * float(bucket.get("tkn_pct") or 0) / 100)
            gvn += round(count * float(bucket.get("gvn_pct") or 0) / 100)
        block.update({"num": num, "tkn": tkn, "gvn": gvn})
        result["num"] += num
        result["tkn"] += tkn
        result["gvn"] += gvn
    return result


def parse_report_file(path: Path, fallback_day: str) -> dict[str, Any] | None:
    """解析日报附件，返回 {day, num, tkn, gvn, sectors}；无法识别返回 None。"""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(path, data_only=True)
    except Exception:
        return None
    try:
        # 旧格式的成交情绪 sheet 必须是经纪商来源那张；个别日期的文件还会带
        # 「（自动表）自动计算平台来源成交情绪」透视表（平台口径、布局不同），
        # 按名称排除，避免误读。
        emotion_name = next(
            (n for n in workbook.sheetnames
             if "成交情绪" in n and "平台" not in n and "自动" not in n),
            None,
        )
        panorama_name = next((n for n in workbook.sheetnames if "全景" in n), None)
        if emotion_name:
            sectors = _parse_emotion(workbook[emotion_name])
            sheet = workbook[emotion_name]
        elif panorama_name:
            sectors = _parse_panorama(workbook[panorama_name])
            sheet = workbook[panorama_name]
        else:
            return None
        if not sectors:
            return None
        # 结构校验：正常日报经纪商来源至少 3 个券种、且存在方向占比；
        # 布局异常（如误选透视表）直接判为无法识别。
        pct_seen = any("tkn_pct" in bucket or "gvn_pct" in bucket
                       for block in sectors.values()
                       for bucket in (block.get("maturity") or {}).values())
        if len(sectors) < 3 or not pct_seen:
            return None
        day = _report_date_from_sheet(sheet, fallback_day)
        summary = _summarize_sectors(sectors)
        if summary["num"] <= 0:
            return None
        return {"day": day, **summary, "sectors": sectors,
                "file": path.name,
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    finally:
        workbook.close()


# ---------------------------------------------------------------- 平台拉取

def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "report.xlsx"


def _post_search(client: Any, start: str, end: str, page_id: str | None = None) -> dict[str, Any]:
    """搜索接口为游标分页：pageNo 无效，翻页须回传上页响应的 endPageId。"""
    body: dict[str, Any] = {"keyword": KEYWORD, "pageNo": 1, "pageSize": SEARCH_PAGE_SIZE,
                            "international": 2, "startDate": start, "endDate": end}
    if page_id:
        body["endPageId"] = page_id
    response = client.session.post(
        client.signed_url(SEARCH_API),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "text/plain", "Accept": "application/json, text/plain, */*"},
        timeout=client.timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in (0, 200):
        raise RuntimeError(f"日报搜索失败: {payload.get('message')}")
    data = payload.get("data") or {}
    return data if isinstance(data, dict) else {}


def _article_day(item: dict[str, Any]) -> str:
    issue_time = item.get("issueTime")
    if isinstance(issue_time, (int, float)) and issue_time:
        return datetime.fromtimestamp(issue_time / 1000).date().isoformat()
    return ""


def _article_files(client: Any, item: dict[str, Any]) -> list[tuple[str, str]]:
    """返回 [(url, 文件名)]；列表页附件缺失时回退文章详情接口。"""
    files = []
    for annex in item.get("annexList") or []:
        url = annex.get("sentimentFileUrl") or annex.get("fileUrl")
        name = annex.get("sentimentFileName") or annex.get("fileName")
        if url and name and str(name).lower().endswith(".xlsx"):
            files.append((url, str(name)))
    if files:
        return files
    sentiment_id = item.get("sentimentId")
    if not sentiment_id:
        return []
    response = client.session.get(
        client.signed_url(DETAIL_API, sentimentId=str(sentiment_id)),
        headers={"Accept": "application/json, text/plain, */*"},
        timeout=client.timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in (0, 200):
        return []
    for info in (payload.get("data") or {}).get("fileInfoDTOList") or []:
        url = info.get("sentimentFileUrl")
        name = info.get("sentimentFileName")
        if url and name and str(name).lower().endswith(".xlsx"):
            files.append((url, str(name)))
    return files


def search_reports(client: Any, start: str, end: str) -> dict[str, dict[str, Any]]:
    """搜索区间内的日报文章，按报告日归并（同日取最新一篇）。"""
    by_day: dict[str, dict[str, Any]] = {}
    page_id: str | None = None
    for _ in range(60):  # 游标不动或翻满 60 页即停，防御接口异常导致的死循环
        data = _post_search(client, start, end, page_id)
        items = data.get("list") or []
        if not items:
            break
        for item in items:
            title = str(item.get("title") or "")
            day = _article_day(item)
            if not day or KEYWORD not in title:
                continue
            previous = by_day.get(day)
            if previous is None or int(item.get("issueTime") or 0) >= int(previous.get("issueTime") or 0):
                by_day[day] = item
        if not data.get("hasNextPage"):
            break
        next_id = str(data.get("endPageId") or "")
        if not next_id or next_id == page_id:
            break
        page_id = next_id
        time.sleep(0.4)
    return by_day


def update_history(client: Any, *, start: str | None = None, end: str | None = None,
                   force: bool = False) -> dict[str, Any]:
    """下载缺失日期的日报并增量写入历史。返回 {fetched, failed, days}。"""
    history = load_history()
    days = history["days"]
    end = end or date.today().isoformat()
    start = start or (max(days) if days else FIRST_REPORT_DATE)
    if start > end:
        return {"fetched": 0, "failed": 0, "days": len(days)}
    articles = search_reports(client, start, end)
    fetched = failed = 0
    for day in sorted(articles):
        if not force and day in days:
            continue
        item = articles[day]
        try:
            files = _article_files(client, item)
            if not files:
                raise RuntimeError("文章无 xlsx 附件")
            url, name = files[0]
            out_dir = REPORTS_DIR / day[:4]
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / _safe_filename(name)
            response = client.session.get(url, timeout=60)
            response.raise_for_status()
            path.write_bytes(response.content)
            record = parse_report_file(path, day)
            if record is None:
                raise RuntimeError("附件格式无法识别")
            if record["day"] != day:
                # 标题日期与发布日期不一致时以附件为准重新归位
                day = record["day"]
                if not force and day in days:
                    continue
            days[day] = {k: v for k, v in record.items() if k != "day"}
            fetched += 1
            save_history(history)
        except Exception as exc:  # noqa: BLE001 - 单日失败不阻断其余日期
            failed += 1
            print(f"[日报] {day} 拉取失败: {type(exc).__name__}: {exc}")
        time.sleep(0.3)
    if fetched or failed:
        save_history(history)
    return {"fetched": fetched, "failed": failed, "days": len(days)}


def history_needs_update(*, through: str | None = None) -> bool:
    """调度器轻量门控：历史最新日早于昨日（日报约 17:30 发布，次日晨补齐）。"""
    days = load_history()["days"]
    if not days:
        return True
    target = (date.fromisoformat(through) if through else date.today()) - timedelta(days=1)
    return max(days) < target.isoformat()


def reparse_history() -> dict[str, int]:
    """本地重解析已下载的全部附件并重建历史（格式修正后的维护入口）。

    保留无法解析的日期条目不动（其文件可能已损坏，重解析也不应丢数据）。
    """
    history = load_history()
    days = history["days"]
    reparsed = dropped = 0
    for day in sorted(days):
        record = days[day]
        name = record.get("file") if isinstance(record, dict) else None
        if not name:
            continue
        path = REPORTS_DIR / day[:4] / _safe_filename(str(name))
        if not path.exists():
            continue
        fresh = parse_report_file(path, day)
        if fresh is None:
            dropped += 1
            continue
        days[day] = {k: v for k, v in fresh.items() if k != "day"}
        reparsed += 1
    if reparsed or dropped:
        save_history(history)
    return {"reparsed": reparsed, "unparsed": dropped, "days": len(days)}


def _main() -> int:
    parser = argparse.ArgumentParser(description="DM 信用债盘面日报历史回填")
    parser.add_argument("--start", help="回填起始日 YYYY-MM-DD，默认最早缺失日")
    parser.add_argument("--end", help="回填截止日 YYYY-MM-DD，默认今日")
    parser.add_argument("--force", action="store_true", help="已有日期也重新下载解析")
    parser.add_argument("--reparse", action="store_true", help="不联网，本地重解析已下载附件重建历史")
    args = parser.parse_args()

    if args.reparse:
        result = reparse_history()
        print(f"[日报] 重解析 {result['reparsed']} 天，无法解析 {result['unparsed']} 天，"
              f"历史共 {result['days']} 天")
        return 0

    from .dm_client_local import DMClient

    attempts = dm_accounts.snapshot_attempt_order(3)
    if not attempts:
        print("DM 账号未配置")
        return 1
    last_error: Exception | None = None
    for account in attempts:
        client = None
        try:
            client = DMClient(account["username"], account["password"], timeout=45)
            client.login()
            result = update_history(client, start=args.start, end=args.end, force=args.force)
            print(f"[日报] 新增 {result['fetched']} 天，失败 {result['failed']} 天，"
                  f"历史共 {result['days']} 天")
            return 0
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[日报] 账号 {account['username']} 失败: {exc}")
        finally:
            if client is not None:
                dm_accounts.update_device_id(account["username"], client.device_id)
                client.close()
    if last_error:
        raise last_error
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
