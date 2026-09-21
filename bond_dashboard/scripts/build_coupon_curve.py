# -*- coding: utf-8 -*-
"""
信用债一级发行「票面-期限」曲线 构建脚本
================================================================
- 数据源：D:/DM API 实/raw_primary_history.csv（DM 一级发行主数据，每日增量更新，覆盖 2026-01-01 起全 YTD）
- 个券筛选（需求口径）：
    * 信用债，剔除 PPN/定向、私募(283/520 前缀或私募标识)、可转债/可交换（含可转换债券/可交换债券类型及「转债/可交换/交换债/EB」名称标记，覆盖转02 等次次券）、取消发行(无票面)
    * 剔除永续(可续期，含 +N 结构)；普通含权债(X+Y，如 3+2/5+5) 取「行权前期限」为曲线期限
    * YY 评分 1-5 档（含子档，如 1-、5+）：主体 YY 取「每日一级发行 Excel 全历史 YY(优先) + DM 公司评级 YY(兜底)」
    * 行权前期限 ≤ 30 年；票面取实际发行票面 issue_yield
- 期限分档（用户选定·整数关键期限）：≤1Y、2Y、3Y、5Y、7Y、10Y、15Y、20Y、30Y
  （≤1Y 含 1 年以内短融/超短融；档间以相邻关键期限中点切分）
- 加权口径：票面按发行金额加权；金额口径 =「实际发行额」（DM actu_issue_amount，市场真实发行，与上市公告一致），
  DM 计划发行额（plan_issue_amount）保留在个券明细 planAmtYi/actuAmtYi 供对照（缩量/超募券两口径不同）
- 输出：
    * data/coupon_curve.json——最新累计曲线（2026 年以来全区间，供页面默认展示）
    * data/coupon_curve_by_date.json——逐日快照（每簿记日=「当日发行」口径，供日期叠加对比）
用法: python scripts/build_coupon_curve.py [截止日 YYYY-MM-DD，默认今天]
"""
import csv, json, os, re, sys, time, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.normpath(os.path.join(HERE, ".."))
DATA = os.path.join(BASE, "data")
CACHE_DIR = os.path.join(DATA, "cache")
ENV_FILE = os.path.join(BASE, ".env.local")

CSV_PATH = r"D:/DM API 实/raw_primary_history.csv"
START = "2026-01-01"
YY_FILE = os.path.join(DATA, "yy_lookup.json")          # 每日 Excel 全历史「主体→YY」
YY_CACHE = os.path.join(CACHE_DIR, "yy_issuer.json")     # DM 评级 YY 兜底缓存（持久化，跨日复用）
OUT = os.path.join(DATA, "coupon_curve.json")            # 最新累计曲线（供页面默认展示）
OUT_BY_DATE = os.path.join(DATA, "coupon_curve_by_date.json")  # 逐日累计快照（供日期叠加对比）

# 期限档位：标签 -> (下界, 上界]（年·行权前期限）；档间取相邻关键期限中点
BUCKETS = [
    ("≤1Y", 0, 1.5),
    ("2Y", 1.5, 2.5),
    ("3Y", 2.5, 4),
    ("5Y", 4, 6),
    ("7Y", 6, 8.5),
    ("10Y", 8.5, 12.5),
    ("15Y", 12.5, 17.5),
    ("20Y", 17.5, 25),
    # 30Y 上界 30.5 容差：DM 中 30 年期 mat 常记为 30.02Y（30年+计息天数折算），须归入 30Y 档
    ("30Y", 25, 30.5),
]

# ---------- 凭证 ----------
def load_env():
    if not os.path.exists(ENV_FILE):
        print("缺少 .env.local"); sys.exit(2)
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("INNO_APP_KEY") or not os.environ.get("INNO_APP_SECRET"):
        print("缺少 INNO_APP_KEY / INNO_APP_SECRET"); sys.exit(2)

# ---------- 期限解析 ----------
def parse_years(s):
    s = (s or "").strip()
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*Y", s, re.I)
    if m:
        return float(m.group(1))
    m = re.match(r"^([0-9]+)\s*D", s, re.I)
    if m:
        return int(m.group(1)) / 365.0
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*年", s)
    if m:
        return float(m.group(1))
    return None


def pre_tenor(mat, tenor):
    """行权前期限（年）：含权债取括号内首段，如 5Y(3+2)→3、4Y(2+2)→2、10.01Y(5+5)→5；非含权取原期限"""
    s = (mat or "").strip() or (tenor or "").strip()
    m = re.search(r"\((.*?)\)", s)
    if m:
        inner = m.group(1).strip()
        part = inner.split("+")[0].strip()
        v = parse_years(part)
        if v is None:
            try:
                v = float(part)   # 括号内首段可能是裸数字（DM 常见如 4Y(2+2) 的「2」）
            except Exception:
                v = None
        if v is not None:
            return v
    return parse_years(s)


def bucket_of(t):
    for name, lo, hi in BUCKETS:
        if lo < t <= hi:
            return name
    return None


def yy_in_scope(yy):
    """YY 评分 1-5 档（含子档：1-、1、1+ … 5+），即整数部分 ∈ {1,2,3,4,5}"""
    if not yy:
        return False
    m = re.match(r"^\s*([0-9]+)", str(yy))
    return bool(m) and int(m.group(1)) in (1, 2, 3, 4, 5)


def is_perp(mat, tenor):
    s = (mat or "") + " " + (tenor or "")
    return bool(re.search(r"\+\s*N", s, re.I)) or ("永续" in s)


# ---------- DM 评级 YY 兜底 ----------
def load_yy_map():
    yy_map = {}
    if os.path.exists(YY_FILE):
        try:
            yy_map = json.load(open(YY_FILE, encoding="utf-8")).get("map", {})
        except Exception:
            pass
    return yy_map


def load_yy_cache():
    if os.path.exists(YY_CACHE):
        try:
            return json.load(open(YY_CACHE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_yy_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(YY_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0)


def dm_yy_lookup(client, issuers):
    """查询 DM company/rating/data：各主体取「数字开头 YY 评级」最新一条；无则返回空"""
    out = {}
    for i in range(0, len(issuers), 5):
        chunk = issuers[i:i + 5]
        try:
            rows = client.post_data({"comChiNameList": chunk},
                                    api_path="/dm-quant-func-service/api/v1/company/rating/data",
                                    return_type="dict")
            rows = rows if isinstance(rows, list) else (rows.get("list") or rows.get("data") or [])
        except Exception as e:
            print(f"  评级查询失败 {chunk}: {str(e)[:80]}")
            continue
        best = {}  # name -> [date, yy]
        for r in rows:
            name = str(r.get("com_chi_name") or "").strip()
            rat = str(r.get("rating") or "").strip()
            dat = str(r.get("rating_date") or "")
            if not name or not rat or not re.match(r"^\s*[0-9]", rat):
                continue  # 仅收数字开头 YY 评分
            if name not in best or dat > best[name][0]:
                best[name] = [dat, rat]
        for name, (_, yy) in best.items():
            out.setdefault(name, yy)
        time.sleep(0.1)
    return out


# ---------- 主流程 ----------
def main():
    load_env()
    end = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    if not os.path.exists(CSV_PATH):
        print("缺少历史 CSV:", CSV_PATH); sys.exit(2)
    yy_lookup = load_yy_map()
    yy_cache = load_yy_cache()
    print(f"YYYYMM: Excel YY 主体 {len(yy_lookup)}，DM YY 缓存 {len(yy_cache)}；区间 {START}~{end}")

    # ---- 第一遍：收集所有通过硬性筛选的行，找出缺 YY 的主体 ----
    pending = {}       # security_id -> row
    miss_issuers = set()
    dup = noyield = 0
    with open(CSV_PATH, encoding="utf-8", errors="replace", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            sd = (row.get("subscribe_date") or "").strip()
            if not (START <= sd <= end):
                continue
            if "信用债" not in (row.get("bond_category_desc") or ""):
                continue
            yld = (row.get("issue_yield") or "").strip()
            if not yld:
                noyield += 1
                continue
            try:
                y = float(yld)
            except Exception:
                continue
            if y <= 0:
                noyield += 1
                continue
            sid = (row.get("security_id") or "").strip()
            if not sid or sid in pending:
                dup += 1
                continue
            typ = (row.get("bond_type_desc") or "").strip()
            nm = (row.get("sec_short_name") or "").strip()
            if "PPN" in typ or "PPN" in nm or "定向" in typ:
                continue
            if "可转债" in typ or "可转换" in typ or "可交换" in typ or "转债" in nm or "可交换" in nm or "交换债" in nm or "EB" in nm:
                continue
            code = str(sid)
            pub = (row.get("public_offering_status") or "").strip()
            if re.match(r"^(283|520)", code) or "私募" in pub:
                continue
            mat = (row.get("bond_matu_struct") or "").strip()
            ten = (row.get("bond_issue_tenor") or "").strip()
            if is_perp(mat, ten):
                continue
            t = pre_tenor(mat, ten)
            if t is None or not (0 < t <= 30.5):   # 30.5 容差：30.02Y 等实际 30 年期归 30Y 档
                continue
            row["_t"] = t
            row["_c"] = y
            # 金额口径：实际发行额优先（DM actu_issue_amount，市场真实发行规模）；计划发行额作对照字段
            _plan = float(row.get("plan_issue_amount") or 0) or 0.0
            _actu = float(row.get("actu_issue_amount") or 0) or 0.0
            row["_plan"] = _plan / 10000.0
            row["_actu"] = _actu / 10000.0
            row["_amt"] = (_actu or _plan) / 10000.0
            pending[sid] = row
            iss = (row.get("issuer_full_name") or "").strip()
            if iss and iss not in yy_lookup and iss not in yy_cache:
                miss_issuers.add(iss)

    rows = list(pending.values())
    print(f"通过硬性筛选 {len(rows)} 只（无票面跳过 {noyield}、重复 {dup}）")

    # ---- DM YY 兜底（仅查缓存未命中主体） ----
    if miss_issuers:
        sys.path.insert(0, r"D:/DM API 实")
        from dm_quant_api_client import DMQuantApiClient  # noqa: E402
        client = DMQuantApiClient(app_key=os.environ["INNO_APP_KEY"],
                                  app_secret=os.environ["INNO_APP_SECRET"], pythonic=True)
        todo = sorted(miss_issuers)
        print(f"DM 评级查询缺 YY 主体 {len(todo)} 个（分批 5/次）...")
        got = dm_yy_lookup(client, todo)
        yy_cache.update(got)
        # 未解析到 YY 的主体写入 None 哨兵，避免每日重复查询
        for iss in todo:
            if iss not in yy_cache:
                yy_cache[iss] = None
        save_yy_cache(yy_cache)
        print(f"  DM YY 命中 {len(got)} 个主体；累计 YY 缓存 {len(yy_cache)}（含无 YY 哨兵）")

    # ---- 第二遍：YY 1-5 档分档聚合 ----
    agg = {name: {"count": 0, "amt": 0.0, "cw": 0.0, "tW": 0.0, "cMin": None, "cMax": None}
           for name, _, _ in BUCKETS}
    miss_yy = 0   # 主体无 YY（Excel 与 DM 均未解析到）
    over_yy = 0   # YY 明确超出样本范围（>5 档）
    bond_rows = []
    for r in rows:
        iss = (r.get("issuer_full_name") or "").strip()
        yy = yy_lookup.get(iss) or yy_cache.get(iss)
        if not yy:
            miss_yy += 1
            continue
        if not yy_in_scope(yy):
            over_yy += 1
            continue
        b = bucket_of(r["_t"])
        if b is None:
            continue
        a = agg[b]
        a["count"] += 1
        a["amt"] += r["_amt"]
        a["cw"] += r["_c"] * r["_amt"]
        a["tW"] += r["_t"] * r["_amt"]
        a["cMin"] = r["_c"] if a["cMin"] is None else min(a["cMin"], r["_c"])
        a["cMax"] = r["_c"] if a["cMax"] is None else max(a["cMax"], r["_c"])
        bond_rows.append({"name": (r.get("sec_short_name") or "").strip(),
                          "date": (r.get("subscribe_date") or "").strip(),
                          "issuer": iss, "yy": yy, "termY": round(r["_t"], 3),
                          "coupon": round(r["_c"], 4), "amtYi": round(r["_amt"], 2),
                          "planAmtYi": round(r["_plan"], 2), "actuAmtYi": round(r["_actu"], 2),
                          "bucket": b})

    # 个券明细（供前端悬浮浮框展示：年限/简称/票面/发行量），按发行量降序
    def bond_brief(br):
        return {"name": br["name"], "termY": br["termY"],
                "coupon": br["coupon"], "amtYi": br["amtYi"]}

    # 按 bucket 归组明细（每档按发行量降序，前端截断显示）
    bucket_bonds = {name: [] for name, _, _ in BUCKETS}
    for br in bond_rows:
        bucket_bonds[br["bucket"]].append(bond_brief(br))
    for name in bucket_bonds:
        bucket_bonds[name].sort(key=lambda x: x["amtYi"], reverse=True)

    buckets_out = []
    for name, _, _ in BUCKETS:
        a = agg[name]
        if not a["count"]:
            buckets_out.append({"term": name, "termY": None, "count": 0, "amountYi": 0,
                                "coupon": None, "couponMin": None, "couponMax": None, "bonds": []})
            continue
        buckets_out.append({
            "term": name,
            "termY": round(a["tW"] / a["amt"], 2),
            "count": a["count"],
            "amountYi": round(a["amt"], 2),
            "coupon": round(a["cw"] / a["amt"], 4),
            "couponMin": round(a["cMin"], 4),
            "couponMax": round(a["cMax"], 4),
            "bonds": bucket_bonds[name],
        })

    tot_amt = sum(a["amt"] for a in agg.values())
    tot_c = sum(a["cw"] for a in agg.values())
    tot_n = sum(a["count"] for a in agg.values())
    meta = {
        "source": "DM 一级发行主数据(raw_primary_history.csv) · 信用债",
        "generated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "range": f"{START}~{max((r.get('subscribe_date') or START) for r in rows) if rows else end}",
        "yyScope": "YY 评分 1-5 档（含 1-/1/1+/2-/2/2+/3-/3/3+/4-/4/4+/5-/5/5+）",
        "yySource": "每日一级发行 Excel YY 优先，DM 公司评级 YY 兜底",
        "clean": "信用债口径：剔 PPN/定向、私募、可转债/可交换、永续(+N)、无票面(未发行/取消)、行权前期限>30.5年；含权债以行权前期限分档",
        "bucket": "≤1Y、2Y、3Y、5Y、7Y、10Y、15Y、20Y、30Y（档间取关键期限中点切分）",
        "weight": "票面按发行金额加权；金额=DM 实际发行额（actu_issue_amount，与上市公告市场真实发行一致）；计划发行额见个券 planAmtYi（缩量/超募券与计划不同）",
        "bondCount": tot_n,
        "amountYi": round(tot_amt, 2),
        "couponAvg": round(tot_c / tot_amt, 4) if tot_amt else None,
        "skippedGt": over_yy,
        "skippedNoYY": miss_yy,
    }
    out = {"meta": meta, "buckets": buckets_out, "bonds": bond_rows}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    # ---- 逐日快照（前端日期叠加对比用）：每个簿记日 = 该日「当日发行」口径 ----
    # 说明：叠加对比场景用户按「当天是否发行」核对（如某日无 20Y 新发则该档为空），
    #       因此每个日期快照只统计该簿记日当日新发的 YY1-5 档个券（非 2026 年以来累计）。
    from collections import defaultdict
    by_day = defaultdict(list)
    for br in bond_rows:
        by_day[br["date"]].append(br)

    def snapshot_of(day_rows):
        day_agg = {name: {"count": 0, "amt": 0.0, "cw": 0.0, "tW": 0.0, "bonds": []}
                   for name, _, _ in BUCKETS}
        for br in day_rows:
            a = day_agg[br["bucket"]]
            a["count"] += 1
            a["amt"] += br["amtYi"]
            a["cw"] += br["coupon"] * br["amtYi"]
            a["tW"] += br["termY"] * br["amtYi"]
            a["bonds"].append({"name": br["name"], "termY": br["termY"],
                               "coupon": br["coupon"], "amtYi": br["amtYi"]})
        snap_buckets = []
        for name, _, _ in BUCKETS:
            a = day_agg[name]
            if not a["count"]:
                snap_buckets.append({"term": name, "termY": None, "count": 0,
                                     "amountYi": 0.0, "coupon": None, "bonds": []})
                continue
            a["bonds"].sort(key=lambda x: x["amtYi"], reverse=True)
            snap_buckets.append({
                "term": name,
                "termY": round(a["tW"] / a["amt"], 2),
                "count": a["count"],
                "amountYi": round(a["amt"], 2),
                "coupon": round(a["cw"] / a["amt"], 4),
                "bonds": a["bonds"],
            })
        tot_n = sum(a["count"] for a in day_agg.values())
        tot_amt = sum(a["amt"] for a in day_agg.values())
        tot_c = sum(a["cw"] for a in day_agg.values())
        return {
            "count": tot_n,
            "amountYi": round(tot_amt, 2),
            "couponAvg": round(tot_c / tot_amt, 4) if tot_amt else None,
            "buckets": snap_buckets,
        }

    series = {d: snapshot_of(rows) for d, rows in sorted(by_day.items())}
    date_keys = sorted(series.keys())
    by_date = {
        "dates": date_keys,
        "series": series,
        "meta": {
            "start": START,
            "end": date_keys[-1] if date_keys else end,
            "points": len(date_keys),
            "mode": "当日发行口径：每个日期=该簿记日当日新发的 YY1-5 档个券（非累计）",
            "generated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }
    with open(OUT_BY_DATE, "w", encoding="utf-8") as f:
        json.dump(by_date, f, ensure_ascii=False)
    print(f"\n逐日快照：{len(date_keys)} 个簿记日  {date_keys[0] if date_keys else '-'} ~ {date_keys[-1] if date_keys else '-'}")
    print("已写入", OUT_BY_DATE)
    print(f"\nYY1-5 档纳入 {tot_n} 只 / {round(tot_amt,1)} 亿，全档加权票面 {meta['couponAvg']}%")
    for b in buckets_out:
        if b["count"]:
            print(f"  {b['term']:5s} n={b['count']:4d} amt={b['amountYi']:9.1f}亿  加权票面 {b['coupon']:.4f}%  [{b['couponMin']:.2f}~{b['couponMax']:.2f}]")
    print(f"跳过（YY>5 档）: {over_yy} 只；跳过（主体无 YY 未解析）: {miss_yy} 只")
    print("已写入", OUT)


if __name__ == "__main__":
    main()
