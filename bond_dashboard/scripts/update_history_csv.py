# -*- coding: utf-8 -*-
"""
增量更新 D:/DM API 实/raw_primary_history.csv（历史一级发行主数据）
================================================================
- 从 CSV 当前最大日期前推缓冲（15 天）开始，拉取至 [END=今天] 的信用债一级发行
- 30 天窗口分批 + 分页（与 fetch_history_primary.py 同接口同字段）
- 与现有 CSV 合并：按 security_id 去重（新拉数据优先），旧文件备份 .bak
- 凭证从 bond-dashboard/.env.local 读取（INNO_APP_KEY / INNO_APP_SECRET）

用法:
  "<venv>/python.exe" scripts/update_history_csv.py [YYYY-MM-DD 截止日, 默认今天] [YYYY-MM-DD 起始日, 可选]
  默认起始 = CSV 最大日期前推 15 天（增量）；传入起始日则从该日拉至截止日（覆盖刷新，用于修正历史快照）
之后运行 scripts/build_history_trends.py 重算历史趋势。
"""
import os, sys, re, csv, json, shutil, datetime as dt, time

sys.path.insert(0, "C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Lib/site-packages")

CSV_PATH = r"D:/DM API 实/raw_primary_history.csv"
HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.normpath(os.path.join(HERE, "..", ".env.local"))

# 从 .env.local 读取 DM 凭据（不落盘、不打印）
os.environ.setdefault("INNO_APP_KEY", "")
os.environ.setdefault("INNO_APP_SECRET", "")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
APP_KEY = os.environ.get("INNO_APP_KEY")
APP_SECRET = os.environ.get("INNO_APP_SECRET")
if not APP_KEY or not APP_SECRET:
    print("ERROR: .env.local 缺少 INNO_APP_KEY/INNO_APP_SECRET")
    sys.exit(2)

from dm_quant_api_client import DMQuantApiClient  # noqa: E402

PATH = "/dm-quant-func-service/api/v1/bond/primary/data"
FIELD_NAMES = [
    "sec_short_name", "security_id", "issue_start_date", "subscribe_date",
    "subscribe_time", "bond_category_desc", "bond_type_desc", "issuer_full_name",
    "province_name", "city_name", "gura_name", "unde_name",
    "bond_issue_tenor", "bond_matu_struct", "plan_issue_amount",
    "actu_issue_amount", "issue_yield", "subscribe_rate",
    "compliant_subscription_mult", "marginal_mult", "weighted_rate",
    "marginal_rate", "public_offering_status", "issue_status", "issue_status_desc",
    "issue_price_forecast", "similar_bond_code", "similar_bond_short_name",
    "similar_bond_remaining_tenor", "similar_bond_cb_valuation",
    "similar_bond_bid_price", "similar_bond_ofr_price", "pay_date",
]

client = DMQuantApiClient(app_key=APP_KEY, app_secret=APP_SECRET, pythonic=True)


def to_date(v):
    try:
        return dt.datetime.fromtimestamp(int(v) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return v


def csv_max_date():
    mx = "2000-01-01"
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            for c in ("issue_start_date", "subscribe_date", "pay_date"):
                d = (row.get(c) or "").strip()
                if re.match(r"20\d{2}-\d{2}-\d{2}", d) and d > mx:
                    mx = d
    return mx


def fetch_window(s, e):
    rows = []
    offset = 0
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
        time.sleep(0.2)
    return rows


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    end = args[0] if len(args) > 0 and re.match(r"20\d{2}-\d{2}-\d{2}", args[0]) else dt.date.today().isoformat()
    start_override = args[1] if len(args) > 1 and re.match(r"20\d{2}-\d{2}-\d{2}", args[1]) else None
    if not os.path.exists(CSV_PATH):
        print("ERROR: CSV 不存在:", CSV_PATH)
        sys.exit(1)

    # 起始：默认 CSV 最大日期前推 15 天；可传入起始日强制覆盖刷新
    cur_max = csv_max_date()
    if start_override:
        start_d = dt.date.fromisoformat(start_override)
        print(f"覆盖刷新模式：{start_override} ~ {end}（DM 返回新值将覆盖旧行）")
    else:
        start_d = dt.date.fromisoformat(cur_max) - dt.timedelta(days=15)
    end_d = dt.date.fromisoformat(end)
    if end_d <= start_d:
        print(f"CSV 已最新至 {cur_max}，无需增量。")
        return

    # 切 30 天窗口
    windows = []
    cur = start_d
    while cur <= end_d:
        w_end = min(cur + dt.timedelta(days=29), end_d)
        windows.append((cur.isoformat(), w_end.isoformat()))
        cur = w_end + dt.timedelta(days=1)
    print(f"增量拉取 {start_d.isoformat()} ~ {end_d.isoformat()}，共 {len(windows)} 个窗口")

    all_rows = []
    for i, (s, e) in enumerate(windows, 1):
        try:
            rows = fetch_window(s, e)
        except Exception as ex:
            print(f"[{i}/{len(windows)}] {s}~{e} 出错: {type(ex).__name__}: {ex}")
            time.sleep(2)
            continue
        print(f"[{i}/{len(windows)}] {s}~{e}: {len(rows)} 条")
        all_rows.extend(rows)
        time.sleep(0.3)

    if not all_rows:
        print("未拉到新数据，退出。")
        return

    # 日期归一
    for r in all_rows:
        for c in ("issue_start_date", "subscribe_date", "pay_date"):
            if c in r and r[c] is not None:
                r[c] = to_date(r[c])

    # 读旧 CSV → 合并去重（新数据覆盖旧数据：DM 后期修正发行额/状态等能回流）
    # 注：旧实现为 setdefault(旧优先)，导致 DM 对已上市券的 actu 金额修正永不落库，须改为覆盖式。
    old = []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        old = list(csv.DictReader(f))
    cols = old[0].keys() if old else FIELD_NAMES
    merged = {}
    for r in old:
        merged.setdefault(r.get("security_id", ""), r)  # 先铺旧行作底（防窗口外券丢失）
    updated = 0
    added = 0
    for r in all_rows:
        sid = r.get("security_id", "")
        if not sid:
            continue
        if sid in merged:
            # 新值覆盖旧值（仅更新到旧列集，保留未拉取列的旧内容）
            merged[sid].update({c: (r.get(c) if c in r else merged[sid].get(c, "")) for c in cols})
            updated += 1
        else:
            merged[sid] = {c: (r.get(c) if c in r else "") for c in cols}
            added += 1
    print(f"旧 CSV {len(old)} 行，新增 {added} 行，覆盖更新 {updated} 行，合并后 {len(merged)} 行")

    if added == 0 and updated == 0:
        print("无变化，不重写 CSV（保持文件时间戳不变）")
        return

    # 备份 + 写回（保持原列顺序）
    bak = CSV_PATH + ".bak"
    shutil.copyfile(CSV_PATH, bak)
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cols))
        w.writeheader()
        w.writerows(merged.values())
    print(f"已更新 {CSV_PATH}（备份 {bak}）")

    # 概览
    years = {}
    for r in merged.values():
        y = (r.get("issue_start_date") or r.get("subscribe_date") or "")[:4]
        years[y] = years.get(y, 0) + 1
    print("年度分布:", dict(sorted(years.items())))


if __name__ == "__main__":
    main()
