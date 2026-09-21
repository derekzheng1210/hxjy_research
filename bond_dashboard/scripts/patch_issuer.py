# -*- coding: utf-8 -*-
"""
推荐个券"发行主体"数据补全：从 DM primary/data 按日拉取，按简称匹配 issuer_full_name
写入 recommended.json 的 issuer 字段（Excel 已有发行人时以 DM 为准覆盖；DM 无则保留原值）
"""
import os, sys, json, re, time

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

rec_path = os.path.join(BASE, "recommended.json")
rec = json.load(open(rec_path, encoding="utf-8"))
dates = sorted(set(b["date"] for b in rec))
print(f"推荐个券 {len(rec)} 只，涉及 {len(dates)} 天")

issuer_map = {}
for i, d in enumerate(dates, 1):
    try:
        res = client.post_data({"startDate": d, "endDate": d, "bondCategory": 1, "offset": 0},
            api_path=PATH, return_type="dict")
        rows = res.get("list") or res.get("data") or []
        for r in rows:
            nm = r.get("sec_short_name")
            if nm:
                issuer_map[nm] = r.get("issuer_full_name")
    except Exception as e:
        print(f"[{i}/{len(dates)}] {d} 失败: {repr(e)[:80]}")
    time.sleep(0.1)
print(f"DM 发行人映射: {len(issuer_map)}")

updated, kept = 0, 0
for b in rec:
    dm_issuer = issuer_map.get(b["name"])
    if dm_issuer:
        if b.get("issuer") != dm_issuer:
            b["issuer"] = dm_issuer
            updated += 1
        else:
            kept += 1
    # DM 无匹配但 Excel 有 → 保留原值（kept 分支不计数）

with open(rec_path, "w", encoding="utf-8") as f:
    json.dump(rec, f, ensure_ascii=False, indent=1)

print(f"DM 更新发行人: {updated} 只；DM 与 Excel 一致: {kept} 只；DM 无: {len(rec) - updated - kept} 只")
# 验证
no_issuer = [b for b in rec if not b.get("issuer")]
print(f"仍无发行人: {len(no_issuer)} 只")
for b in rec[:3]:
    print(f"  {b['name']} → {b['issuer']}")
