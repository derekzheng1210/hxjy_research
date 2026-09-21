# -*- coding: utf-8 -*-
r"""
重建 data/history_trends.json（剔除 PPN/私募/可交换/可转债/永续）
输入: D:/DM API 实/raw_primary_history.csv

剔除口径（与 src/lib/credit.ts 一致）:
  PPN   = bond_type_desc 含"定向"/"PPN"，或简称含 "PPN"
  私募  = security_id 前缀 283/520，或 public_offering_status 含"私募"
  永续  = bond_matu_struct/bond_issue_tenor 含 "+N"/"永续"，或简称含"永续"
  可转债 = bond_type_desc 含"可转"，或简称含"转债"/"转\d*$"
  可交换 = bond_type_desc 含"可交换"，或简称含"交换债"

输出结构（2024-01 起为月度明细口径）:
- monthly           : 2023-01 起月度只数/规模/平均票面（趋势总览）
- monthly_yield_term: 2024-01 起，按发行期限 3/5/10/15/30Y 的月度平均票面（同图 5 线）
- type_monthly      : 2024-01 起，按债券类型的月度发行规模（TOP8 类型 + 其他）
- term_monthly      : 2024-01 起，按期限档的月度发行规模
- yield_hist        : 2024/2025/2026 各年票面利率直方图 + 正态拟合参数(mean/sd/n)
- province_top_year : 2024/2025/2026 各年区域发行规模 TOP12
用法: python scripts/build_history_trends.py
"""
import csv, json, os, re
from collections import defaultdict
from datetime import date

BASE = r"D:/DM API 实"
SRC = os.path.join(BASE, "raw_primary_history.csv")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "history_trends.json")

YEARS = ("2024", "2025", "2026")
TOP_TYPES = 8


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def ym_of(row, idx):
    for c in ("subscribe_date", "issue_start_date"):
        v = row[idx[c]].strip()
        if len(v) >= 7 and v[:4].isdigit():
            return v[:7]
    return None


# ---- 剔除判定 ----
def is_ppn(row, idx):
    t = row[idx["bond_type_desc"]].strip()
    n = row[idx["sec_short_name"]].strip()
    return ("定向" in t) or ("PPN" in t.upper()) or ("PPN" in n.upper())


def is_private(row, idx):
    sid = row[idx["security_id"]].strip()
    if sid.startswith("283") or sid.startswith("520"):
        return True
    return "私募" in row[idx["public_offering_status"]].strip()


def is_perpetual(row, idx):
    s = f"{row[idx['bond_matu_struct']].strip()} {row[idx['bond_issue_tenor']].strip()}"
    if re.search(r"\+\s*N", s, re.IGNORECASE):
        return True
    return ("永续" in s) or ("永续" in row[idx["sec_short_name"]].strip())


def is_convertible(row, idx):
    t = row[idx["bond_type_desc"]].strip()
    n = row[idx["sec_short_name"]].strip()
    if "可转" in t:
        return True
    return ("转债" in n) or bool(re.search(r"转\d*$", n))


def is_exchangeable(row, idx):
    t = row[idx["bond_type_desc"]].strip()
    n = row[idx["sec_short_name"]].strip()
    return ("可交换" in t) or ("交换债" in n)


DROP_REASONS = [
    ("PPN", is_ppn), ("私募", is_private), ("永续", is_perpetual),
    ("可转债", is_convertible), ("可交换债", is_exchangeable),
]


def drop_reason(row, idx):
    for name, fn in DROP_REASONS:
        if fn(row, idx):
            return name
    return None


# ---- 期限口径 ----
TERM_BUCKET_ORDER = ["<1Y", "1-3Y", "3-5Y", "5-10Y", ">=10Y", "未知"]


def term_bucket(ten):
    ten = (ten or "").strip()
    m = re.match(r"^([\d.]+)\s*(Y|D)?", ten)
    if not m:
        return "未知"
    y = float(m.group(1)) if m.group(2) != "D" else float(m.group(1)) / 365
    if y < 1: return "<1Y"
    if y < 3: return "1-3Y"
    if y < 5: return "3-5Y"
    if y < 10: return "5-10Y"
    return ">=10Y"


def main():
    with open(SRC, encoding="utf-8-sig") as f:
        r = csv.reader(f)
        h = next(r)
        idx = {n: i for i, n in enumerate(h)}
        rows = [row for row in r if row[idx["sec_short_name"]].strip()]

    keep, dropped = [], defaultdict(int)
    for row in rows:
        rr = drop_reason(row, idx)
        if rr:
            dropped[rr] += 1
            continue
        keep.append(row)
    print(f"原始 {len(rows)} 行, 保留 {len(keep)} 行, 剔除:",
          {n: dropped[n] for n, _ in DROP_REASONS})

    plan_yi = lambda row: fnum(row[idx["plan_issue_amount"]]) / 10000  # 万元 -> 亿元

    # ================= monthly（2023-01 起，总览） =================
    mon = defaultdict(lambda: [0, 0.0, 0.0, []])
    for row in keep:
        ym = ym_of(row, idx)
        if not ym or ym < "2023-01":
            continue
        d = mon[ym]
        d[0] += 1
        d[1] += plan_yi(row)
        d[2] += fnum(row[idx["actu_issue_amount"]]) / 10000
        y = fnum(row[idx["issue_yield"]])
        if y > 0:
            d[3].append(y)
    monthly = []
    for ym in sorted(mon):
        cnt, plan, act, ys = mon[ym]
        monthly.append({
            "ym": ym, "cnt": cnt,
            "plan": round(plan, 4),
            "act": round(act, 4),
            "yield_mean": round(sum(ys) / len(ys), 6) if ys else None,
        })

    # ============ 2024-01 起明细样本 ============
    sel = [(ym_of(row, idx), row) for row in keep]
    sel = [(ym, row) for ym, row in sel if ym and ym >= "2024-01"]

    # ================= monthly_yield_term（2024-01 起 5 条期限线） =================
    TERMS = [3, 5, 10, 15, 30]

    def tenor_year(row):
        t = (row[idx["bond_issue_tenor"]] or "").strip()
        m = re.match(r"^(\d+(?:\.\d+)?)\s*Y$", t)
        return int(round(float(m.group(1)))) if m else None

    mt = defaultdict(lambda: defaultdict(list))
    for ym, row in sel:
        y = fnum(row[idx["issue_yield"]])
        if y <= 0:
            continue
        t = tenor_year(row)
        if t in TERMS:
            mt[ym][t].append(y)
    months_with_yield = [m["ym"] for m in monthly if m["yield_mean"] is not None and m["ym"] >= "2024-01"]
    monthly_yield_term = []
    for ym in months_with_yield:
        rec = {"ym": ym}
        for t in TERMS:
            ys = mt.get(ym, {}).get(t) or []
            rec[f"t{t}"] = round(sum(ys) / len(ys), 6) if ys else None
        monthly_yield_term.append(rec)

    # ================= type_monthly（2024-01 起，按债券类型） =================
    type_tot = defaultdict(float)
    tp = defaultdict(lambda: defaultdict(float))
    for ym, row in sel:
        t = row[idx["bond_type_desc"]].strip() or "未知"
        v = plan_yi(row)
        tp[ym][t] += v
        type_tot[t] += v
    type_order = [t for t, _ in sorted(type_tot.items(), key=lambda x: -x[1])]
    top_types = type_order[:TOP_TYPES]
    tm = defaultdict(lambda: defaultdict(float))
    for ym, d in tp.items():
        for t, v in d.items():
            tm[ym][t if t in top_types else "其他"] += v
    type_keys = top_types + ["其他"]
    type_monthly = []
    for ym in sorted(tm):
        rec = {"ym": ym}
        for t in type_keys:
            rec[t] = round(tm[ym].get(t, 0.0), 4)
        type_monthly.append(rec)
    # 年度合计（图表副标题用）
    type_year_tot = defaultdict(lambda: defaultdict(float))
    for ym, d in tp.items():
        for t, v in d.items():
            type_year_tot[ym[:4]][t if t in top_types else "其他"] += v
    type_year_total = [
        {"year": y, **{t: round(type_year_tot[y].get(t, 0.0), 4) for t in type_keys}}
        for y in YEARS if y in type_year_tot
    ]

    # ================= term_monthly（2024-01 起，按期限档） =================
    tt = defaultdict(lambda: defaultdict(float))
    for ym, row in sel:
        b = term_bucket(row[idx["bond_issue_tenor"]])
        tt[ym][b] += plan_yi(row)
    term_keys = [b for b in TERM_BUCKET_ORDER if any(b in d for d in tt.values())]
    term_monthly = []
    for ym in sorted(tt):
        rec = {"ym": ym}
        for b in term_keys:
            rec[b] = round(tt[ym].get(b, 0.0), 4)
        term_monthly.append(rec)

    # ================= yield_hist（按年直方图 + 正态拟合） =================
    yield_hist = []
    for yr in YEARS:
        ys = [fnum(row[idx["issue_yield"]]) for ym, row in sel if ym.startswith(yr)]
        ys = [y for y in ys if y > 0]
        if not ys:
            continue
        n = len(ys)
        mean = sum(ys) / n
        sd = (sum((y - mean) ** 2 for y in ys) / n) ** 0.5
        bins = defaultdict(int)
        for y in ys:
            bins[round(y * 10) / 10] += 1
        yield_hist.append({
            "year": yr,
            "n": n,
            "mean": round(mean, 6),
            "sd": round(sd, 6),
            "min": round(min(ys), 4),
            "max": round(max(ys), 4),
            "bins": [{"ybin": b, "cnt": c} for b, c in sorted(bins.items())],
        })

    # ================= province_top_year（各年 TOP12） =================
    province_top_year = []
    for yr in YEARS:
        pt = defaultdict(lambda: [0, 0.0])
        for ym, row in sel:
            if not ym.startswith(yr):
                continue
            p = row[idx["province_name"]].strip()
            if not p:
                continue
            pt[p][0] += 1
            pt[p][1] += plan_yi(row)
        items = [{"province_name": p, "cnt": c, "plan": round(v, 4)}
                 for p, (c, v) in sorted(pt.items(), key=lambda x: -x[1][1])[:12]]
        if items:
            province_top_year.append({"year": yr, "items": items})

    out = {
        "meta": {
            "source": "raw_primary_history.csv（剔除 PPN/私募/永续/可转债/可交换债）",
            "generated": date.today().isoformat(),
            "range": f"{monthly[0]['ym']}~{monthly[-1]['ym']}" if monthly else "",
        },
        "monthly": monthly,
        "monthly_yield_term": monthly_yield_term,
        "type_monthly": type_monthly,
        "type_keys": type_keys,
        "type_year_total": type_year_total,
        "term_monthly": term_monthly,
        "term_keys": term_keys,
        "yield_hist": yield_hist,
        "province_top_year": province_top_year,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"已写出 {os.path.normpath(OUT)}")
    print("monthly", len(monthly), monthly[0]["ym"], "->", monthly[-1]["ym"])
    print("type_keys", type_keys)
    print("type_monthly", len(type_monthly), type_monthly[-1])
    print("term_keys", term_keys, "| term_monthly", len(term_monthly))
    print("monthly_yield_term", len(monthly_yield_term),
          monthly_yield_term[0]["ym"], "->", monthly_yield_term[-1]["ym"])
    print("yield_hist", [(h["year"], h["n"], h["mean"], h["sd"], len(h["bins"])) for h in yield_hist])
    print("province_top_year", [(p["year"], p["items"][0]["province_name"], p["items"][0]["plan"]) for p in province_top_year])


if __name__ == "__main__":
    main()
