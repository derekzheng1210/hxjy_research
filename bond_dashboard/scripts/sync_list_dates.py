# -*- coding: utf-8 -*-
"""
DM 上市信息同步 → data/store/bids.json（每日刷新步骤12）
================================================================
背景（2026-09-11 用户反馈）：「中标个券上市提醒」板块的记录只有 Excel 登记表字段，
缺上市日（listDate）时页面只能用「缴款日 + 2 个工作日」估算，DM 已上市个券无法体现
（如 26江铜股份MTN004A(并购)）。本脚本从 DM 基础资料接口拉取真实上市日等字段回填。

处理对象：data/store/bids.json 中 participated / won 记录
回填字段（仅在缺失时写入；上市日若与 DM 不一致则以 DM 为准并记录变更）：
  securityId ← DM（或 name_code.json / 历史 CSV 名称反查兜底）
  payDate    ← DM inte_start_date（起息日，与缴款日一致）
  listDate   ← DM list_date（上市日）
  issuer     ← DM issuer_name
  coupon     ← DM iss_coup_rate（仅当原值为空）
数据源：DM /bond/basic-info/info（camelCase 入参 securityIdList，每批 20 只）
用法: "<venv>/python.exe" scripts/sync_list_dates.py
"""
import json, os, re, sys, csv, datetime as dt, time

sys.path.insert(0, "C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Lib/site-packages")
from dm_quant_api_client import DMQuantApiClient

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")
BIDS_PATH = os.path.join(DATA, "store", "bids.json")
NAME_CODE = os.path.join(DATA, "store", "name_code.json")
HIST_CSV = r"D:/DM API 实/raw_primary_history.csv"

# ---- DM 凭据（来自 .env.local）----
ENV_FILE = os.path.join(ROOT, ".env.local")
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
APP_KEY = os.environ.get("INNO_APP_KEY")
APP_SECRET = os.environ.get("INNO_APP_SECRET")
if not APP_KEY or not APP_SECRET:
    print("请设置 INNO_APP_KEY / INNO_APP_SECRET（.env.local）")
    sys.exit(2)


def norm_keep(s: str) -> str:
    """去括号符号但保留内容（26农行二级资本债02(BC) ↔ ...02BC）"""
    return re.sub(r"[（()）\s]", "", s or "").strip()


def norm_drop(s: str) -> str:
    """连括号内容一起去掉"""
    s = re.sub(r"[（(][^）)]*[）)]", "", s or "")
    return re.sub(r"\s+", "", s).strip()


def load_name_code():
    try:
        return json.load(open(NAME_CODE, encoding="utf-8"))
    except Exception:
        return {}


def load_hist_name_code():
    """历史 CSV 简称 → security_id（取最新 subscribe_date，剔除取消发行）"""
    out = {}
    try:
        with open(HIST_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                nm = (row.get("sec_short_name") or "").strip()
                sid = (row.get("security_id") or "").strip()
                if not nm or not sid or sid.startswith("DY"):
                    continue
                if "取消发行" in (row.get("issue_status_desc") or ""):
                    continue
                d = (row.get("subscribe_date") or row.get("issue_start_date") or "")
                cur = out.get(nm)
                if not cur or d >= cur[0]:
                    out[nm] = (d, sid)
    except FileNotFoundError:
        pass
    return {k: v[1] for k, v in out.items()}


def resolve_code(rec, nc, hc):
    sid = (rec.get("securityId") or "").strip()
    if sid and not sid.startswith("DY"):
        return sid, "已有"
    name = str(rec.get("bondName") or "").strip()
    if not name:
        return None, "无简称"
    for key in (name, norm_keep(name), norm_drop(name)):
        if key and nc.get(key):
            return nc[key], "名称映射"
    for cand, sid2 in hc.items():
        if cand == name or norm_keep(cand) == norm_keep(name) or norm_drop(cand) == norm_drop(name):
            return sid2, "历史CSV"
    return None, "未匹配"


def sync_sqlite(rows):
    import sqlite3
    db = os.path.join(DATA, "bond.db")
    try:
        con = sqlite3.connect(db, timeout=8)
        con.execute("PRAGMA busy_timeout=8000")
        cur = con.cursor()
        cur.execute("UPDATE bids SET securityId=?, payDate=?, listDate=?, issuer=?, coupon=COALESCE(coupon,?) WHERE id=?",
                    (rows.get("securityId"), rows.get("payDate"), rows.get("listDate"),
                     rows.get("issuer"), rows.get("coupon"), rows["id"]))
        con.commit()
        con.close()
    except Exception as e:
        print(f"  ⚠ SQLite 更新失败（不影响 JSON）: {e}")


def main():
    bids = json.load(open(BIDS_PATH, encoding="utf-8"))
    nc, hc = load_name_code(), load_hist_name_code()
    print(f"bids {len(bids)} 条 | 名称映射 {len(nc)} 条 | 历史CSV名称 {len(hc)} 条")

    todo = []
    for r in bids:
        if r.get("type") not in ("won", "participated"):
            continue
        code, how = resolve_code(r, nc, hc)
        if not code:
            continue
        need = (not r.get("securityId")) or (not r.get("listDate")) or (not r.get("payDate")) or (not r.get("issuer"))
        if need:
            todo.append((r, code, how))
    if not todo:
        print("无需更新：全部记录已有代码/缴款日/上市日/发行人")
        return 0

    uniq = sorted({c for _, c, _ in todo})
    print(f"待查询 {len(todo)} 条记录，涉及 {len(uniq)} 只个券")

    client = DMQuantApiClient(app_key=APP_KEY, app_secret=APP_SECRET, pythonic=True)
    info = {}
    for i in range(0, len(uniq), 20):
        chunk = uniq[i:i + 20]
        try:
            rows = client.post_data({"securityIdList": chunk},
                                    api_path="/dm-quant-func-service/api/v1/bond/basic-info/info",
                                    return_type="dict")
            rows = rows if isinstance(rows, list) else (rows.get("list") or [])
            for r in rows:
                sid = (r.get("security_id") or "").strip()
                if sid:
                    info[sid] = r
        except Exception as e:
            print(f"  批次 {i} 失败: {repr(e)[:120]}")
        time.sleep(0.2)
    print(f"DM 返回 {len(info)}/{len(uniq)} 只")

    n_code = n_pay = n_list = n_iss = n_cpn = 0
    changed_list = []
    for r, code, how in todo:
        d = info.get(code)
        if not d:
            continue
        if not r.get("securityId"):
            r["securityId"] = code
            n_code += 1
        if not r.get("payDate") and d.get("inte_start_date"):
            r["payDate"] = str(d["inte_start_date"])[:10]
            n_pay += 1
        ld = str(d.get("list_date") or "")[:10]
        if ld:
            old = str(r.get("listDate") or "")[:10]
            if old != ld:
                r["listDate"] = ld
                n_list += 1
                if old:
                    changed_list.append((r.get("bondName"), old, ld))
        if not r.get("issuer") and d.get("issuer_name"):
            r["issuer"] = d["issuer_name"]
            n_iss += 1
        if not r.get("coupon") and d.get("iss_coup_rate"):
            try:
                r["coupon"] = float(d["iss_coup_rate"])
                n_cpn += 1
            except (TypeError, ValueError):
                pass
        r["updatedAt"] = dt.datetime.now().isoformat()
        sync_sqlite(r)

    json.dump(bids, open(BIDS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"回填：代码 {n_code} / 缴款日 {n_pay} / 上市日 {n_list} / 发行人 {n_iss} / 票面 {n_cpn}")
    for nm, old, new in changed_list:
        print(f"  ~ 上市日修正 {nm}: {old} -> {new}")
    # 打印本次涉及的上市提醒（供人工核对）
    wons = [r for r in bids if r.get("type") == "won" and r.get("listDate")]
    wons.sort(key=lambda r: r.get("listDate") or "", reverse=True)
    print("最近上市（won）:")
    for r in wons[:6]:
        print(f"  {r['listDate']}  {r.get('bondName')}  {(r.get('amount') or 0):.1f}亿  {r.get('securityId')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
