"""
v6 精细化补算脚本：只重算受永续分类规则影响的债券

背景：
    v5→v6 变更 = 永续债识别改用 CVTBDEXPIREMEMP 字段（"+N"后缀）。
    对仍标记为 v5 的受影响发行人，并非旗下全部债券都需要重算：

    需要重算的债券（结果可能变化）：
      A. 自身是"隐性永续"（字段判永续但名称看不出）：
         分类 ordinary→perpetual，参考池完全不同。
      B. 名称可识别的永续债：v6 参考池新增了隐性永续债（池扩大），
         最优参考券可能变化。
      C. 普通债但 v5 选中的参考券是隐性永续：该参考券在 v6 中被
         移出普通债池，需要重新选参考券。

    可直接迁移的债券（结果严格等价）：
      - 普通债且参考券不是隐性永续：池只是剔除了未被选中的候选，
        argmin 不变。
      - 二级资本债 / TLAC：分类优先级按名称，池完全不变。

性能优化：
    - 一次性全量拉取新发债券（避免逐发行人执行 fetch_new_issues 大 SQL）
    - 重算按 (发行人, 发行日) 分组，共享存续债+估值查询
    - 曲线/隐含评级全程共享缓存
    - 按发行人提交，可中断续跑（issuer_summary 翻到 v6 即完成标记）

运行（先停掉正在跑的 cache_builder.py！）：
    python recalc_v6_selective.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from primary_market_pricing.db_utils import get_connection
from primary_market_pricing.data_fetcher import fetch_new_issues, fetch_issuer_outstanding
from primary_market_pricing.calculator import classify_bond_type, _QueryCache, _calculate_single_bond_with_cache
from primary_market_pricing.cache_builder import init_cache_db, CACHE_DB_PATH, ISSUE_DATE_RULE_VERSION

OLD_RULE = "issue_date_v5"
NEW_RULE = ISSUE_DATE_RULE_VERSION  # issue_date_v6
HISTORY_START = "20240101"

_NAME_PERP_RE = re.compile(r"Y\d+$", re.IGNORECASE)


def _is_name_perp(name: str) -> bool:
    n = name or ""
    return ("永续" in n) or bool(_NAME_PERP_RE.search(n))


def fetch_oracle_metadata(oracle_conn, end_date: str):
    """返回 (隐性永续SYMBOL集合, symbol->含权期限说明映射, 全量新发债DataFrame)"""
    cur = oracle_conn.cursor()

    print("① 拉取含权字段映射并计算隐性永续集合...")
    t0 = time.time()
    cur.execute("""
        SELECT SYMBOL, BONDSNAME, CVTBDEXPIREMEMP
        FROM TQ_BD_BASICINFO
        WHERE ISVALID = 1 AND CVTBDEXPIREMEMP IS NOT NULL AND SYMBOL IS NOT NULL
    """)
    memo_map: dict[str, str] = {}
    hidden_perp: set[str] = set()
    for sym, name, memo in cur.fetchall():
        sym = str(sym)
        memo_s = str(memo).strip() if memo else ""
        if memo_s:
            memo_map[sym] = memo_s
        if memo_s.upper().endswith("+N"):
            n = name or ""
            if "永续" in n or "二级" in n or "TLAC" in n.upper() or _NAME_PERP_RE.search(n):
                continue
            hidden_perp.add(sym)
    print(f"   含权映射 {len(memo_map)} 条，隐性永续 {len(hidden_perp)} 只 ({time.time()-t0:.1f}s)")

    print("② 一次性全量拉取新发债券列表（免去逐发行人大查询）...")
    t0 = time.time()
    all_issues = fetch_new_issues(oracle_conn, HISTORY_START, end_date)
    print(f"   {len(all_issues)} 只 ({time.time()-t0:.1f}s)")
    return hidden_perp, memo_map, all_issues


def _save_bond(sconn, bond: dict, now_str: str):
    """按 save_issuer_result 的格式写入单只债券（symbol 为主键自动覆盖旧行）"""
    sconn.execute("""
        INSERT OR REPLACE INTO bond_deviations (
            symbol, issue_date_rule, bond_name, issuer, coupon_rate, issue_amount_wan,
            issue_date, effective_term, rating, implied_rating,
            effective_rating, raise_mode, bond_type, cvtbd_expire,
            ref_bond_name, ref_bond_symbol, ref_start_date,
            ref_date_gap_years, ref_yield, ref_term,
            curve_code, curve_at_ref, curve_at_target,
            spread, fair_price, deviation, deviation_bp,
            is_non_market, is_overpriced, is_no_judgement, computed_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        bond.get("bond_symbol", ""), NEW_RULE,
        bond.get("bond_name", ""), bond.get("issuer", ""),
        bond.get("coupon_rate"), bond.get("issue_amount_wan"), bond.get("issue_date", ""),
        bond.get("maturity_year"), bond.get("rating", ""),
        bond.get("implied_rating", ""), bond.get("effective_rating", ""),
        bond.get("raise_mode", ""), bond.get("bond_type", ""),
        bond.get("cvtbd_expire", ""), bond.get("ref_bond_name", ""),
        bond.get("ref_bond_symbol", ""), bond.get("ref_start_date", ""),
        bond.get("ref_date_gap_years"), bond.get("ref_yield"),
        bond.get("ref_term"), bond.get("curve_code", ""),
        bond.get("curve_at_ref"), bond.get("curve_at_target"),
        bond.get("spread"), bond.get("fair_price"),
        bond.get("deviation"), bond.get("deviation_bp"),
        1 if bond.get("is_non_market") else 0,
        1 if bond.get("is_overpriced") else 0,
        1 if bond.get("is_no_judgement") else 0,
        now_str,
    ))


def _issue_fact_row(row, issuer: str) -> dict:
    """Keep issuance facts even when no pricing reference can be selected."""
    name = str(row["BONDSNAME"] or "")
    cvtbd_expire = row["CVTBDEXPIREMEMP"] if "CVTBDEXPIREMEMP" in row.index else None
    return {
        "bond_symbol": str(row["SYMBOL"]),
        "bond_name": name,
        "issuer": issuer,
        "coupon_rate": float(row["COUPONRATE"]) if pd.notna(row["COUPONRATE"]) else None,
        "issue_amount_wan": float(row["ISSUE_AMOUNT_WAN"]) if pd.notna(row.get("ISSUE_AMOUNT_WAN")) else None,
        "issue_date": str(row["ISSUE_DATE"]),
        "maturity_year": float(row["EFFECTIVE_TERM"]),
        "rating": str(row["RATING"] or ""),
        "raise_mode": str(row["RAISEMODE"] or ""),
        "bond_type": classify_bond_type(name, cvtbd_expire),
        "cvtbd_expire": str(cvtbd_expire).strip() if pd.notna(cvtbd_expire) else "",
        "is_non_market": False,
        "is_overpriced": False,
        "is_no_judgement": True,
    }


def _refresh_summary(sconn, issuer: str, start_date: str, end_date: str, now_str: str):
    """基于 v6 债券行重算发行人汇总并翻版本标记（issuer 为主键自动覆盖）"""
    agg = sconn.execute("""
        SELECT COUNT(*),
            SUM(CASE WHEN COALESCE(is_no_judgement,0)=0 AND deviation_bp IS NOT NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(is_no_judgement,0)=0 AND deviation_bp IS NOT NULL AND is_non_market=1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN COALESCE(is_no_judgement,0)=0 AND deviation_bp IS NOT NULL AND is_overpriced=1 THEN 1 ELSE 0 END),
            AVG(CASE WHEN COALESCE(is_no_judgement,0)=0 AND deviation_bp IS NOT NULL THEN deviation_bp END)
        FROM bond_deviations
        WHERE issuer = ? AND issue_date_rule = ?
          AND issue_date >= ? AND issue_date <= ?
    """, (issuer, NEW_RULE, start_date, end_date)).fetchone()
    total, calc, nm, op, avg_bp = (
        int(agg[0] or 0), int(agg[1] or 0), int(agg[2] or 0),
        int(agg[3] or 0), round(float(agg[4] or 0.0), 2),
    )
    sconn.execute("""
        INSERT OR REPLACE INTO issuer_summary (
            issuer, issue_date_rule, total_bonds, calculated_bonds,
            non_market_count, non_market_ratio,
            overpriced_count, overpriced_ratio,
            avg_deviation_bp, start_date, end_date, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        issuer, NEW_RULE, total, calc,
        nm, round(nm / calc, 4) if calc else 0.0,
        op, round(op / calc, 4) if calc else 0.0,
        avg_bp, start_date, end_date, now_str,
    ))


def main():
    end_date = datetime.now().strftime("%Y%m%d")
    sconn = init_cache_db(CACHE_DB_PATH)

    pending = sconn.execute(
        "SELECT issuer, start_date, end_date FROM issuer_summary WHERE issue_date_rule = ?",
        (OLD_RULE,)).fetchall()
    if not pending:
        print("没有待处理的 v5 发行人，退出")
        return
    pending_map = {r[0]: (r[1] or HISTORY_START, r[2] or end_date) for r in pending}
    print(f"待处理 v5 发行人: {len(pending_map)} 个")

    with get_connection() as oracle_conn:
        hidden_perp, memo_map, all_issues = fetch_oracle_metadata(oracle_conn, end_date)

        all_issues["_issuer"] = all_issues["ISSUER"].astype(str)
        df = all_issues[all_issues["_issuer"].isin(pending_map)].copy()
        df["_symbol"] = df["SYMBOL"].astype(str)
        df["_date"] = df["ISSUE_DATE"].astype(str)
        print(f"③ 待处理发行人的新发债券: {len(df)} 只")

        # 读取这些发行人的全部 v5 缓存行
        issuers = list(pending_map)
        ph = ",".join("?" * len(issuers))
        cached = sconn.execute(
            f"SELECT symbol, bond_name, issuer, ref_bond_symbol FROM bond_deviations "
            f"WHERE issue_date_rule = ? AND issuer IN ({ph})",
            [OLD_RULE, *issuers]).fetchall()
        cached_by_issuer: dict[str, list] = {}
        for row in cached:
            cached_by_issuer.setdefault(row[2], []).append(row)

        df_by_issuer = dict(tuple(df.groupby("_issuer")))

        # 发行人按最近发行日倒序（Oracle buffer cache 友好）
        latest = df.groupby("_issuer")["_date"].max().to_dict()
        ordered = sorted(pending_map, key=lambda i: latest.get(i, ""), reverse=True)

        shared_cache = _QueryCache(oracle_conn)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stats = {"upgraded": 0, "recalced": 0, "deleted": 0, "errors": 0}
        t_start = time.time()

        for i, issuer in enumerate(ordered, 1):
            t0 = time.time()
            try:
                idf = df_by_issuer.get(issuer, pd.DataFrame())
                rows = cached_by_issuer.get(issuer, [])

                # ── 判定需重算的 symbol 集合 ──
                recalc_syms: set[str] = set()
                if not idf.empty:
                    for _, r in idf.iterrows():
                        btype = classify_bond_type(
                            str(r["BONDSNAME"]), r["CVTBDEXPIREMEMP"])
                        if btype == "perpetual":       # 情形 A + B
                            recalc_syms.add(r["_symbol"])
                for sym, name, _iss, ref_sym in rows:
                    if str(ref_sym or "") in hidden_perp:  # 情形 C
                        recalc_syms.add(str(sym))

                # ── 安全行：原地升级 ──
                upgrades = []
                for sym, name, _iss, _ref in rows:
                    if str(sym) in recalc_syms:
                        continue
                    memo = memo_map.get(str(sym), "")
                    btype = classify_bond_type(name or "", memo or None)
                    upgrades.append((NEW_RULE, btype, memo, str(sym)))
                if upgrades:
                    sconn.executemany(
                        "UPDATE bond_deviations SET issue_date_rule=?, bond_type=?, cvtbd_expire=? "
                        f"WHERE symbol=? AND issue_date_rule='{OLD_RULE}'", upgrades)
                stats["upgraded"] += len(upgrades)

                # ── 重算：按发行日分组，共享存续债+估值 ──
                n_recalc = 0
                if recalc_syms and not idf.empty:
                    target = idf[idf["_symbol"].isin(recalc_syms)]
                    for issue_date, gdf in target.groupby("_date"):
                        outstanding = fetch_issuer_outstanding(
                            oracle_conn, issuer, issue_date)
                        valuations = {}
                        if not outstanding.empty:
                            valuations = shared_cache.get_valuations_batch(
                                outstanding["SYMBOL"].tolist(), issue_date)
                        for _, r in gdf.iterrows():
                            result = None
                            if not outstanding.empty and valuations:
                                result = _calculate_single_bond_with_cache(
                                    cache=shared_cache,
                                    bond_symbol=r["SYMBOL"],
                                    bond_name=r["BONDSNAME"],
                                    issuer=issuer,
                                    coupon_rate=float(r["COUPONRATE"]) if pd.notna(r["COUPONRATE"]) else None,
                                    issue_date=str(r["ISSUE_DATE"]),
                                    maturity_year=float(r["EFFECTIVE_TERM"]),
                                    rating=str(r["RATING"]) if r["RATING"] else "",
                                    raise_mode=str(r["RAISEMODE"]) if r["RAISEMODE"] else "",
                                    outstanding=outstanding,
                                    valuations=valuations,
                                    cvtbd_expire=r["CVTBDEXPIREMEMP"],
                                    exchange=r["EXCHANGE"] if "EXCHANGE" in r.index else None,
                                    issue_amount_wan=(
                                        float(r["ISSUE_AMOUNT_WAN"])
                                        if "ISSUE_AMOUNT_WAN" in r.index and pd.notna(r["ISSUE_AMOUNT_WAN"])
                                        else None
                                    ),
                                )
                            if not result:
                                result = _issue_fact_row(r, issuer)
                            if result:
                                _save_bond(sconn, result, now_str)
                                n_recalc += 1
                stats["recalced"] += n_recalc

                # 缓存中有、但已不在新发列表里的待重算行 → 删除（口径已变）
                cached_syms = {str(s) for s, *_ in rows}
                orphan = (recalc_syms & cached_syms) - set(
                    idf["_symbol"]) if not idf.empty else (recalc_syms & cached_syms)
                for sym in orphan:
                    cur = sconn.execute(
                        "DELETE FROM bond_deviations WHERE symbol = ? AND issue_date_rule = ?",
                        (sym, OLD_RULE))
                    stats["deleted"] += cur.rowcount

                # ── 汇总翻版本，提交（完成标记，支持断点续跑）──
                s_start, s_end = pending_map[issuer]
                _refresh_summary(sconn, issuer, s_start, s_end, now_str)
                sconn.commit()

                elapsed = time.time() - t0
                total_elapsed = time.time() - t_start
                eta_min = total_elapsed / i * (len(ordered) - i) / 60
                print(f"[{i}/{len(ordered)}] {issuer} | 迁移{len(upgrades)} 重算{n_recalc} | "
                      f"{elapsed:.1f}s | 预计剩余 {eta_min:.0f} 分钟")

            except Exception as e:
                stats["errors"] += 1
                sconn.rollback()
                print(f"[{i}/{len(ordered)}] {issuer} | ❌ {e}")

    n_v5_left = sconn.execute(
        "SELECT COUNT(*) FROM issuer_summary WHERE issue_date_rule = ?",
        (OLD_RULE,)).fetchone()[0]
    sconn.close()

    print("\n" + "=" * 55)
    print("完成")
    print(f"  直接迁移债券: {stats['upgraded']}")
    print(f"  重算债券:     {stats['recalced']}")
    print(f"  删除失效行:   {stats['deleted']}")
    print(f"  出错发行人:   {stats['errors']}")
    print(f"  剩余v5发行人: {n_v5_left}")
    print(f"  总耗时:       {(time.time()-t_start)/60:.1f} 分钟")
    print("=" * 55)


if __name__ == "__main__":
    main()
