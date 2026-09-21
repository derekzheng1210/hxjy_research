# -*- coding: utf-8 -*-
"""探测 DM market-data/date：dataSourceList 含义 + 9-10 估值是否已出 + 各估值字段返回情况"""
import os, sys, json

sys.path.insert(0, "C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Lib/site-packages")
from dm_quant_api_client import DMQuantApiClient

ENV = r"C:/Users/yoyo1/WorkBuddy/2026-09-01-16-51-51/bond-dashboard/.env.local"
with open(ENV, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

client = DMQuantApiClient(app_key=os.environ["INNO_APP_KEY"], app_secret=os.environ["INNO_APP_SECRET"], pythonic=True)

# 样本：银行间 1 只 + 交易所 1 只
samples = ["102683030.IB", "245726.SH"]  # 26金融街MTN004A(银行间) / 26锡交03(交易所)

for ds in ([1], [2], [1, 2]):
    print(f"\n===== dataSourceList={ds} =====")
    rows = client.post_data({"securityIdList": samples, "dataSourceList": ds,
                             "startDate": "2026-09-08", "endDate": "2026-09-10"},
                            api_path="/dm-quant-func-service/api/v1/bond/market-data/date",
                            return_type="dict")
    rows = rows if isinstance(rows, list) else (rows.get("list") or [])
    print(f"返回 {len(rows)} 行")
    for r in rows:
        keep = {k: v for k, v in r.items() if v not in (None, "") and k not in ("security_id", "issue_date")}
        print(f"  {r.get('security_id')} @ {r.get('issue_date')}: {json.dumps(keep, ensure_ascii=False, default=str)[:500]}")
