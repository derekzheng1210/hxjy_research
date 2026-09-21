# -*- coding: utf-8 -*-
"""
发行人分析 · YTD 数据构建脚本
- 从 DM API 拉取 2026-01-01 ~ 今日 信用债一级发行（30 天窗口分页）
- 按全站信用债口径清洗（剔 PPN/定向、永续、私募 283/520、可转债/可交换）
- 按发行人聚合：只数、计划/实际规模、平均票面、品种、市场、区域、外部评级、YY 评分
- 输出：bond-dashboard/data/issuer_ytd.json
用法: python scripts/build_issuer_ytd.py [YYYY-MM-DD 截止日，默认今天]
"""
import os, sys, json, re, datetime as dt, time, collections

# 凭证从 bond-dashboard/.env.local 读取（不落盘、不打印）
HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.normpath(os.path.join(HERE, "..", ".env.local"))
if not os.path.exists(ENV_FILE):
    print("缺少 .env.local，无法读取 INNO_APP_KEY/INNO_APP_SECRET"); sys.exit(2)
with open(ENV_FILE, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

APP_KEY = os.environ.get("INNO_APP_KEY")
APP_SECRET = os.environ.get("INNO_APP_SECRET")
if not APP_KEY or not APP_SECRET:
    print("未找到 INNO_APP_KEY / INNO_APP_SECRET"); sys.exit(2)

sys.path.insert(0, r"D:/DM API 实")
from dm_quant_api_client import DMQuantApiClient  # noqa: E402

client = DMQuantApiClient(app_key=APP_KEY, app_secret=APP_SECRET, pythonic=True)
PATH = "/dm-quant-func-service/api/v1/bond/primary/data"
FIELD_NAMES = [
    "sec_short_name", "security_id", "subscribe_date", "bond_type_desc",
    "issuer_full_name", "province_name", "bond_issue_tenor", "bond_matu_struct",
    "public_offering_status",
    "plan_issue_amount", "actu_issue_amount", "issue_yield", "issue_status_desc",
]

START = "2026-01-01"
END = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()


def to_date(v):
    try:
        return dt.datetime.fromtimestamp(int(v) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return str(v)


def fetch_window(s, e):
    rows, offset = [], 0
    for _ in range(200):
        res = client.post_data(
            {"startDate": s, "endDate": e, "bondCategory": 1,
             "fieldNames": FIELD_NAMES, "offset": offset},
            api_path=PATH, return_type="dict")
        lst = res.get("list") or res.get("data") or []
        rows.extend(lst)
        mo = res.get("max_offset") or res.get("maxOffset")
        if mo is None or mo <= offset:
            break
        offset = mo
        time.sleep(0.15)
    return rows


def clean_reason(b):
    # 永续标志在 bond_matu_struct（如 5Y(5+N)），仅看 bond_issue_tenor 会漏剔
    perp = f'{b.get("bond_matu_struct") or ""} {b.get("bond_issue_tenor") or ""}'
    if re.search(r"\+\s*N", perp, re.I) or "永续" in perp or "永续" in str(b.get("sec_short_name") or ""):
        return "永续"
    t = str(b.get("bond_type_desc") or "")
    if "PPN" in t or "定向" in t:
        return "PPN"
    if "可交换" in t:
        return "可交换"
    if "可转" in t:
        return "可转债"
    code = str(b.get("security_id") or "")
    if re.match(r"^(283|520)", code) or "私募" in str(b.get("public_offering_status") or ""):
        return "私募"
    _nm = str(b.get("sec_short_name") or "")
    if "转债" in _nm or re.search(r"转\d*$", _nm):
        return "可转债"
    return None


def num(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except Exception:
        return 0.0


def market_of(code):
    c = str(code or "")
    if c.endswith((".IB", ".ib")) or ".IB" in c.upper() or c.startswith(("10", "11", "12")):
        return "银行间"
    if c.endswith((".SH", ".SZ")) or c.upper().endswith((".SH", ".SZ")):
        return "交易所"
    if "交易所" in c:
        return "交易所"
    return "其他"


def main():
    windows = []
    cur = dt.date.fromisoformat(START)
    end = dt.date.fromisoformat(END)
    while cur <= end:
        w_end = min(cur + dt.timedelta(days=29), end)
        windows.append((cur.isoformat(), w_end.isoformat()))
        cur = w_end + dt.timedelta(days=1)

    rows, seen = [], set()
    for s, e in windows:
        batch = fetch_window(s, e)
        for b in batch:
            sid = str(b.get("security_id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            b["_subscribe_date"] = to_date(b.get("subscribe_date"))
            b["_issue_yield"] = num(b.get("issue_yield"))
            b["_plan"] = num(b.get("plan_issue_amount"))
            b["_act"] = num(b.get("actu_issue_amount"))
            rows.append(b)
        print(f"窗口 {s}~{e}: {len(batch)} 条 / 累计 {len(rows)}")

    # 清洗
    kept, dropped = [], collections.Counter()
    for b in rows:
        if b["_subscribe_date"] < START or b["_subscribe_date"] > END:
            continue
        r = clean_reason(b)
        if r:
            dropped[r] += 1
            continue
        st = str(b.get("issue_status_desc") or "")
        if "取消" in st:
            dropped["取消发行"] += 1
            continue
        kept.append(b)
    print("剔除:", dict(dropped), "保留:", len(kept))

    issuers = collections.OrderedDict()
    bonds_by_issuer = collections.defaultdict(list)
    monthly = collections.defaultdict(lambda: [0, 0.0, 0.0])
    types = collections.Counter()
    type_plan = collections.defaultdict(float)
    markets = collections.Counter()
    market_plan = collections.defaultdict(float)

    for b in kept:
        name = (str(b.get("issuer_full_name") or "")).strip()
        if not name:
            name = "未知主体"
        amt = b["_act"] if b["_act"] > 0 else b["_plan"]
        amt_yi = amt / 10000.0
        sd = b["_subscribe_date"]
        ym = sd[:7]
        monthly[ym][0] += 1
        monthly[ym][1] += b["_plan"] / 10000.0
        monthly[ym][2] += b["_act"] / 10000.0
        t = str(b.get("bond_type_desc") or "其他")
        types[t] += 1
        type_plan[t] += amt_yi
        mk = market_of(b.get("security_id"))
        markets[mk] += 1
        market_plan[mk] += amt_yi

        g = issuers.setdefault(name, {
            "issuer": name, "cnt": 0, "planYi": 0.0, "actYi": 0.0,
            "couponSum": 0.0, "couponN": 0, "types": collections.Counter(),
            "markets": collections.Counter(), "province": "",
            "firstDate": sd, "lastDate": sd,
        })
        g["cnt"] += 1
        g["planYi"] += b["_plan"] / 10000.0
        g["actYi"] += b["_act"] / 10000.0
        if b["_issue_yield"] > 0:
            g["couponSum"] += b["_issue_yield"]
            g["couponN"] += 1
        g["types"][t] += 1
        g["markets"][mk] += 1
        bonds_by_issuer[name].append({
            "name": str(b.get("sec_short_name") or ""),
            "code": str(b.get("security_id") or ""),
            "tenor": str(b.get("bond_issue_tenor") or "") or None,
            "type": t,
            "planYi": round(b["_plan"] / 10000.0, 2),
            "actYi": round(b["_act"] / 10000.0, 2),
            "coupon": round(b["_issue_yield"], 3) if b["_issue_yield"] > 0 else None,
            "date": sd,
            "market": mk,
        })
        if not g["province"]:
            g["province"] = str(b.get("province_name") or "").strip()
        g["firstDate"] = min(g["firstDate"], sd)
        g["lastDate"] = max(g["lastDate"], sd)

    # 关联评级缓存（外部评级 + YY 评分）
    # 优先用 scripts/sync_issuer_ratings.py 生成的 issuer_ratings.json（YY 已归集本地主表、外部评级已用 DM 批量补全），
    # 缺失时回退到旧的 ratings.json。
    ratings = {}
    rating_file = os.path.normpath(os.path.join(HERE, "..", "data", "cache", "ratings.json"))
    if os.path.exists(rating_file):
        with open(rating_file, encoding="utf-8") as f:
            ratings = json.load(f)
    ir_file = os.path.normpath(os.path.join(HERE, "..", "data", "cache", "issuer_ratings.json"))
    if os.path.exists(ir_file):
        with open(ir_file, encoding="utf-8") as f:
            ir_map = (json.load(f) or {}).get("map") or {}
        for k, v in ir_map.items():
            cur = ratings.setdefault(k, {})
            if v.get("yy"):
                cur["yy"] = v["yy"]
            if v.get("external"):
                cur["external"] = v["external"]
            if v.get("agency"):
                cur["agency"] = v["agency"]

    out_issuers = []
    for name, g in issuers.items():
        top_type = g["types"].most_common(1)[0][0] if g["types"] else None
        top_market = g["markets"].most_common(1)[0][0] if g["markets"] else None
        rt = ratings.get(name) or {}
        out_issuers.append({
            "issuer": name,
            "cnt": g["cnt"],
            "planYi": round(g["planYi"], 2),
            "actYi": round(g["actYi"], 2),
            "couponAvg": round(g["couponSum"] / g["couponN"], 2) if g["couponN"] else None,
            "topType": top_type,
            "topMarket": top_market,
            "exCount": g["markets"].get("交易所", 0),
            "province": g["province"],
            "ratingExt": rt.get("external") or None,
            "yy": rt.get("yy") or None,
            "firstDate": g["firstDate"],
            "lastDate": g["lastDate"],
        })

    out_issuers.sort(key=lambda x: (-x["planYi"], -x["cnt"]))
    monthly_arr = [{"ym": ym, "cnt": v[0], "planYi": round(v[1], 2), "actYi": round(v[2], 2)}
                   for ym, v in sorted(monthly.items())]
    types_arr = [{"type": t, "cnt": c, "planYi": round(type_plan[t], 2)}
                 for t, c in types.most_common(12)]
    markets_arr = [{"market": m, "cnt": markets[m], "planYi": round(market_plan[m], 2)}
                   for m in sorted(markets)]

    plan_total = sum(x["planYi"] for x in out_issuers)
    act_total = sum(x["actYi"] for x in out_issuers)

    # 个券明细按主体索引（期限从长到短，便于发行人下拉展开研究单一主体）
    def tenor_key(s):
        t = str(s or "")
        m = re.search(r"([\d.]+)\s*([YMDW])?", t, re.I)
        if not m:
            return -1.0
        v = float(m.group(1))
        u = (m.group(2) or "Y").upper()
        if u == "D":
            v /= 365.0
        elif u == "W":
            v /= 52.0
        elif u == "M":
            v /= 12.0
        return v

    for nm in bonds_by_issuer:
        bonds_by_issuer[nm].sort(key=lambda x: (-tenor_key(x["tenor"]), x["date"], x["name"]))
    bonds_out = {
        "meta": {
            "generated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "range": f"{START}~{END}",
            "issuerCount": len(bonds_by_issuer),
            "bondCount": sum(len(v) for v in bonds_by_issuer.values()),
        },
        "issuers": {k: bonds_by_issuer[k] for k in sorted(bonds_by_issuer)},
    }
    bonds_path = os.path.normpath(os.path.join(HERE, "..", "data", "issuer_bonds.json"))
    with open(bonds_path, "w", encoding="utf-8") as f:
        json.dump(bonds_out, f, ensure_ascii=False)
    print("已写入", bonds_path, "主体", len(bonds_by_issuer))
    out = {
        "meta": {
            "source": "DM 一级发行(primary/data) 信用债",
            "generated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "range": f"{START}~{END}",
            "clean": "信用债口径：剔 PPN/定向、永续、私募(283/520)、可转债/可交换、取消发行",
            "issuerCount": len(out_issuers),
            "bondCount": len(kept),
            "planYi": round(plan_total, 2),
            "actYi": round(act_total, 2),
        },
        "monthly": monthly_arr,
        "types": types_arr,
        "markets": markets_arr,
        "issuers": out_issuers,
    }
    out_path = os.path.normpath(os.path.join(HERE, "..", "data", "issuer_ytd.json"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("\n主体数:", len(out_issuers), "券数:", len(kept))
    print("计划规模合计(亿):", round(plan_total, 1), "实际(亿):", round(act_total, 1))
    print("Top5:", [(x["issuer"], x["planYi"], x["cnt"]) for x in out_issuers[:5]])
    print("月份:", [(m["ym"], m["cnt"]) for m in monthly_arr])
    print("类型Top5:", types_arr[:5])
    print("市场:", markets_arr)
    print("已写入", out_path)


if __name__ == "__main__":
    main()
