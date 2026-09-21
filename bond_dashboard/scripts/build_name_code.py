# -*- coding: utf-8 -*-
"""
从 DM 一级发行历史 CSV 生成「债券简称 -> security_id」映射，落盘 data/store/name_code.json。

用途：推荐清单（recommended.json，由 Excel 导入）中部分个券的「债券代码」列为空，
导致估值序列拉不到（obs=0，页面显示无估值）。用 DM 的 sec_short_name 反查 security_id
作为兜底，避免 Excel 手填代码缺失/错误。

键同时写入原始简称与「去括号」简称（如 26农行二级资本债02(BC) <-> 26农行二级资本债02BC），
同名多期取 subscribe_date 最新的一条。
"""
import csv
import json
import os
import re

CSV = r"D:/DM API 实/raw_primary_history.csv"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "store", "name_code.json")


def norm_keep(s: str) -> str:
    """去掉括号符号本身但保留括号内内容（Excel 常写 26农行二级资本债02(BC)，DM 写作 ...02BC）"""
    return re.sub(r"[（()）\s]", "", s or "").strip()


def norm_drop(s: str) -> str:
    """连同括号内容一起去掉（DM 写作 ...GN001A，Excel 写作 ...GN001A(碳中和债)）"""
    s = re.sub(r"[（(][^）)]*[）)]", "", s or "")
    return re.sub(r"\s+", "", s).strip()


def main():
    best = {}  # name -> (score, security_id)，score 越大越优先
    with open(CSV, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("sec_short_name") or "").strip()
            sid = (row.get("security_id") or "").strip()
            if not name or not sid:
                continue
            # 取消发行的记录一律不入表：DM 给待发行/取消券发 DY 前缀代码
            # （如 26泉州交通MTN005A(取消发行) -> DY102682926.IB），
            # 若被当成真实代码会张冠李戴（2026-09-11 排查 26农行二级资本债02 时发现）
            if (row.get("issue_status_desc") or "").strip() == "取消发行" or "取消发行" in name:
                continue
            sd = (row.get("subscribe_date") or "").strip()
            # 优先正式代码（非 DY 待发行代码），其次簿记日较新
            score = (0 if not sid.upper().startswith("DY") else -1000000) + (int(sd.replace("-", "")) if sd[:4].isdigit() else 0)
            for key in {name, norm_keep(name), norm_drop(name)}:
                if not key:
                    continue
                cur = best.get(key)
                if cur is None or score >= cur[0]:
                    best[key] = (score, sid)

    out = {k: v[1] for k, v in sorted(best.items())}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print(f"写入 {OUT}: {len(out)} 条")

    # 校验推荐清单中无代码的个券能命中多少
    rec_path = os.path.join(os.path.dirname(OUT), "..", "recommended.json")
    with open(rec_path, encoding="utf-8") as f:
        rec = json.load(f)
    miss = 0
    for r in rec:
        if r.get("code"):
            continue
        hit = out.get(r["name"]) or out.get(norm_keep(r["name"])) or out.get(norm_drop(r["name"]))
        print(f"  {'OK ' if hit else 'MISS'} {r['name']} -> {hit}")
        if not hit:
            miss += 1
    print(f"无代码推荐券命中 {sum(1 for r in rec if not r.get('code')) - miss} / {sum(1 for r in rec if not r.get('code'))}")


if __name__ == "__main__":
    main()
