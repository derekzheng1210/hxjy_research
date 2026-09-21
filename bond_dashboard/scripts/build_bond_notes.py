# -*- coding: utf-8 -*-
"""
为「票面为空」的个券生成缺失原因标注 → data/store/bond_notes.json
（覆盖两处：参与/中标记录、推荐个券清单）

背景（2026-09-11 用户反馈）：看板仍有票面显示空值，需判断是「回拨其他期限」还是
「取消发行」，并在「票面」一栏标注。

判定优先级：
  1. 一级中标登记表「票面」列 / 每日发行 Excel 票面列原文若为文字（回拨15年、取消发行）→ 采用；
  2. 登记表「中标量（亿元）」列若为文字（取消发行 / 未截标 / 未中标）→ 采用；
  3. DM 一级发行历史：同名券状态「取消发行」→ 取消发行；「发行中/待发行」→ 未截标。
另：若 DM 该券已上市且有票面而本地记录票面为空，则回填票面（不改标注）。

产物：
  data/store/bond_notes.json = { 债券简称: {note, dmStatus, dmName, source} }
  必要时回填 data/store/bids.json / data/recommended.json 的票面并同步 SQLite
用法：python scripts/build_bond_notes.py
"""
import csv
import json
import os
import re
import sqlite3
from collections import OrderedDict

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
BIDS_PATH = os.path.join(ROOT, "data", "store", "bids.json")
REC_PATH = os.path.join(ROOT, "data", "recommended.json")
NOTES_PATH = os.path.join(ROOT, "data", "store", "bond_notes.json")
CSV = r"D:/DM API 实/raw_primary_history.csv"
XLSX_BID = r"D:/2026/一级投标/投标情况/一级中标登记表.xlsx"
EXCEL_DIR = r"D:/2026/一级投标/投标情况"

TEXT_KEEP = ("取消发行", "回拨", "未截标", "延期", "推迟")


def norm(s):
    """去括号内容（DM 取消发行券记作「26HPI01K(取消发行)」）"""
    return re.sub(r"[（(][^）)]*[）)]", "", str(s or "")).strip()


def is_num(s):
    return bool(re.fullmatch(r"\d+(\.\d+)?", str(s).strip())) if s is not None else False


def load_excel_bids():
    """登记表：简称 -> {coupon_raw, win_raw}"""
    import openpyxl
    wb = openpyxl.load_workbook(XLSX_BID, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(x).strip() if x is not None else "" for x in rows[0]]
    idx = {h: i for i, h in enumerate(hdr) if h}

    def col(*names):
        for n in names:
            for h, i in idx.items():
                if h.startswith(n):
                    return i
        return None

    c_coupon, c_win, c_name = col("票面"), col("中标量"), col("债券简称")
    out = {}
    for r in rows[1:]:
        nm = str(r[c_name] or "").strip()
        if not nm:
            continue
        v = out.setdefault(nm, {"coupon_raw": None, "win_raw": None})
        if v["coupon_raw"] is None and r[c_coupon] is not None and str(r[c_coupon]).strip():
            v["coupon_raw"] = str(r[c_coupon]).strip()
        if v["win_raw"] is None and r[c_win] is not None and str(r[c_win]).strip():
            v["win_raw"] = str(r[c_win]).strip()
    return out


def load_excel_daily():
    """每日发行 Excel：简称 -> 票面列原文（文字标注：回拨X年 / 取消发行）"""
    import openpyxl
    out = {}
    files = [f for f in os.listdir(EXCEL_DIR) if re.match(r"一级发行-信用债发行 2026-\d{2}-\d{2}\.xlsx$", f)]
    for fn in sorted(files):
        try:
            wb = openpyxl.load_workbook(os.path.join(EXCEL_DIR, fn), data_only=True, read_only=True)
        except Exception:
            continue
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            hdr = [str(x).strip() if x is not None else "" for x in rows[0]]

            def cidx(*names):
                for n in names:
                    for i, h in enumerate(hdr):
                        if h.startswith(n) and "票面-预测" not in h:
                            return i
                return None

            c_name, c_coupon = cidx("债券简称"), cidx("票面")
            if c_name is None or c_coupon is None:
                continue
            for r in rows[1:]:
                if c_name >= len(r):
                    continue
                nm = str(r[c_name] or "").strip()
                if not nm or nm in out:
                    continue
                v = r[c_coupon] if c_coupon < len(r) else None
                if v is not None and str(v).strip() and not is_num(v):
                    s = str(v).strip()
                    if any(k in s for k in TEXT_KEEP):
                        out[nm] = s
    return out


def load_dm():
    """normalized name -> 记录（优先非取消发行、簿记日较新）"""
    best = {}
    with open(CSV, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("sec_short_name") or "").strip()
            if not name:
                continue
            key = norm(name)
            sd = (row.get("subscribe_date") or "").strip()
            status = (row.get("issue_status_desc") or "").strip()
            cancelled = status == "取消发行" or "取消发行" in name
            score = (0 if not cancelled else -1, sd)
            cur = best.get(key)
            if cur is None or score > cur[0]:
                best[key] = (score, {
                    "name": name,
                    "id": (row.get("security_id") or "").strip(),
                    "status": status,
                    "cancelled": cancelled,
                    "coupon": (row.get("issue_yield") or "").strip(),
                    "tenor": (row.get("bond_issue_tenor") or "").strip(),
                })
    return {k: v[1] for k, v in best.items()}


def load_calendar():
    """excel_calendar.json：简称 -> 票面（用户每日 Excel 口径，DM 尚未收录时的兜底）"""
    try:
        cal = json.load(open(os.path.join(ROOT, "data", "excel_calendar.json"), encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for day in (cal.get("days") or {}).values():
        for b in day.get("bonds") or []:
            nm = b.get("name")
            c = b.get("coupon")
            if nm and c is not None and nm not in out:
                out[nm] = c
    return out


def resolve_note(name, xl_bid, xl_daily, dm):
    """返回 (note, source) 或 (None, None)"""
    x = xl_bid.get(name) or {}
    # 1. 票面列文字（登记表 / 每日发行 Excel）
    for raw, src in ((x.get("coupon_raw"), "登记表·票面列文字"),
                     (xl_daily.get(name), "发行 Excel·票面列文字")):
        if raw and not is_num(raw) and raw not in ("-", "--", "--/--"):
            return raw, src
    # 2. 中标量列文字
    wv = x.get("win_raw")
    if wv and any(k in wv for k in TEXT_KEEP):
        return wv, "登记表·中标量列文字"
    # 3. DM 状态
    d = dm.get(norm(name)) or {}
    if d.get("cancelled"):
        return "取消发行", "DM 发行状态"
    if d.get("status") in ("发行中", "待发行", "未上市"):
        return "未截标", "DM 发行状态"
    return None, None


def main():
    bids = json.load(open(BIDS_PATH, encoding="utf-8"))
    rec = json.load(open(REC_PATH, encoding="utf-8"))
    xl_bid = load_excel_bids()
    xl_daily = load_excel_daily()
    dm = load_dm()
    cal = load_calendar()

    def fill_coupon(name):
        """DM 或 Excel 日历中有票面时回填，返回 (coupon, source) 或 (None, None)"""
        d = dm.get(norm(name)) or {}
        if is_num(d.get("coupon")):
            return float(d["coupon"]), "DM"
        c = cal.get(name)
        if c is not None:
            return float(c), "发行 Excel 日历"
        return None, None

    notes = OrderedDict()
    filled = {"bid": 0, "rec": 0}
    changed_rec = 0
    bad_yy = 0

    # ---------- 参与/中标记录 ----------
    for b in bids:
        yy = b.get("yy")
        if yy and not re.fullmatch(r"[1-5][+-]?", str(yy).strip()):
            print(f"  清理异常 YY：{b['bondName']} yy={yy!r} → 置空")
            b["yy"] = None
            bad_yy += 1
        if b.get("coupon"):
            continue
        name = b.get("bondName") or ""
        note, source = resolve_note(name, xl_bid, xl_daily, dm)
        if note:
            notes.setdefault(name, {"note": note, "source": source, "dmStatus": (dm.get(norm(name)) or {}).get("status", "")})
            print(f"  [参与/中标] {name} → {note}（{source}）")
        else:
            c0, src0 = fill_coupon(name)
            if c0 is not None:
                b["coupon"] = c0
                filled["bid"] += 1
                print(f"  [参与/中标] 回填票面 {name} = {c0}（{src0}）")

    # ---------- 推荐个券清单 ----------
    for r in rec:
        if r.get("coupon"):
            continue
        name = r.get("name") or ""
        note, source = resolve_note(name, xl_bid, xl_daily, dm)
        raw = r.get("coupon_raw")
        if not note and raw and not is_num(raw):
            note, source = raw, "推荐清单·票面列原文"
        if note:
            notes.setdefault(name, {"note": note, "source": source, "dmStatus": (dm.get(norm(name)) or {}).get("status", "")})
            # 占位票面（0）清空，避免页面把它当成已定价（sp_bp 同步置空）
            if r.get("coupon") is not None and not r.get("coupon"):
                r["coupon"] = None
                r["sp_bp"] = None
                changed_rec += 1
            print(f"  [推荐] {name} → {note}（{source}）")
        else:
            c0, src0 = fill_coupon(name)
            if c0 is not None:
                r["coupon"] = c0
                filled["rec"] += 1
                print(f"  [推荐] 回填票面 {name} = {c0}（{src0}）")

    with open(NOTES_PATH, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=1)
    if filled["bid"] or bad_yy:
        with open(BIDS_PATH, "w", encoding="utf-8") as f:
            json.dump(bids, f, ensure_ascii=False, indent=1)
        try:
            con = sqlite3.connect(os.path.join(ROOT, "data", "bond.db"), timeout=8)
            cur = con.cursor()
            for b in bids:
                cur.execute("UPDATE bids SET coupon=?, yy=? WHERE id=?", (b.get("coupon"), b.get("yy"), b["id"]))
            con.commit()
            con.close()
            print("SQLite 已同步")
        except Exception as e:
            print(f"⚠ SQLite 同步失败：{e}")
    if filled["rec"] or changed_rec:
        with open(REC_PATH, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)

    print(f"\n标注 {len(notes)} 条 → {NOTES_PATH}")
    print(f"票面回填：参与/中标 {filled['bid']} 条，推荐清单 {filled['rec']} 条；清空占位票面 {changed_rec} 条；清理异常 YY {bad_yy} 条")
    from collections import Counter
    print("标注分布:", Counter(v["note"] for v in notes.values()).most_common())


if __name__ == "__main__":
    main()
