# -*- coding: utf-8 -*-
"""
从 DM 抓取个券估值（优先当日，未发布则回退上一工作日）：中债（cb_ytm/cb_yte）+ 中证（cs_ytm/cs_yte）
+ 发行场所（secondary_market），按规则提取估值后写入 data/valuations.json

规则（2026-09 修订）：
- 含权债一律取「行权收益率」（cb_yte/cs_yte，即 yte）；无行权口径（非含权）取 ytm
- 仅银行间 → 只取中债估值
- 仅交易所 → 只取中证估值
- 银行间+交易所（双市场）→ 中债 + 中证都取

数据源：
- 参与/中标：data/store/bids.json（securityId）
- 推荐个券：data/recommended.json（code）
"""
import os, sys, json, re, datetime as dt, time

sys.path.insert(0, "C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Lib/site-packages")
from dm_quant_api_client import DMQuantApiClient

APP_KEY = os.environ.get("INNO_APP_KEY")
APP_SECRET = os.environ.get("INNO_APP_SECRET")
# 本机凭据存于 bond-dashboard/.env.local（不落盘、不打印）
if not APP_KEY or not APP_SECRET:
    ENV_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env.local"))
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        APP_KEY = os.environ.get("INNO_APP_KEY")
        APP_SECRET = os.environ.get("INNO_APP_SECRET")
if not APP_KEY or not APP_SECRET:
    print("请设置 INNO_APP_KEY / INNO_APP_SECRET")
    sys.exit(2)

BASE = "C:/Users/yoyo1/WorkBuddy/2026-09-01-16-51-51/bond-dashboard/data"
client = DMQuantApiClient(app_key=APP_KEY, app_secret=APP_SECRET, pythonic=True)

def prev_workday():
    d = dt.date.today()
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()

def today():
    return dt.date.today().isoformat()

def clean_code(c):
    """清洗债券代码为 DM 标准格式"""
    if not c:
        return None
    c = str(c).strip()
    # 已有交易所后缀
    if c.endswith((".IB", ".SH", ".SZ")):
        return c
    # 纯数字：推断后缀（8位纯数字IB / 6位SH/SZ）
    if re.fullmatch(r"\d+", c):
        if len(c) >= 8:
            return c + ".IB"
        return c + ".SH"
    return c

# ==================== 收集个券 ====================
bonds = {}  # name -> {security_id, source}
for fn in ["store/bids.json", "recommended.json"]:
    try:
        with open(os.path.join(BASE, fn), encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        continue
    for r in rows:
        name = r.get("bondName") or r.get("name")
        code = r.get("securityId") or r.get("code")
        if not name:
            continue
        if name not in bonds:
            bonds[name] = {"security_id": clean_code(code), "source": fn}
        elif not bonds[name]["security_id"]:
            bonds[name]["security_id"] = clean_code(code)

ids = [b["security_id"] for b in bonds.values() if b["security_id"]]
print(f"个券总数: {len(bonds)}，有代码: {len(ids)}")
# 检查代码格式分布
fmts = {}
for c in ids:
    suf = c.split(".")[-1] if "." in c else "无后缀"
    fmts[suf] = fmts.get(suf, 0) + 1
print("代码后缀分布:", fmts)

# ==================== 1. 发行场所（basic-info，每批20） ====================
DATE = prev_workday()
market_map = {}
uniq_ids = list(dict.fromkeys(ids))
for i in range(0, len(uniq_ids), 20):
    chunk = uniq_ids[i:i + 20]
    try:
        rows = client.post_data({"securityIdList": chunk},
            api_path="/dm-quant-func-service/api/v1/bond/basic-info/info", return_type="dict")
        for r in rows:
            market_map[r.get("security_id")] = {
                "market": r.get("secondary_market"),
                "cros": r.get("is_cros_mar"),
                "name": r.get("sec_short_name"),
            }
    except Exception as e:
        print(f"basic-info 批次失败 {i}: {repr(e)[:120]}")
    time.sleep(0.2)
print(f"发行场所获取: {len(market_map)}/{len(uniq_ids)}")

# ==================== 2. 估值（market-data/date，每批5） ====================
# 优先取当日估值（DM 一般晚间发布当日数据）；若当日覆盖率过低（<30%）视为尚未发布，回退上一工作日
def fetch_vals(date_str):
    val_map = {}
    for i in range(0, len(uniq_ids), 5):
        chunk = uniq_ids[i:i + 5]
        try:
            rows = client.post_data({"securityIdList": chunk, "dataSourceList": [2],
                "startDate": date_str, "endDate": date_str},
                api_path="/dm-quant-func-service/api/v1/bond/market-data/date", return_type="dict")
            rows = rows if isinstance(rows, list) else (rows.get("list") or [])
            for r in rows:
                sid = r.get("security_id")
                if sid:
                    val_map[sid] = {
                        "cb_ytm": r.get("cb_ytm"),
                        "cb_yte": r.get("cb_yte"),
                        "cs_ytm": r.get("cs_ytm"),
                        "cs_yte": r.get("cs_yte"),
                        "date": date_str,
                    }
        except Exception as e:
            print(f"估值批次失败 {i}: {repr(e)[:120]}")
        time.sleep(0.15)
    return val_map

DATE = today()
val_map = fetch_vals(DATE)
if uniq_ids and len(val_map) < 0.3 * len(uniq_ids):
    fb = prev_workday()
    print(f"当日({DATE})估值覆盖率过低({len(val_map)}/{len(uniq_ids)})，回退上一工作日 {fb}")
    DATE = fb
    val_map = fetch_vals(DATE)
print(f"估值获取: {len(val_map)}/{len(uniq_ids)}（估值日期 {DATE}）")

# ==================== 3. 按规则提取 ====================
def pick(val_ytm, val_yte):
    """含权债(存在 yte)一律取行权收益率；非含权取 ytm"""
    if val_yte is not None and str(val_yte).strip() != "":
        return float(val_yte), "行权"
    if val_ytm is not None and str(val_ytm).strip() != "":
        return float(val_ytm), "到期"
    return None, None

def extract(name, sid):
    """返回 {cb, cs, cb_basis, cs_basis, market}"""
    info = market_map.get(sid, {})
    market = info.get("market") or ""
    cros = info.get("cros") or ""
    v = val_map.get(sid, {})
    cb, cb_basis = pick(v.get("cb_ytm"), v.get("cb_yte"))
    cs, cs_basis = pick(v.get("cs_ytm"), v.get("cs_yte"))

    is_interbank = "银行间" in market
    is_exchange = ("交易所" in market) or ("证券交易" in market)
    is_cros = "是" in str(cros)

    if is_cros or (is_interbank and is_exchange):
        # 双市场：都取
        return {"cb": cb, "cs": cs, "cb_basis": cb_basis, "cs_basis": cs_basis, "market": market or "双市场"}
    if is_interbank:
        # 仅银行间：只取中债
        return {"cb": cb, "cs": None, "cb_basis": cb_basis, "cs_basis": None, "market": market}
    if is_exchange:
        # 仅交易所：只取中证
        return {"cb": None, "cs": cs, "cb_basis": None, "cs_basis": cs_basis, "market": market}
    # 未知场所：都取（宽松）
    return {"cb": cb, "cs": cs, "cb_basis": cb_basis, "cs_basis": cs_basis, "market": market or "未知"}

out = {
    "meta": {"date": DATE, "generated": dt.datetime.now().isoformat(),
             "source": "DM market-data/date + basic-info/info",
             "valuation_rule": "含权债取行权收益率(yte)，非含权取到期收益率(ytm)"},
    "bonds": {},
}
for name, b in bonds.items():
    sid = b["security_id"]
    if not sid:
        out["bonds"][name] = {"security_id": None, "cb": None, "cs": None, "market": None}
        continue
    r = extract(name, sid)
    out["bonds"][name] = {
        "security_id": sid,
        "cb": r["cb"], "cs": r["cs"],
        "cb_basis": r["cb_basis"], "cs_basis": r["cs_basis"],
        "market": r["market"],
    }

with open(os.path.join(BASE, "valuations.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n已写入 valuations.json，估值日期 {DATE}")

# 统计
have_cb = sum(1 for v in out["bonds"].values() if v["cb"] is not None)
have_cs = sum(1 for v in out["bonds"].values() if v["cs"] is not None)
total = len(out["bonds"])
both_none = sum(1 for v in out["bonds"].values() if v["cb"] is None and v["cs"] is None)
xq = sum(1 for v in out["bonds"].values() if v.get("cb_basis") == "行权" or v.get("cs_basis") == "行权")
print(f"共 {total} 只：中债 {have_cb}，中证 {have_cs}，均无 {both_none}，其中含权取行权 {xq} 只")
# 样例
for name, v in list(out["bonds"].items())[:8]:
    basis = "/".join(filter(None, [v.get("cb_basis"), v.get("cs_basis")])) or "无"
    print(f"  {name}: 场所={v['market']} 中债={v['cb']} 中证={v['cs']} 口径={basis}")
