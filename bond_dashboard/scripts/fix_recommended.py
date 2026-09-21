# -*- coding: utf-8 -*-
"""
推荐个券修正脚本：
1. 用 DM primary/data 按日拉取，按简称匹配 → 补债券代码（security_id）
2. 补最终发行规模（actu_issue_amount，万元→亿；回拨修正）
3. 更新 recommended.json
4. 重跑估值（market-data/date cb_ytm/cs_ytm + basic-info 场所判断）→ 更新 valuations.json
"""
import os, sys, json, re, datetime as dt, time

sys.path.insert(0, "C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Lib/site-packages")
from dm_quant_api_client import DMQuantApiClient

APP_KEY = os.environ.get("INNO_APP_KEY")
APP_SECRET = os.environ.get("INNO_APP_SECRET")
if not APP_KEY or not APP_SECRET:
    print("请设置 INNO_APP_KEY / INNO_APP_SECRET")
    sys.exit(2)

BASE = "C:/Users/yoyo1/WorkBuddy/2026-09-01-16-51-51/bond-dashboard/data"
client = DMQuantApiClient(app_key=APP_KEY, app_secret=APP_SECRET, pythonic=True)
PATH = "/dm-quant-func-service/api/v1/bond/primary/data"

def prev_workday():
    d = dt.date.today() - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()

def fetch_day(d):
    rows, offset = [], 0
    for _ in range(10):
        res = client.post_data({"startDate": d, "endDate": d, "bondCategory": 1, "offset": offset},
            api_path=PATH, return_type="dict")
        lst = res.get("list") or res.get("data") or []
        rows.extend(lst)
        mo = res.get("max_offset") or res.get("maxOffset")
        if mo is None or mo <= offset:
            break
        offset = mo
        time.sleep(0.15)
    return rows

# ==================== 1. 读推荐个券 ====================
rec_path = os.path.join(BASE, "recommended.json")
rec = json.load(open(rec_path, encoding="utf-8"))
print(f"推荐个券: {len(rec)} 只")

dates = sorted(set(b["date"] for b in rec if b.get("date")))
print(f"涉及日期: {len(dates)} 天 ({dates[0]} ~ {dates[-1]})")

# ==================== 2. 按日拉取并匹配 ====================
name_map = {}  # 简称 -> {security_id, actu_issue_amount(万元), plan_issue_amount(万元)}
for i, d in enumerate(dates, 1):
    try:
        rows = fetch_day(d)
    except Exception as e:
        print(f"[{i}/{len(dates)}] {d} 失败: {repr(e)[:100]}")
        continue
    for r in rows:
        nm = r.get("sec_short_name")
        if nm:
            name_map[nm] = {
                "security_id": r.get("security_id"),
                "actu": r.get("actu_issue_amount"),
                "plan": r.get("plan_issue_amount"),
            }
    print(f"[{i}/{len(dates)}] {d}: {len(rows)} 条，累计匹配 {len(name_map)}")
    time.sleep(0.15)

# ==================== 3. 更新推荐券 ====================
fixed_code, fixed_actu, no_match = 0, 0, 0
for b in rec:
    info = name_map.get(b["name"])
    if info:
        sid = info.get("security_id")
        # 补代码（原代码为空或格式异常（询价代码/纯数字））
        old = b.get("code")
        if not old or not re.match(r"^\d{6,}\.(IB|SH|SZ)$", str(old)):
            if sid:
                b["code"] = sid
                fixed_code += 1
        # 补最终发行规模（万元→亿）
        actu = info.get("actu")
        plan = info.get("plan")
        final = None
        if actu is not None and isinstance(actu, (int, float)) and actu > 0:
            final = round(actu / 10000, 2)
        elif plan is not None and isinstance(plan, (int, float)) and plan > 0:
            final = round(plan / 10000, 2)
        if final is not None:
            old_plan = b.get("plan")
            if b.get("actual") != final or (old_plan is not None and abs(final - old_plan) > 0.001):
                fixed_actu += 1
            b["actual"] = final
    else:
        no_match += 1
        b.setdefault("actual", b.get("plan"))

print(f"补代码: {fixed_code} 只；规模修正/新增: {fixed_actu} 只；未匹配: {no_match} 只")

with open(rec_path, "w", encoding="utf-8") as f:
    json.dump(rec, f, ensure_ascii=False, indent=1)
print("recommended.json 已更新")

# ==================== 4. 估值补足 ====================
print("\n=== 估值补足 ===")
val_path = os.path.join(BASE, "valuations.json")
vals = json.load(open(val_path, encoding="utf-8"))
DATE = prev_workday()
print(f"估值日期(上一工作日): {DATE}")

# 收集推荐券代码
ids = []
for b in rec:
    code = b.get("code")
    if code and re.match(r"^\d{6,}\.(IB|SH|SZ)$", str(code)):
        ids.append(code)
ids = list(dict.fromkeys(ids))
print(f"推荐券有效代码: {len(ids)}")

# 4a. 发行场所（basic-info，每批 20）
market_map = {}
for i in range(0, len(ids), 20):
    try:
        rows = client.post_data({"securityIdList": ids[i:i+20]},
            api_path="/dm-quant-func-service/api/v1/bond/basic-info/info", return_type="dict")
        for r in rows:
            market_map[r.get("security_id")] = {"market": r.get("secondary_market"), "cros": r.get("is_cros_mar")}
    except Exception as e:
        print(f"basic-info 批次失败 {i}: {repr(e)[:100]}")
    time.sleep(0.15)

# 4b. 估值（market-data/date，每批 5）
val_map = {}
for i in range(0, len(ids), 5):
    try:
        rows = client.post_data({"securityIdList": ids[i:i+5], "dataSourceList": [2],
            "startDate": DATE, "endDate": DATE},
            api_path="/dm-quant-func-service/api/v1/bond/market-data/date", return_type="dict")
        rows = rows if isinstance(rows, list) else (rows.get("list") or [])
        for r in rows:
            val_map[r.get("security_id")] = {"cb_ytm": r.get("cb_ytm"), "cs_ytm": r.get("cs_ytm")}
    except Exception as e:
        print(f"估值批次失败 {i}: {repr(e)[:100]}")
    time.sleep(0.1)
print(f"估值获取: {len(val_map)}/{len(ids)}")

def extract(sid):
    info = market_map.get(sid, {})
    market = str(info.get("market") or "")
    cros = str(info.get("cros") or "")
    v = val_map.get(sid, {})
    cb, cs = v.get("cb_ytm"), v.get("cs_ytm")
    is_interbank = "银行间" in market
    is_exchange = ("交易所" in market) or ("证券交易" in market)
    is_cros = "是" in cros
    if is_cros or (is_interbank and is_exchange):
        return {"cb": cb, "cs": cs, "market": market or "双市场"}
    if is_interbank:
        return {"cb": cb, "cs": None, "market": market}
    if is_exchange:
        return {"cb": None, "cs": cs, "market": market}
    return {"cb": cb, "cs": cs, "market": market or "未知"}

bonds = vals.get("bonds", {})
for b in rec:
    code = b.get("code")
    name = b["name"]
    entry = bonds.setdefault(name, {"security_id": None, "cb": None, "cs": None, "market": None})
    if code and re.match(r"^\d{6,}\.(IB|SH|SZ)$", str(code)):
        entry["security_id"] = code
        r = extract(code)
        entry["cb"] = r["cb"]
        entry["cs"] = r["cs"]
        entry["market"] = r["market"]

vals["meta"]["date"] = DATE
vals["meta"]["generated"] = dt.datetime.now().isoformat()
with open(val_path, "w", encoding="utf-8") as f:
    json.dump(vals, f, ensure_ascii=False, indent=1)

have_cb = sum(1 for v in bonds.values() if v.get("cb") is not None)
have_cs = sum(1 for v in bonds.values() if v.get("cs") is not None)
print(f"valuations.json 已更新：{len(bonds)} 只，中债 {have_cb}，中证 {have_cs}")
