# -*- coding: utf-8 -*-
"""
发行人评级同步：为 issuer 页主体表补全「YY 评分」与「外部评级」
数据来源
  1) 本地 YY 主表（优先级最高，同口径直取）：
     data/yy_lookup.json（每日发行 Excel「YY评分」列全历史归集）
     > data/cache/ratings.json > data/cache/yy_issuer.json
  2) DM /company/rating/data（单次最多 5 个主体全称）：
     - data_source="YY评分" → YY 评分（如 5- / 7-），**仅补本地缺失的主体**
     - data_source="外部评级" → 外部评级最新值 + 评级机构（覆盖本地旧值）
输出：data/cache/issuer_ratings.json
  {"meta": {…, "missed"}, "map": {主体全称: {yy, external, agency, yyFrom, extFrom}}}

说明：DM 另有一个「YY 主体隐含评分」接口（yy_com_implied_rating，连续数值、随市场每日变动），
     与档位制「YY 评分」不是同一口径，本脚本**不采用**，避免混口径。

用法: python scripts/sync_issuer_ratings.py [--dm] [--dm-limit N] [--retry-missing-days 14]
  默认只用本地缓存（秒级、不联网）；--dm 时用 DM 补齐（限速 5 家/批、0.35s 间隔）。
"""
import os, sys, json, time, re, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
OUT_FILE = os.path.join(DATA, "cache", "issuer_ratings.json")

USE_DM = "--dm" in sys.argv
DM_LIMIT = 0
RETRY_DAYS = 14
_argv = sys.argv
if "--dm-limit" in _argv:
    try:
        DM_LIMIT = int(_argv[_argv.index("--dm-limit") + 1])
    except (ValueError, IndexError):
        DM_LIMIT = 0
if "--retry-missing-days" in _argv:
    try:
        RETRY_DAYS = int(_argv[_argv.index("--retry-missing-days") + 1])
    except (ValueError, IndexError):
        RETRY_DAYS = 14

YY_RE = re.compile(r"^[1-8][+-]?$")  # YY 评分档位：1~8 带可选 +/-


def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def build_client():
    env_file = os.path.normpath(os.path.join(HERE, "..", ".env.local"))
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    sys.path.insert(0, r"D:/DM API 实")
    from dm_quant_api_client import DMQuantApiClient
    return DMQuantApiClient(app_key=os.environ["INNO_APP_KEY"],
                            app_secret=os.environ["INNO_APP_SECRET"], pythonic=True)


def fetch_dm_ratings(client, names):
    """DM company/rating/data → {name: {yy, external, agency}}（各取最新一条）

    ⚠️ 单次最多 5 个主体全称（超出直接报错），故强制 5 家/批 + 限速；429 退避重试。
    """
    PATH = "/dm-quant-func-service/api/v1/company/rating/data"
    out, batch, i = {}, 5, 0
    while i < len(names):
        chunk = names[i:i + batch]
        rows, ok = None, False
        for attempt in range(3):
            try:
                rows = client.post_data({"comChiNameList": chunk}, api_path=PATH, return_type="dict")
                ok = True
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg or "频繁" in msg:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                print(f"    批次 {i} 失败，跳过：{msg[:80]}")
                break
        if ok:
            rows = rows if isinstance(rows, list) else (rows.get("list") or [])
            best = {}  # name -> {field: (date, value)}
            for r in rows:
                nm = str(r.get("com_chi_name") or "").strip()
                src = str(r.get("data_source") or "")
                rat = str(r.get("rating") or "").strip()
                dat = str(r.get("rating_date") or "")
                if not nm or not rat:
                    continue
                b = best.setdefault(nm, {})
                if src == "YY评分" and YY_RE.match(rat):
                    if "yy" not in b or dat > b["yy"][0]:
                        b["yy"] = (dat, rat)
                elif src == "外部评级":
                    if "ext" not in b or dat > b["ext"][0]:
                        b["ext"] = (dat, rat, r.get("rating_institution_short_name"))
            for nm, b in best.items():
                cur = out.setdefault(nm, {})
                if "yy" in b:
                    cur["yy"] = b["yy"][1]
                if "ext" in b:
                    cur["external"] = b["ext"][1]
                    cur["agency"] = b["ext"][2]
        i += len(chunk)
        if i % 50 == 0 or i >= len(names):
            print(f"    DM 评级 {i}/{len(names)} → YY {sum(1 for v in out.values() if v.get('yy'))}"
                  f" / 外部 {sum(1 for v in out.values() if v.get('external'))}")
        time.sleep(0.35)
    return out


def main():
    ytd = load_json(os.path.join(DATA, "issuer_ytd.json"))
    names = [x["issuer"] for x in (ytd or {}).get("issuers", [])] if ytd else []
    if not names:
        print("!! data/issuer_ytd.json 缺失或无 issuers，先跑 build_issuer_ytd.py")
    print(f"issuer_ytd 主体数：{len(names)}")

    prev = load_json(OUT_FILE, {}) or {}
    prev_map = prev.get("map") or {}
    prev_missed = prev.get("meta", {}).get("missed") or {}

    # ---- 1) YY 评分：本地三源归集（低 → 高优先级）----
    yy, yy_from = {}, {}
    stat = {}
    for src, path, getter in (
        ("yy_issuer", os.path.join(DATA, "cache", "yy_issuer.json"), lambda d: d or {}),
        ("yy_lookup", os.path.join(DATA, "yy_lookup.json"), lambda d: (d or {}).get("map") or {}),
        ("ratings", os.path.join(DATA, "cache", "ratings.json"),
         lambda d: {k: v.get("yy") for k, v in (d or {}).items() if v.get("yy")}),
    ):
        m = getter(load_json(path, {})) or {}
        n0 = len(yy)
        for k, v in m.items():
            v = str(v).strip()
            if k and YY_RE.match(v):
                yy[k] = v
                yy_from[k] = "local"
        stat[src] = f"+{len(yy) - n0}"
    print("YY 本地来源累计:", stat, "→ 全量", len(yy), "条")

    # ---- 2) 外部评级：本地既有（DM 稍后覆盖）----
    ratings = load_json(os.path.join(DATA, "cache", "ratings.json"), {}) or {}
    ext, ext_from = {}, {}
    for k, v in ratings.items():
        if v.get("external"):
            ext[k] = {"external": str(v["external"]).strip(), "agency": v.get("agency")}
            ext_from[k] = "local"
    print(f"外部评级（本地既有）：{len(ext)} 条")

    # 上次缓存结果参与“是否已补齐”的判定，避免每日重复查询
    seeded_yy = seeded_ext = 0
    for k, v in prev_map.items():
        if v.get("yy") and k not in yy:
            yy[k] = v["yy"]
            yy_from[k] = v.get("yyFrom") or "cache"
            seeded_yy += 1
        if v.get("external") and k not in ext:
            ext[k] = {"external": v["external"], "agency": v.get("agency")}
            ext_from[k] = v.get("extFrom") or "cache"
            seeded_ext += 1
    if seeded_yy or seeded_ext:
        print(f"沿用上次缓存：YY {seeded_yy} 条 / 外部评级 {seeded_ext} 条")

    # ---- 3) DM 补齐（只查缺的；已查无果的按 RETRY_DAYS 冷却，避免每日空跑）----
    today = dt.date.today()
    todo, cooled = [], 0
    for n in names:
        if n in yy and n in ext:
            continue
        d0 = prev_missed.get(n)
        if d0:
            try:
                if (today - dt.date.fromisoformat(d0)).days < RETRY_DAYS:
                    cooled += 1
                    continue
            except ValueError:
                pass
        todo.append(n)
    if DM_LIMIT:
        todo = todo[:DM_LIMIT]
    print(f"待补主体 {len(todo)} 家（冷却跳过 {cooled} 家）")

    dm = {}
    if USE_DM and todo:
        try:
            print("拉取 DM 主体评级（YY评分 + 外部评级）…")
            dm = fetch_dm_ratings(build_client(), todo)
        except Exception as e:
            print(f"!! DM 获取失败：{str(e)[:120]}")
    elif not USE_DM:
        print("（未加 --dm，仅用本地缓存；DM 结果会持久化在缓存中，无需每日重跑）")

    yy_dm = ext_dm = 0
    for n, v in dm.items():
        if v.get("yy") and n not in yy:          # YY：本地优先，DM 只补空
            yy[n] = v["yy"]
            yy_from[n] = "dm"
            yy_dm += 1
        if v.get("external"):                    # 外部评级：DM 最新值覆盖
            if ext.get(n, {}).get("external") != v["external"]:
                ext_dm += 1
            ext[n] = {"external": v["external"], "agency": v.get("agency")}
            ext_from[n] = "dm"

    # 冷却表：本次查过仍缺的主体
    missed = {n: today.isoformat() for n in todo if n not in yy or n not in ext}
    for n in list(prev_missed):
        if n in missed:
            continue
        if n not in yy or n not in ext:          # 仍未补齐且本轮未查 → 保留原冷却时间
            if n not in todo:
                missed[n] = prev_missed[n]

    # ---- 4) 写出 ----
    out_map = {}
    for nm in names:
        p = prev_map.get(nm) or {}
        e = ext.get(nm) or {}
        out_map[nm] = {
            "yy": yy.get(nm) or p.get("yy"),
            "external": e.get("external") or p.get("external"),
            "agency": e.get("agency") or p.get("agency"),
            "yyFrom": yy_from.get(nm) or p.get("yyFrom"),
            "extFrom": ext_from.get(nm) or p.get("extFrom"),
        }
    meta = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "issuerCount": len(names),
        "yyCovered": sum(1 for v in out_map.values() if v["yy"]),
        "extCovered": sum(1 for v in out_map.values() if v["external"]),
        "yyDmAdded": yy_dm,
        "extDmUpdated": ext_dm,
        "dmQueried": len(dm),
        "yySource": "本地 YY 主表（Excel「YY评分」列归集）+ DM company/rating/data(data_source=YY评分) 补空",
        "extSource": "DM company/rating/data（外部评级最新值）+ 本地 ratings.json",
        "missed": missed,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "map": out_map}, f, ensure_ascii=False, indent=1)
    print(f"\n✓ 写入 {OUT_FILE}")
    print(f"  YY 覆盖 {meta['yyCovered']}/{meta['issuerCount']}"
          f"（{meta['yyCovered'] * 100 // max(1, meta['issuerCount'])}%，本轮 DM 补 {yy_dm}）"
          f" · 外部评级 {meta['extCovered']}/{meta['issuerCount']}"
          f"（{meta['extCovered'] * 100 // max(1, meta['issuerCount'])}%，本轮更新 {ext_dm}）")
    rest = [n for n in names if not yy.get(n)]
    if rest:
        print(f"  DM 亦无 YY 的主体 {len(rest)} 家，示例:", rest[:8])


if __name__ == "__main__":
    main()
