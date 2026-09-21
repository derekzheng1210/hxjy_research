# -*- coding: utf-8 -*-
"""
发行人附加指标预计算：为 issuer 页主体表补「交易所存量债券质押比区间」与「近五年 YY 评级调整」

数据来源（均 DM）：
  1) 交易所存量债券标准券折算率（std_bond_conv_ratio）
     - 从 raw_primary_history.csv 取榜单内发行人的交易所债券代码（.SH/.SZ，剔除 DY 待发行）
     - DM /bond/basic-info/info 批量查 std_bond_conv_ratio（每批 200）
     - 仅保留非 null 折算率 → 按发行人聚合 min~max 区间
  2) 近五年 YY 评级调整
     - DM /company/rating/data（comChiNameList，单次最多 5 个主体，默认近 5 年全量）
     - 过滤 data_source="YY评分"，按 rating_date 排序，识别评级档位变化
     - 分「调高」「调低」两块（每次调整明细）；另算「净档位变化 yyNet」= 近五年首档→末档连续索引差：
       调优（net<0）/ 调差（net>0）/ 维持（net=0 但有波动）
       YY 档位排序（好→差）：1+ < 1 < 2+ < 2 < 2- < 3+ < 3 < 3- < ... < 8- < 8 < 8+
       即数字越小信用越好；数字↑=调低，数字↓=调高

输出：data/cache/issuer_metrics.json
  {"meta": {...}, "map": {主体全称: {"pledge": {"min": 0.70, "max": 0.91, "n": 5},
                                   "yyAdj": {"up": [{"from":"6+","to":"6","date":"2023-01-01"}, ...],
                                             "down": [...],
                                             "yyNet": {"dir":"up|down|flat","steps":2,"from":"4-","to":"4+",
                                                       "firstDate":"...","lastDate":"...","moves":3}}}}}

用法: python scripts/build_issuer_metrics.py [--dm-limit N] [--skip-pledge] [--skip-yy]
  默认全量；--dm-limit N 仅处理前 N 家（测试用）。
"""
import os, sys, json, time, re, csv, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DATA = os.path.join(ROOT, "data")
OUT_FILE = os.path.join(DATA, "cache", "issuer_metrics.json")
HISTORY_CSV = r"D:/DM API 实/raw_primary_history.csv"

DM_LIMIT = 0
SKIP_PLEDGE = "--skip-pledge" in sys.argv
SKIP_YY = "--skip-yy" in sys.argv
if "--dm-limit" in sys.argv:
    try:
        DM_LIMIT = int(sys.argv[sys.argv.index("--dm-limit") + 1])
    except (ValueError, IndexError):
        DM_LIMIT = 0

YY_RE = re.compile(r"^[1-8][+-]?$")
BATCH_BASIC = 200   # basic-info 单次最多 200 只
BATCH_RATING = 5    # company/rating/data 单次最多 5 家
RATE_SLEEP = 0.35   # 限速（秒/批）


def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def build_client():
    env_file = os.path.join(ROOT, ".env.local")
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


def yy_order(v):
    """YY 档位 → 可比较数值：数字越大信用越差。
    后缀语义：'7+' 比 '7' 更好（order 更小），'7-' 比 '7' 更差（order 更大）。
    故：7+ < 7 < 7- < 8- < 8 < 8+ < 9- ..."""
    m = YY_RE.match(v)
    if not m:
        return None
    base = int(v[0])
    suffix = v[1:] if len(v) > 1 else ""
    adj = 0 if not suffix else (1 if suffix == "-" else -1)  # '-' 信用更差 → order+1；'+' 更好 → order-1
    return base * 10 + adj


def yy_level_index(v):
    """YY 档位 → 连续索引（0 起，越大信用越差），用于计算净档位变化。
    档位序列（好→差）：1+ < 1 < 2+ < 2 < 2- < 3+ < 3 < 3- < ... < 8- < 8 < 8+
    相邻子档差 1；数字 1 无「1-」（1+ 与 1 直接接 2+）。"""
    m = YY_RE.match(v.strip())
    if not m:
        return None
    s0 = v.strip()
    n = int(s0[0])
    s = s0[-1] if len(s0) > 1 and s0[-1] in "+-" else ""
    if n == 1:
        return 0 if s == "+" else 1
    base = (n - 1) * 3
    if s == "+":
        return base
    if s == "-":
        return base + 2
    return base + 1


def fetch_pledge(client, issuer_to_codes):
    """查每只交易所债券的标准券折算率，聚合为发行人 min~max 区间。返回 {name: {min,max,n}}"""
    # 展平为 (name, code) 列表，按批查 basic-info
    flat = []
    for name, codes in issuer_to_codes.items():
        for c in codes:
            flat.append((name, c))
    result = {}
    i = 0
    while i < len(flat):
        chunk = flat[i:i + BATCH_BASIC]
        codes = [c for _, c in chunk]
        rows = None
        for attempt in range(3):
            try:
                r = client.post_data({"securityIdList": codes},
                                     api_path="/dm-quant-func-service/api/v1/bond/basic-info/info",
                                     return_type="dict")
                rows = r if isinstance(r, list) else (r.get("list") or r.get("data") or [])
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg or "频繁" in msg:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                print(f"    basic-info 批次 {i} 失败，跳过：{msg[:80]}")
                break
        if rows:
            # code -> ratio
            ratio_by_code = {}
            for row in rows:
                code = str(row.get("security_id") or "")
                ratio = row.get("std_bond_conv_ratio")
                if code and ratio is not None:
                    try:
                        ratio_by_code[code] = float(ratio)
                    except (TypeError, ValueError):
                        pass
            # 回填到 name
            name_ratios = {}
            for name, c in chunk:
                if c in ratio_by_code:
                    name_ratios.setdefault(name, []).append(ratio_by_code[c])
            for name, vals in name_ratios.items():
                cur = result.setdefault(name, {"min": None, "max": None, "n": 0})
                for v in vals:
                    if cur["min"] is None or v < cur["min"]:
                        cur["min"] = v
                    if cur["max"] is None or v > cur["max"]:
                        cur["max"] = v
                    cur["n"] += 1
        i += len(chunk)
        time.sleep(RATE_SLEEP)
    return result


def fetch_yy_history(client, names):
    """查 YY 评分历史序列，识别评级调整。返回 {name: {"up":[...], "down":[...]}}"""
    result = {}
    i = 0
    while i < len(names):
        chunk = names[i:i + BATCH_RATING]
        rows = None
        for attempt in range(3):
            try:
                r = client.post_data({"comChiNameList": chunk},
                                     api_path="/dm-quant-func-service/api/v1/company/rating/data",
                                     return_type="dict")
                rows = r if isinstance(r, list) else (r.get("list") or r.get("data") or [])
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg or "频繁" in msg:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                print(f"    rating 批次 {i} 失败，跳过：{msg[:80]}")
                break
        if rows:
            # name -> [(date, rating)] 仅 YY 评分，按日期排序
            seq = {}
            for row in rows:
                nm = str(row.get("com_chi_name") or "").strip()
                src = str(row.get("data_source") or "")
                rat = str(row.get("rating") or "").strip()
                dat = str(row.get("rating_date") or "")
                if not nm or src != "YY评分" or not YY_RE.match(rat):
                    continue
                seq.setdefault(nm, []).append((dat, rat))
            for nm, arr in seq.items():
                arr.sort(key=lambda x: x[0])  # 按日期升序
                up, down = [], []
                for j in range(1, len(arr)):
                    prev_d, prev_r = arr[j - 1]
                    cur_d, cur_r = arr[j]
                    p, c = yy_order(prev_r), yy_order(cur_r)
                    if p is None or c is None or p == c:
                        continue
                    if c < p:  # 数字变小 = 信用变好 = 调高
                        up.append({"from": prev_r, "to": cur_r, "date": cur_d})
                    else:       # 数字变大 = 信用变差 = 调低
                        down.append({"from": prev_r, "to": cur_r, "date": cur_d})
                # 净档位变化：近五年首档 → 末档（连续索引差）
                first_i = yy_level_index(arr[0][1])
                last_i = yy_level_index(arr[-1][1])
                net = None
                if first_i is not None and last_i is not None:
                    net = last_i - first_i
                yy_net = None
                if net is not None:
                    if net < 0:
                        dirn = "up"
                    elif net > 0:
                        dirn = "down"
                    else:
                        dirn = "flat"
                    # 首次波动方向（flat 时用于区分「先优后差」/「先差后优」）
                    first_dir = None
                    moves = up + down
                    if moves:
                        moves_sorted = sorted(moves, key=lambda x: x["date"])
                        first_dir = "up" if moves_sorted[0] in up else "down"
                    yy_net = {
                        "dir": dirn,
                        "steps": abs(net),
                        "from": arr[0][1],
                        "to": arr[-1][1],
                        "firstDate": arr[0][0],
                        "lastDate": arr[-1][0],
                        "moves": len(up) + len(down),  # 是否有过波动（flat 且有 moves 仍展示「维持」）
                        "firstDir": first_dir,  # flat 时首次波动方向
                    }
                result[nm] = {"up": up, "down": down, "yyNet": yy_net}
        i += len(chunk)
        time.sleep(RATE_SLEEP)
    return result


def main():
    ytd = load_json(os.path.join(DATA, "issuer_ytd.json"))
    issuers = [x["issuer"] for x in (ytd or {}).get("issuers", [])] if ytd else []
    if not issuers:
        print("!! data/issuer_ytd.json 缺失或无 issuers，先跑 build_issuer_ytd.py")
        return
    if DM_LIMIT:
        issuers = issuers[:DM_LIMIT]
    issuer_set = set(issuers)
    print(f"榜单发行人：{len(issuers)} 家（{'限 ' + str(DM_LIMIT) + ' 家' if DM_LIMIT else '全量'}）")

    # ---- 1) 交易所债券代码（从历史 CSV 按发行人聚合）----
    issuer_to_codes = {}
    if not SKIP_PLEDGE and os.path.exists(HISTORY_CSV):
        from collections import defaultdict
        tmp = defaultdict(list)
        with open(HISTORY_CSV, encoding="utf-8-sig", errors="replace") as f:
            r = csv.DictReader(f)
            for row in r:
                code = row.get("security_id") or ""
                name = row.get("issuer_full_name") or ""
                if (code.endswith(".SH") or code.endswith(".SZ")) and not code.startswith("DY"):
                    if name in issuer_set:
                        tmp[name].append(code)
        for k, v in tmp.items():
            issuer_to_codes[k] = list(dict.fromkeys(v))  # 去重保序
        total = sum(len(v) for v in issuer_to_codes.values())
        print(f"有交易所债券的发行人：{len(issuer_to_codes)} 家，共 {total} 只（去重）")

    client = None
    pledge = {}
    yy_hist = {}

    if not SKIP_PLEDGE and issuer_to_codes:
        try:
            client = build_client()
            print("拉取 DM 标准券折算率（basic-info）…")
            pledge = fetch_pledge(client, issuer_to_codes)
            n_with_ratio = sum(1 for v in pledge.values() if v["n"] > 0)
            print(f"  折算率覆盖 {n_with_ratio}/{len(issuer_to_codes)} 家有交易所债券的发行人")
        except Exception as e:
            print(f"!! 折算率获取失败：{str(e)[:120]}")

    if not SKIP_YY:
        try:
            if client is None:
                client = build_client()
            print("拉取 DM YY 评级历史（company/rating/data，近5年）…")
            yy_hist = fetch_yy_history(client, issuers)
            n_up = sum(1 for v in yy_hist.values() if v["up"])
            n_down = sum(1 for v in yy_hist.values() if v["down"])
            print(f"  YY 调整：{n_up} 家调高 / {n_down} 家调低 / {len(yy_hist)} 家有 YY 序列")
        except Exception as e:
            print(f"!! YY 历史获取失败：{str(e)[:120]}")

    # ---- 写出 ----
    out_map = {}
    for nm in issuers:
        p = pledge.get(nm)
        y = yy_hist.get(nm)
        out_map[nm] = {
            "pledge": {"min": p["min"], "max": p["max"], "n": p["n"]} if p and p["n"] > 0 else None,
            "yyAdj": y or {"up": [], "down": [], "yyNet": None},
        }
    meta = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "issuerCount": len(issuers),
        "pledgeCovered": sum(1 for v in out_map.values() if v["pledge"]),
        "yyUpCount": sum(1 for v in out_map.values() if v["yyAdj"]["up"]),
        "yyDownCount": sum(1 for v in out_map.values() if v["yyAdj"]["down"]),
        "yyNetUpCount": sum(1 for v in out_map.values() if (v["yyAdj"].get("yyNet") or {}).get("dir") == "up"),
        "yyNetDownCount": sum(1 for v in out_map.values() if (v["yyAdj"].get("yyNet") or {}).get("dir") == "down"),
        "yyNetFlatCount": sum(1 for v in out_map.values() if (v["yyAdj"].get("yyNet") or {}).get("dir") == "flat"),
        "pledgeSource": "DM bond/basic-info/info std_bond_conv_ratio（交易所公募可质押券，私募/非公开为 null 不计入）",
        "yySource": "DM company/rating/data data_source=YY评分 近5年历史序列（档位数字↑=调低，↓=调高）",
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "map": out_map}, f, ensure_ascii=False, indent=1)
    print(f"\n✓ 写入 {OUT_FILE}")
    print(f"  质押比覆盖 {meta['pledgeCovered']}/{len(issuers)} · YY 调优 {meta['yyNetUpCount']} 家 / 调差 {meta['yyNetDownCount']} 家 / 维持 {meta['yyNetFlatCount']} 家")


if __name__ == "__main__":
    main()
