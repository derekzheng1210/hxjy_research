# -*- coding: utf-8 -*-
"""
一级中标登记表.xlsx → data/store/bids.json 幂等同步（增量合并，不覆盖已有记录）
- 聚合规则：按 (发行日, 债券简称) 分组
  * participated 记录：amount = Σ 投资量（全部行）
  * won 记录：amount = Σ 中标量（有数值的行才产生），仅当合计>0
- note：投资经理/参与券商 均去重保序拼接
- 已存在的 (bidDate, bondName, type) 记录跳过（保留页面手工录入与备注）
"""
import openpyxl, json, os, re, uuid
from collections import defaultdict, OrderedDict
from datetime import datetime

BASE = r"D:/2026/一级投标/投标情况"
XLSX = os.path.join(BASE, "一级中标登记表.xlsx")
BIDS_PATH = "C:/Users/yoyo1/WorkBuddy/2026-09-01-16-51-51/bond-dashboard/data/store/bids.json"

COLS = {  # 登记表表头 → 索引
    "中标量": "中标量（亿元）", "券商": "参与券商", "票面": "票面",
    "代码": "债券代码", "简称": "债券简称", "时间": "发行时间",
    "期限": "发行期限", "经理": "投资经理", "投资量": "投资量",
    "投标价": "投标价格", "yy": "YY评分",
}

def load_col_idx(h):
    idx = {}
    for i, x in enumerate(h):
        if x is not None:
            idx[str(x).strip()] = i
    return idx

def fnum(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0

def parse_date(s):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s))
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})-(\d{2})", str(s))
    if m:
        return f"2026-{m.group(1)}-{m.group(2)}"
    return None

def uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out

wb = openpyxl.load_workbook(XLSX, data_only=True, read_only=True)
ws = wb.worksheets[0]
rows = list(ws.iter_rows(values_only=True))
h = rows[0]
ci = load_col_idx(h)
need = set(COLS.values())
missing = need - set(ci.keys())
if missing:
    raise SystemExit(f"登记表缺少列: {missing}（现有: {list(ci)[:20]}）")
c = {k: ci[v] for k, v in COLS.items()}

groups = defaultdict(lambda: {
    "inv": 0.0, "win": 0.0, "managers": [], "brokers": [],
    "coupon": None, "code": None, "term": None, "yy": None, "win_managers": [],
})
for r in rows[1:]:
    name = str(r[c["简称"]] or "").strip()
    if not name:
        continue
    d = parse_date(r[c["时间"]])
    if not d:
        print("跳过无法解析日期行:", r[c["时间"]], name)
        continue
    g = groups[(d, name)]
    mgr = str(r[c["经理"]] or "").strip()
    brk = str(r[c["券商"]] or "").strip()
    if mgr and mgr not in g["managers"]:
        g["managers"].append(mgr)
    if brk and brk not in g["brokers"]:
        g["brokers"].append(brk)
    g["inv"] += fnum(r[c["投资量"]])
    wv = r[c["中标量"]]
    ws_ = str(wv).strip() if wv is not None else ""
    try:
        if ws_ not in ("", "未中标", "None"):
            amt = float(ws_)
            g["win"] += amt
            if mgr and mgr not in g["win_managers"]:
                g["win_managers"].append(mgr)
    except ValueError:
        pass
    g["coupon"] = g["coupon"] if g["coupon"] is not None else r[c["票面"]]
    code = str(r[c["代码"]] or "").strip()
    g["code"] = g["code"] or (code or None)
    tm = str(r[c["期限"]] or "").strip()
    g["term"] = g["term"] or (tm or None)
    yv = r[c["yy"]]
    if g["yy"] is None and yv is not None:
        s = str(yv).strip()
        # YY 评分档位形如 1/2/3/4/5 或 4+/2-；其余（如 2.1088）系登记表个别行单元格错位，
        # 不能当 YY 展示（2026-09-11 用户反馈「YY 评分怎么还有小数点」）
        if re.fullmatch(r"[1-5][+-]?", s):
            g["yy"] = s

bids = json.load(open(BIDS_PATH, encoding="utf-8"))
existing = {(b["bidDate"], b["bondName"], b["type"]) for b in bids}
now = datetime.now().isoformat()
added = 0

for (d, name), g in sorted(groups.items()):
    if (d, name, "participated") not in existing:
        note = "投资经理: " + "、".join(g["managers"]) if g["managers"] else ""
        if g["brokers"]:
            note += ("；" if note else "") + "参与券商: " + "、".join(g["brokers"])
        bids.append({
            "id": str(uuid.uuid4()),
            "type": "participated",
            "bondName": name,
            "securityId": g["code"],
            "issuer": None,
            "term": g["term"],
            "amount": round(g["inv"], 2),
            "coupon": g["coupon"],
            "spread": None,
            "bidDate": d,
            "payDate": None,
            "note": note,
            "createdAt": now,
            "yy": g["yy"],
        })
        added += 1
        print(f"  + 参与 {d} {name} {round(g['inv'],2)}亿")
    if g["win"] > 0 and (d, name, "won") not in existing:
        note = "投资经理: " + "、".join(g["win_managers"]) if g["win_managers"] else ""
        bids.append({
            "id": str(uuid.uuid4()),
            "type": "won",
            "bondName": name,
            "securityId": g["code"],
            "issuer": None,
            "term": g["term"],
            "amount": round(g["win"], 2),
            "coupon": g["coupon"],
            "spread": None,
            "bidDate": d,
            "payDate": None,
            "note": note,
            "createdAt": now,
            "yy": g["yy"],
        })
        added += 1
        print(f"  + 中标 {d} {name} {round(g['win'],2)}亿")

with open(BIDS_PATH, "w", encoding="utf-8") as f:
    json.dump(bids, f, ensure_ascii=False, indent=1)
print(f"\n登记表组数 {len(groups)}（中标 {sum(1 for g in groups.values() if g['win']>0)}）| 本次新增 {added} 条 | bids.json 现有 {len(bids)} 条")

# ---------- 回填：已存在记录缺票面/期限/代码/YY 时用登记表补齐（不覆盖已有值） ----------
# 背景：本脚本按 (发行日, 简称, 类型) 去重跳过已有记录，页面早期手工录入的记录会长期缺票面
#       （2026-09-11 用户反馈「票面为什么还有空值」）。此处做增量回填。
def backfill():
    by_key = {(b.get("bidDate"), b.get("bondName"), b.get("type")): b for b in bids}
    n_coupon = n_other = 0
    for (d, name), g in groups.items():
        for t in ("participated", "won"):
            r0 = by_key.get((d, name, t))
            if not r0:
                continue
            if not r0.get("coupon"):
                s = str(g["coupon"]).strip() if g["coupon"] is not None else ""
                # 仅接受纯数值票面；「回拨15年 / 取消发行」等文字标注不写入票面（改由备注标注）
                if re.fullmatch(r"\d+(\.\d+)?", s):
                    r0["coupon"] = float(s)
                    n_coupon += 1
            if not r0.get("term") and g["term"]:
                r0["term"] = g["term"]
                n_other += 1
            if not r0.get("securityId") and g["code"]:
                r0["securityId"] = g["code"]
                n_other += 1
            if not r0.get("yy") and g["yy"]:
                r0["yy"] = g["yy"]
                n_other += 1
    if n_coupon or n_other:
        with open(BIDS_PATH, "w", encoding="utf-8") as f:
            json.dump(bids, f, ensure_ascii=False, indent=1)
    print(f"回填：票面 {n_coupon} 条，期限/代码/YY {n_other} 条")

backfill()

# ---------- 同步 SQLite 镜像（data/bond.db bids 表） ----------
# 说明：页面录入走 SQLite 主写并同步写 bids.json；本脚本以 bids.json 为权威
#       （Excel 登记表合并结果），完成后全量重建 DB bids 表，保证两边一致。
def sync_db_from_json():
    import sqlite3
    db_path = os.path.join(os.path.dirname(BIDS_PATH), "..", "bond.db")
    db_path = os.path.normpath(db_path)
    try:
        con = sqlite3.connect(db_path, timeout=8)
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("""CREATE TABLE IF NOT EXISTS bids(
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            bondName TEXT, securityId TEXT, issuer TEXT, term TEXT,
            amount REAL NOT NULL, coupon REAL, spread REAL, yy TEXT,
            bidDate TEXT NOT NULL, payDate TEXT, listDate TEXT, note TEXT,
            author TEXT,
            createdAt TEXT NOT NULL, updatedAt TEXT)""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_bids_type ON bids(type)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_bids_bidDate ON bids(bidDate)")
        with open(BIDS_PATH, encoding="utf-8") as f:
            rows = json.load(f)
        cur = con.cursor()
        cur.execute("DELETE FROM bids")
        ins = """INSERT OR REPLACE INTO bids(
            id, type, bondName, securityId, issuer, term, amount, coupon, spread, yy,
            bidDate, payDate, listDate, note, author, createdAt, updatedAt)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        for r in rows:
            cur.execute(ins, (
                r.get("id"), r.get("type") or "participated",
                r.get("bondName") or "", r.get("securityId"), r.get("issuer"),
                r.get("term"), float(r.get("amount") or 0), r.get("coupon"),
                r.get("spread"), r.get("yy"), r.get("bidDate") or "",
                r.get("payDate"), r.get("listDate"), r.get("note"),
                r.get("author"), r.get("createdAt") or datetime.now().isoformat(),
                r.get("updatedAt")))
        con.commit()
        con.close()
        print(f"SQLite bids 表已同步 {len(rows)} 条 → {db_path}")
    except Exception as e:
        print(f"⚠ SQLite 同步失败（不影响 JSON 产物）: {e}")

sync_db_from_json()
