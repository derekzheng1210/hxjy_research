"""
缓存迁移脚本：issue_date_v5 -> issue_date_v6（原地升级版）

背景：
    v6 变更 = 永续债识别改用 CVTBDEXPIREMEMP 字段（"+N"后缀），并新增
    bond_type / cvtbd_expire 两个缓存字段。

注意：cache.db 实际表结构中 symbol / issuer 是单列主键，
    issue_date_rule 只是版本标记列，同一债券只能存一行。
    因此迁移方式为【原地 UPDATE 版本标记】，而非复制新行。

迁移逻辑：
    1. 从 Oracle 找出"受影响发行人"：旗下存在【字段判永续但名称看不出】
       债券的发行人。这些发行人的 v5 结果（参考券匹配）可能有误，
       保持 v5 标记不动，留给 cache_builder 在 v6 下重算
       （重算时 INSERT OR REPLACE 按 symbol 主键自动覆盖旧行）。
    2. 其余发行人：v5 与 v6 计算结果完全等价，原地把版本标记改为 v6，
       并从 Oracle 回填 cvtbd_expire（含权期限说明）与 bond_type。

运行：python migrate_cache_v5_to_v6.py
    完成后运行 python cache_builder.py --once 补算受影响发行人即可。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primary_market_pricing.db_utils import get_connection
from primary_market_pricing.calculator import classify_bond_type
from primary_market_pricing.cache_builder import init_cache_db, CACHE_DB_PATH

OLD_RULE = "issue_date_v5"
NEW_RULE = "issue_date_v6"


def fetch_oracle_metadata():
    """返回 (受影响发行人集合, symbol -> 含权期限说明 映射)"""
    with get_connection() as conn:
        cur = conn.cursor()

        print("① 查询受影响发行人（旗下有隐性永续债）...")
        t0 = time.time()
        cur.execute(r"""
            SELECT DISTINCT n.COMPNAME
            FROM TQ_BD_BASICINFO b
            JOIN TQ_BD_NEWESTBASICINFO n ON n.SECODE = b.SECODE AND n.ISVALID = 1
            WHERE b.ISVALID = 1
              AND UPPER(b.CVTBDEXPIREMEMP) LIKE '%+N'
              AND b.BONDSNAME NOT LIKE '%永续%'
              AND b.BONDSNAME NOT LIKE '%二级%'
              AND UPPER(b.BONDSNAME) NOT LIKE '%TLAC%'
              AND NOT REGEXP_LIKE(b.BONDSNAME, 'Y[0-9]+$')
        """)
        affected_issuers = {row[0] for row in cur.fetchall()}
        print(f"   受影响发行人: {len(affected_issuers)} 个 ({time.time()-t0:.1f}s)")

        print("② 拉取含权期限说明字段映射 (SYMBOL -> CVTBDEXPIREMEMP)...")
        t0 = time.time()
        cur.execute("""
            SELECT b.SYMBOL, b.CVTBDEXPIREMEMP
            FROM TQ_BD_BASICINFO b
            WHERE b.ISVALID = 1
              AND b.CVTBDEXPIREMEMP IS NOT NULL
              AND b.SYMBOL IS NOT NULL
        """)
        memo_map = {}
        for symbol, memo in cur.fetchall():
            if memo and str(memo).strip():
                memo_map[str(symbol)] = str(memo).strip()
        print(f"   含权字段映射: {len(memo_map)} 条 ({time.time()-t0:.1f}s)")

    return affected_issuers, memo_map


def migrate():
    affected_issuers, memo_map = fetch_oracle_metadata()

    conn = init_cache_db(CACHE_DB_PATH)

    v5_issuers = {r[0] for r in conn.execute(
        "SELECT issuer FROM issuer_summary WHERE issue_date_rule = ?", (OLD_RULE,))}
    skip_issuers = v5_issuers & affected_issuers
    migrate_issuers = v5_issuers - affected_issuers
    print(f"\n③ v5 发行人 {len(v5_issuers)} 个："
          f"迁移 {len(migrate_issuers)} 个，跳过待重算 {len(skip_issuers)} 个")

    # ── 原地升级 bond_deviations ──
    print("④ 原地升级 bond_deviations（版本标记 + 回填 bond_type / cvtbd_expire）...")
    t0 = time.time()
    rows = conn.execute(
        "SELECT symbol, bond_name, issuer FROM bond_deviations "
        "WHERE issue_date_rule = ?", (OLD_RULE,)).fetchall()

    updates = []
    for symbol, bond_name, issuer in rows:
        if issuer in skip_issuers:
            continue
        memo = memo_map.get(str(symbol), "")
        btype = classify_bond_type(bond_name or "", memo or None)
        updates.append((NEW_RULE, btype, memo, symbol))

    conn.executemany(
        "UPDATE bond_deviations "
        "SET issue_date_rule = ?, bond_type = ?, cvtbd_expire = ? "
        "WHERE symbol = ? AND issue_date_rule = '%s'" % OLD_RULE,
        updates,
    )
    conn.commit()
    print(f"   已升级债券行: {len(updates)} ({time.time()-t0:.1f}s)")

    # ── 原地升级 issuer_summary ──
    print("⑤ 原地升级 issuer_summary ...")
    conn.executemany(
        "UPDATE issuer_summary SET issue_date_rule = ? "
        "WHERE issuer = ? AND issue_date_rule = '%s'" % OLD_RULE,
        [(NEW_RULE, issuer) for issuer in migrate_issuers],
    )
    conn.commit()

    # ── 校验 ──
    n_v6 = conn.execute(
        "SELECT COUNT(*) FROM bond_deviations WHERE issue_date_rule = ?",
        (NEW_RULE,)).fetchone()[0]
    n_v5_left = conn.execute(
        "SELECT COUNT(*) FROM bond_deviations WHERE issue_date_rule = ?",
        (OLD_RULE,)).fetchone()[0]
    n_v6_sum = conn.execute(
        "SELECT COUNT(*) FROM issuer_summary WHERE issue_date_rule = ?",
        (NEW_RULE,)).fetchone()[0]
    n_memo = conn.execute(
        "SELECT COUNT(*) FROM bond_deviations WHERE issue_date_rule = ? "
        "AND cvtbd_expire IS NOT NULL AND cvtbd_expire != ''", (NEW_RULE,)).fetchone()[0]
    n_perp = conn.execute(
        "SELECT COUNT(*) FROM bond_deviations WHERE issue_date_rule = ? "
        "AND bond_type = 'perpetual'", (NEW_RULE,)).fetchone()[0]
    conn.close()

    print("\n" + "=" * 55)
    print("迁移完成")
    print(f"  v6 债券行数:        {n_v6}")
    print(f"  v6 发行人汇总:      {n_v6_sum}")
    print(f"  含权期限说明非空:   {n_memo}")
    print(f"  其中永续债:         {n_perp}")
    print(f"  仍为 v5(待重算覆盖): {n_v5_left}")
    print("=" * 55)
    print("\n下一步：python cache_builder.py --once  （只会补算待重算发行人）")


if __name__ == "__main__":
    migrate()
