# -*- coding: utf-8 -*-
"""
中标组合时间序列 + 30Y 国债收益率构建脚本
- 30Y 国债收益率：DM「中债国债收益率曲线」term=30，按自然日分段(≤12天)增量拉取，缓存 data/gov30y.json{date:yield}
- 组合序列：自首笔中标日起的每个交易日（以 30Y 数据日期为准），滚动累计「截至该日已中标」组合的
  amountSum(亿元)/wCoupon(加权票面)/wTerm(加权期限·行权前)/DV01(万元)
- DV01 口径与中标页 KPI 一致：修正久期=年付息+期末还本，以该券对应估值(含权取行权)折现；估值缺失券以票面折现兜底
- 输出：data/portfolio_series.json
用法: python scripts/build_portfolio_series.py [截止日 YYYY-MM-DD，默认=valuations meta.date]
"""
import os, sys, json, re, time, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.normpath(os.path.join(HERE, ".."))
ENV_FILE = os.path.join(BASE, ".env.local")

# ---------- 凭证 ----------
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

sys.path.insert(0, r"D:/DM API 实")
from dm_quant_api_client import DMQuantApiClient  # noqa: E402

client = DMQuantApiClient(app_key=os.environ["INNO_APP_KEY"],
                          app_secret=os.environ["INNO_APP_SECRET"], pythonic=True)
YC_PATH = "/dm-quant-func-service/api/v1/bond/yield-curve/data"

GOV_FILE = os.path.join(BASE, "data", "gov30y.json")
OUT = os.path.join(BASE, "data", "portfolio_series.json")


# ---------- 工具 ----------
def term_years(t):
    m = re.match(r"^(\d+(?:\.\d+)?)", str(t or "").strip())
    return float(m.group(1)) if m else None


def mod_duration(T, coupon, y_pct):
    """年付息、期末还本、面值100；返回(修正久期, 理论价)"""
    n = max(1, int(round(T)))
    r = 1 + y_pct / 100.0
    price = sum(coupon / (r ** t) for t in range(1, n)) + (100 + coupon) / (r ** n)
    mac = sum(t * coupon / (r ** t) for t in range(1, n)) + n * (100 + coupon) / (r ** n)
    mac /= price
    return mac / r, price


def dv01_wan(amount, dur, price):
    return amount * dur * price / 100.0


# ---------- 30Y 国债收益率（增量） ----------
def fetch_y30(s, e):
    res = client.post_data(
        {"dataSource": "18", "curveName": "中债国债收益率曲线",
         "curveTermList": ["30"], "curveType": "1", "startDate": s, "endDate": e},
        api_path=YC_PATH, return_type="dict")
    if isinstance(res, list):
        lst = res
    elif isinstance(res, dict):
        lst = res.get("list") or res.get("data") or []
    else:
        lst = []
    out = {}
    for r in lst:
        d = str(r.get("valuation_date") or "")[:10]
        y = r.get("yield")
        if d and y is not None:
            out[d] = float(y)
    return out


def load_gov():
    if os.path.exists(GOV_FILE):
        return json.load(open(GOV_FILE, encoding="utf-8"))
    return {"meta": {"source": "DM 中债国债收益率曲线 30Y"}, "map": {}}


def refresh_gov(end):
    gov = load_gov()
    m = gov["map"]
    have = sorted(m.keys())
    if have and have[-1] >= end:
        return gov, have[-1], 0
    start = (have[-1] + " 00:00:00") if have else "2026-07-01"
    if have:
        start = (dt.datetime.strptime(have[-1], "%Y-%m-%d") + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    pulled = 0
    s = start
    while s <= end:
        e = min(end, (dt.datetime.strptime(s, "%Y-%m-%d") + dt.timedelta(days=11)).strftime("%Y-%m-%d"))
        try:
            m.update(fetch_y30(s, e))
            pulled += 1
        except Exception as ex:
            print("  30Y 拉取失败", s, e, ex)
        time.sleep(0.3)
        s = (dt.datetime.strptime(e, "%Y-%m-%d") + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    gov["meta"]["updated"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    gov["map"] = dict(sorted(m.items()))
    with open(GOV_FILE, "w", encoding="utf-8") as f:
        json.dump(gov, f, ensure_ascii=False)
    return gov, start, pulled


# ---------- 组合序列 ----------
def build(end):
    bids = json.load(open(os.path.join(BASE, "data", "store", "bids.json"), encoding="utf-8"))
    rows = bids if isinstance(bids, list) else bids.get("list", [])
    won = [r for r in rows if r.get("type") == "won" and r.get("amount")]
    valuations = json.load(open(os.path.join(BASE, "data", "valuations.json"), encoding="utf-8"))
    bonds = valuations.get("bonds", {})
    vdate = str((valuations.get("meta") or {}).get("date") or "")[:10]

    gov, _, pulled = refresh_gov(end)

    # 预计算每笔券：年限 / 对应估值 / 单券DV01
    items = []
    for r in won:
        name = r["bondName"]
        amt = float(r["amount"])
        c = r.get("coupon")
        T = term_years(r.get("term"))
        if c is None or T is None:
            continue
        v = bonds.get(name) or {}
        y = v.get("cb")
        if y is None:
            y = v.get("cs")
        if y is None:
            y = float(c)  # 兜底：票面折现
        dur, price = mod_duration(T, float(c), float(y))
        items.append({
            "date": str(r.get("bidDate") or "")[:10],
            "amount": amt, "coupon": float(c), "term": T, "dv01": dv01_wan(amt, dur, price),
        })

    items = [it for it in items if it["date"]]
    if not items:
        print("无中标记录"); return
    first = min(it["date"] for it in items)
    days = sorted(d for d in gov["map"] if d >= first)
    if not days:
        days = sorted(gov["map"].keys())

    points = []
    cur = {"amount": 0.0, "wNum": 0.0, "cSum": 0.0, "tSum": 0.0, "tW": 0.0, "dv01": 0.0, "cnt": 0}
    bucket = sorted(items, key=lambda x: x["date"])
    idx = 0
    for d in days:
        while idx < len(bucket) and bucket[idx]["date"] <= d:
            it = bucket[idx]
            a = it["amount"]
            cur["amount"] += a
            cur["cnt"] += 1
            cur["cSum"] += a * it["coupon"]
            cur["tSum"] += a * it["term"]
            cur["dv01"] += it["dv01"]
            idx += 1
        if cur["cnt"] == 0:
            continue
        points.append({
            "date": d,
            "amount": round(cur["amount"], 2),
            "wCoupon": round(cur["cSum"] / cur["amount"], 4),
            "wTerm": round(cur["tSum"] / cur["amount"], 4),
            "dv01": round(cur["dv01"], 1),
            "t30": gov["map"].get(d),
        })

    out = {
        "meta": {
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "valuationDate": vdate,
            "range": [points[0]["date"], points[-1]["date"]] if points else None,
            "pointCount": len(points),
            "note": "加权票面/期限与 DV01 为截至当日已中标组合、权重=中标量；DV01 历史点按当前最新估值回溯（久期=行权前期限、年付息、期末还本）；t30=中债国债30Y收益率(DM)。",
        },
        "points": points,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"已输出 {len(points)} 点 [{points[0]['date']} ~ {points[-1]['date']}] → {os.path.normpath(OUT)}")
    if points:
        p = points[-1]
        print(f"  最新 {p['date']}: 合计{p['amount']}亿 加权票面{p['wCoupon']}% 加权期限{p['wTerm']}Y DV01={p['dv01']}万 30Y国债={p['t30']}%")
    if pulled:
        print(f"  30Y 增量拉取 {pulled} 段，缓存 {len(gov['map'])} 个交易日")


if __name__ == "__main__":
    end = sys.argv[1] if len(sys.argv) > 1 else None
    if not end:
        try:
            end = str((json.load(open(os.path.join(BASE, "data", "valuations.json"), encoding="utf-8")).get("meta") or {}).get("date") or "")[:10]
        except Exception:
            end = ""
    if not end:
        end = dt.date.today().isoformat()
    print("截止日:", end)
    build(end)
