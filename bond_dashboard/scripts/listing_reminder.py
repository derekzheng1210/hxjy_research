# -*- coding: utf-8 -*-
"""
「明日上市」桌面提醒
================================================================
- 触发：Windows 计划任务 BondListingReminder，每交易日 17:00 运行（可手动：python scripts/listing_reminder.py --toast）
- 口径（用户确认）：
  * Excel 每日清单（excel_calendar.json）全部新发券：上市日按「缴款日(payDate) 后首个工作日」推断；
    当“上市日 = 下一工作日”时提醒（即缴款日当天 17:00 收到“明日上市”通知）。
  * 参与/中标记录（data/store/bids.json）人工 listDate 优先：listDate = 下一工作日也提醒。
- 通道：Windows 系统 Toast（scripts/listing_toast.ps1，NotifyIcon 气泡，无需 AUMID/浏览器）
- 日志：logs/listing_reminder_YYYYMMDD.log
"""
import json, os, subprocess, sys, datetime as dt, re

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LOGS = os.path.join(ROOT, "logs")
DATA = os.path.join(ROOT, "data")
os.makedirs(LOGS, exist_ok=True)

TODAY = dt.date.today()
LOG = os.path.join(LOGS, f"listing_reminder_{TODAY.isoformat()}.log")


def log(msg):
    line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass


def next_workday(d):
    n = d + dt.timedelta(days=1)
    while n.weekday() >= 5:
        n += dt.timedelta(days=1)
    return n


def load_calendar():
    p = os.path.join(DATA, "excel_calendar.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8")).get("days", {})
    except Exception:
        return {}


def load_bids():
    p = os.path.join(DATA, "store", "bids.json")
    if not os.path.exists(p):
        return []
    try:
        rows = json.load(open(p, encoding="utf-8"))
        return rows if isinstance(rows, list) else rows.get("list", [])
    except Exception:
        return []


def main():
    do_toast = "--toast" in sys.argv
    target = next_workday(TODAY)
    target_s = target.isoformat()

    lines = []
    # 1) Excel 每日清单：缴款日 = 今天 → 明日(下一工作日)上市
    cal = load_calendar()
    today_bond = 0
    for day, dd in sorted(cal.items()):
        for b in (dd.get("bonds") or []):
            pay = str(b.get("payDate") or "")
            if pay == TODAY.isoformat():
                today_bond += 1
                nm = str(b.get("name") or "").strip()
                tenor = str(b.get("tenor") or "").strip() or "-"
                amt = float(b.get("amountYi") or 0)
                cpn = b.get("coupon")
                if nm:
                    tail = f"{tenor}"
                    if cpn is not None:
                        tail += f" · 票面{float(cpn):.2f}%"
                    lines.append(f"· {nm}（{tail}，{amt:.1f}亿）今日缴款，预计明日上市")
    # 2) 参与/中标记录：人工 listDate = 下一工作日
    bid_due = 0
    for r in load_bids():
        ld = str(r.get("listDate") or "")
        if ld == target_s:
            bid_due += 1
            nm = str(r.get("bondName") or "").strip()
            typ = "中标" if r.get("type") == "won" else "参与"
            amt = float(r.get("amount") or 0)
            if nm:
                lines.append(f"· 【{typ}】{nm} {amt:.1f}亿，上市日 {ld}")

    total = len(lines)
    log(f"检查上市提醒：今日 {TODAY.isoformat()}，下一工作日 {target_s}；命中 {total} 只"
        f"（Excel 今日缴款 {today_bond}，bid listDate={target_s} {bid_due}）")
    for ln in lines:
        log("  " + ln)

    if total == 0:
        log("今日无明日上市个券，不弹提醒。")
        return 0

    title = f"债券上市提醒 · {total} 只明日上市（{target_s}）"
    body = "\n".join(lines[:12])
    if total > 12:
        body += f"\n…另有 {total - 12} 只，详见看板"
    log(f"Toast: {title}")

    if do_toast:
        ps1 = os.path.join(ROOT, "scripts", "listing_toast.ps1")
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA",
                 "-File", ps1, "-Title", title, "-Body", body],
                timeout=30, capture_output=True,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            log("Toast 已发送")
        except Exception as e:
            log(f"Toast 发送失败: {e}")
    else:
        log("（未加 --toast，仅输出）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
