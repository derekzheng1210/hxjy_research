"""
小范围测试脚本 - 探查数据库表结构 & 验证计算逻辑

目标：
1. 确认 COUPONRATE（票面利率）在哪个表
2. 确认 ISSENDDATE（发行截止日）在哪个表
3. 确认隐含评级在哪个表
4. 验证中债估值和曲线数据可获取
5. 用1-2个发行人的案例完整跑通计算逻辑

运行：python test_small.py
"""

from __future__ import annotations

import sys
from datetime import datetime, date

import pandas as pd

sys.path.insert(0, ".")
import config
from db_utils import get_connection, rows_as_dicts


def test_step1_explore_bond_tables(conn):
    """Step 1: 探查债券基本信息表，确认票面利率和发行截止日字段"""
    cur = conn.cursor()
    print("=" * 60)
    print("Step 1: 探查 TQ_BD_NEWESTBASICINFO 表字段")
    print("=" * 60)

    # 查看表的列名
    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM ALL_TAB_COLUMNS
        WHERE TABLE_NAME = 'TQ_BD_NEWESTBASICINFO'
        AND OWNER = 'FINCHINA'
        ORDER BY COLUMN_ID
    """)
    cols = cur.fetchall()
    print(f"\nTQ_BD_NEWESTBASICINFO 共 {len(cols)} 个字段:")
    for name, dtype in cols:
        print(f"  {name} ({dtype})")

    # 检查是否有COUPONRATE字段
    col_names = [c[0] for c in cols]
    has_coupon = "COUPONRATE" in col_names
    has_issenddate = "ISSENDDATE" in col_names
    print(f"\n  → COUPONRATE 字段: {'✅ 存在' if has_coupon else '❌ 不存在'}")
    print(f"  → ISSENDDATE 字段: {'✅ 存在' if has_issenddate else '❌ 不存在'}")

    return has_coupon, has_issenddate


def test_step1b_explore_basicinfo(conn):
    """探查 TQ_BD_BASICINFO 表（更完整的债券基本信息表）"""
    cur = conn.cursor()
    print("\n" + "=" * 60)
    print("Step 1b: 探查 TQ_BD_BASICINFO 表字段")
    print("=" * 60)

    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM ALL_TAB_COLUMNS
        WHERE TABLE_NAME = 'TQ_BD_BASICINFO'
        AND OWNER = 'FINCHINA'
        ORDER BY COLUMN_ID
    """)
    cols = cur.fetchall()
    print(f"\nTQ_BD_BASICINFO 共 {len(cols)} 个字段:")
    for name, dtype in cols:
        print(f"  {name} ({dtype})")

    col_names = [c[0] for c in cols]
    has_coupon = "COUPONRATE" in col_names
    has_issenddate = "ISSENDDATE" in col_names
    print(f"\n  → COUPONRATE 字段: {'✅ 存在' if has_coupon else '❌ 不存在'}")
    print(f"  → ISSENDDATE 字段: {'✅ 存在' if has_issenddate else '❌ 不存在'}")

    return has_coupon, has_issenddate


def test_step2_sample_bonds(conn):
    """Step 2: 取一些近期发行的债券样本，看看数据长什么样"""
    cur = conn.cursor()
    print("\n" + "=" * 60)
    print("Step 2: 取近期发行债券样本（近1个月）")
    print("=" * 60)

    # 先尝试从 TQ_BD_BASICINFO 获取（更可能有完整字段）
    sql = """
        SELECT SECODE, SYMBOL, BONDSNAME, COMPNAME, COUPONRATE,
               ISSENDDATE, STARTDATE, MATURITYDATE, MATURITYYEAR
        FROM TQ_BD_BASICINFO
        WHERE ISVALID = 1
          AND ISSENDDATE >= '20260601'
          AND ISSENDDATE <= '20260703'
          AND COUPONRATE IS NOT NULL
          AND COUPONRATE > 0
          AND ROWNUM <= 20
        ORDER BY ISSENDDATE DESC
    """
    try:
        cur.execute(sql)
        rows = rows_as_dicts(cur)
        if rows:
            print(f"\n从 TQ_BD_BASICINFO 取到 {len(rows)} 条近期发行债券:")
            df = pd.DataFrame(rows)
            print(df.to_string(index=False))
            return rows, "TQ_BD_BASICINFO"
    except Exception as e:
        print(f"  TQ_BD_BASICINFO 查询失败: {e}")

    # 备选：从 NEWESTBASICINFO 查
    sql2 = """
        SELECT SECODE, SYMBOL, BONDSNAME, COMPNAME, COUPONRATE,
               STARTDATE, MATURITYDATE
        FROM TQ_BD_NEWESTBASICINFO
        WHERE ISVALID = 1
          AND STARTDATE >= '20260601'
          AND STARTDATE <= '20260703'
          AND ROWNUM <= 20
        ORDER BY STARTDATE DESC
    """
    try:
        cur.execute(sql2)
        rows = rows_as_dicts(cur)
        if rows:
            print(f"\n从 TQ_BD_NEWESTBASICINFO 取到 {len(rows)} 条:")
            df = pd.DataFrame(rows)
            print(df.to_string(index=False))
            return rows, "TQ_BD_NEWESTBASICINFO"
    except Exception as e:
        print(f"  TQ_BD_NEWESTBASICINFO 查询失败: {e}")

    return [], ""


def test_step3_implied_rating(conn):
    """Step 3: 探查隐含评级数据"""
    cur = conn.cursor()
    print("\n" + "=" * 60)
    print("Step 3: 探查隐含评级相关表")
    print("=" * 60)

    # 先看看 TQ_BD_BONDYIELDS 里是否有评级信息
    candidate_tables = [
        "TQ_BD_BONDYIELDS",
        "TQ_BD_IMPLIEDRATING",
        "TQ_BD_CBIMPLIEDRATING",
        "TQ_QT_CBESTIMATE",
    ]

    for table in candidate_tables:
        try:
            cur.execute(f"""
                SELECT COLUMN_NAME, DATA_TYPE
                FROM ALL_TAB_COLUMNS
                WHERE TABLE_NAME = '{table}'
                AND OWNER = 'FINCHINA'
                ORDER BY COLUMN_ID
            """)
            cols = cur.fetchall()
            if cols:
                print(f"\n✅ {table} 存在，共 {len(cols)} 个字段:")
                for name, dtype in cols:
                    print(f"    {name} ({dtype})")
            else:
                print(f"\n❌ {table} 不存在或无权限")
        except Exception as e:
            print(f"\n❌ {table} 查询失败: {e}")

    # 也检查 BESTIMATE 表
    try:
        cur.execute("""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM ALL_TAB_COLUMNS
            WHERE TABLE_NAME = 'BESTIMATE'
            AND OWNER = 'FINCHINA'
            ORDER BY COLUMN_ID
        """)
        cols = cur.fetchall()
        if cols:
            print(f"\n✅ BESTIMATE 存在，共 {len(cols)} 个字段:")
            for name, dtype in cols:
                print(f"    {name} ({dtype})")
    except Exception as e:
        print(f"\n❌ BESTIMATE 查询失败: {e}")


def test_step4_curve_data(conn):
    """Step 4: 验证曲线数据可获取"""
    cur = conn.cursor()
    print("\n" + "=" * 60)
    print("Step 4: 验证曲线数据（AA+曲线，代码216）")
    print("=" * 60)

    # 查最新的曲线数据日期
    cur.execute("""
        SELECT MAX(TRADEDATE) FROM TQ_QT_YIELDCURVE
        WHERE YCURVECODE = '216'
          AND YCURVETYPE = '1'
          AND ISVALID = 1
    """)
    row = cur.fetchone()
    latest_date = row[0] if row else None
    print(f"\n  AA+曲线最新日期: {latest_date}")

    if latest_date:
        # 取该日的完整曲线
        cur.execute("""
            SELECT MATURITY, YIELD
            FROM TQ_QT_YIELDCURVE
            WHERE TRADEDATE = :trade_date
              AND YCURVECODE = '216'
              AND YCURVETYPE = '1'
              AND ISVALID = 1
            ORDER BY MATURITY
        """, {"trade_date": str(latest_date)})
        points = cur.fetchall()
        print(f"  AA+曲线点位数: {len(points)}")
        print("  期限 → 收益率:")
        for maturity, yld in points[:15]:
            print(f"    {maturity}Y → {yld:.4f}%")

    return latest_date


def test_step5_bond_valuation(conn, sample_bonds: list):
    """Step 5: 验证债券估值可获取"""
    cur = conn.cursor()
    print("\n" + "=" * 60)
    print("Step 5: 验证中债估值（TQ_QT_CBESTIMATE）")
    print("=" * 60)

    if not sample_bonds:
        print("  无样本债券，跳过")
        return

    # 取第一只样本债券的SECODE
    test_secode = sample_bonds[0]["SECODE"]
    test_name = sample_bonds[0].get("BONDSNAME", "")
    print(f"\n  测试债券: {test_name} (SECODE={test_secode})")

    # 查该债券的最新估值
    cur.execute("""
        SELECT TRADEDATE, YIELD, DATASOURCE, VALUATIONTYPE
        FROM TQ_QT_CBESTIMATE
        WHERE SECODE = :secode
          AND ISVALID = 1
          AND YIELD IS NOT NULL
          AND ROWNUM <= 10
        ORDER BY TRADEDATE DESC
    """, {"secode": test_secode})
    rows = cur.fetchall()
    if rows:
        print(f"  最近估值记录 ({len(rows)} 条):")
        for td, yld, ds, vt in rows[:5]:
            print(f"    日期={td}, 估值={yld:.4f}%, 数据源={ds}, 估值类型={vt}")
    else:
        print("  ❌ 无估值记录，尝试 BESTIMATE 表...")
        cur.execute("""
            SELECT TDATE, YIELD, DATASOURCE
            FROM BESTIMATE
            WHERE SYMBOL = :symbol
              AND YIELD IS NOT NULL
              AND ROWNUM <= 10
            ORDER BY TDATE DESC
        """, {"symbol": sample_bonds[0].get("SYMBOL", "")})
        rows2 = cur.fetchall()
        if rows2:
            print(f"  BESTIMATE 表有 {len(rows2)} 条记录:")
            for td, yld, ds in rows2[:5]:
                print(f"    日期={td}, 估值={yld:.4f}%, 数据源={ds}")


def test_step6_issuer_outstanding(conn, sample_bonds: list, bond_table: str):
    """Step 6: 查找某发行人的存续债"""
    cur = conn.cursor()
    print("\n" + "=" * 60)
    print("Step 6: 查找发行人存续债")
    print("=" * 60)

    if not sample_bonds:
        print("  无样本债券，跳过")
        return

    issuer = sample_bonds[0].get("COMPNAME", "")
    issue_date = sample_bonds[0].get("ISSENDDATE", "") or sample_bonds[0].get("STARTDATE", "")
    print(f"\n  发行人: {issuer}")
    print(f"  发行截止日: {issue_date}")

    # 查该发行人在发行截止日的存续债
    sql = f"""
        SELECT SECODE, SYMBOL, BONDSNAME, STARTDATE, MATURITYDATE, MATURITYYEAR
        FROM {bond_table}
        WHERE COMPNAME = :issuer
          AND ISVALID = 1
          AND STARTDATE <= :issue_date
          AND MATURITYDATE > :issue_date
          AND ROWNUM <= 50
        ORDER BY MATURITYDATE
    """
    try:
        cur.execute(sql, {"issuer": issuer, "issue_date": issue_date})
        rows = rows_as_dicts(cur)
        print(f"  存续债数量: {len(rows)}")
        if rows:
            df = pd.DataFrame(rows)
            print(df.to_string(index=False))
    except Exception as e:
        print(f"  查询失败: {e}")
        # 尝试用 NEWESTBASICINFO
        try:
            cur.execute("""
                SELECT SECODE, SYMBOL, BONDSNAME, STARTDATE, MATURITYDATE
                FROM TQ_BD_NEWESTBASICINFO
                WHERE COMPNAME = :issuer
                  AND ISVALID = 1
                  AND STARTDATE <= :issue_date
                  AND MATURITYDATE > :issue_date
                  AND ROWNUM <= 50
                ORDER BY MATURITYDATE
            """, {"issuer": issuer, "issue_date": issue_date})
            rows = rows_as_dicts(cur)
            print(f"  (备选表) 存续债数量: {len(rows)}")
            if rows:
                df = pd.DataFrame(rows)
                print(df.to_string(index=False))
        except Exception as e2:
            print(f"  备选查询也失败: {e2}")


def test_step7_full_calculation(conn, sample_bonds: list, bond_table: str):
    """Step 7: 完整计算一只债券的偏离度（端到端验证）"""
    cur = conn.cursor()
    print("\n" + "=" * 60)
    print("Step 7: 端到端计算验证")
    print("=" * 60)

    if not sample_bonds:
        print("  无样本债券，跳过")
        return

    bond = sample_bonds[0]
    coupon = bond.get("COUPONRATE")
    issuer = bond.get("COMPNAME", "")
    issue_date = bond.get("ISSENDDATE", "") or bond.get("STARTDATE", "")
    maturity_year = bond.get("MATURITYYEAR")
    bond_name = bond.get("BONDSNAME", "")

    print(f"\n  目标债券: {bond_name}")
    print(f"  票面利率: {coupon}%")
    print(f"  发行人: {issuer}")
    print(f"  发行截止日: {issue_date}")
    print(f"  期限(年): {maturity_year}")

    if not coupon or not issue_date:
        print("  ❌ 缺少关键数据，无法计算")
        return

    # 1. 查找发行人存续债（在发行截止日时点）
    print("\n  [1] 查找存续债...")
    cur.execute(f"""
        SELECT SECODE, SYMBOL, BONDSNAME, STARTDATE, MATURITYDATE, MATURITYYEAR
        FROM {bond_table}
        WHERE COMPNAME = :issuer
          AND ISVALID = 1
          AND STARTDATE < :issue_date
          AND MATURITYDATE > :issue_date
          AND SECODE != :self_secode
          AND ROWNUM <= 50
    """, {"issuer": issuer, "issue_date": issue_date, "self_secode": bond["SECODE"]})
    outstanding = rows_as_dicts(cur)
    print(f"      存续债数量: {len(outstanding)}")

    if not outstanding:
        print("  ❌ 无存续债，无法计算合理价格")
        return

    # 2. 计算每只存续债的剩余期限
    for b in outstanding:
        mat_date = b.get("MATURITYDATE", "")
        if mat_date and issue_date:
            try:
                mat_dt = datetime.strptime(str(mat_date)[:8], "%Y%m%d")
                iss_dt = datetime.strptime(str(issue_date)[:8], "%Y%m%d")
                b["remaining_years"] = (mat_dt - iss_dt).days / 365.0
            except:
                b["remaining_years"] = None
        else:
            b["remaining_years"] = None

    # 找最接近目标期限的存续债
    target_term = float(maturity_year) if maturity_year else 3.0
    valid_outstanding = [b for b in outstanding if b["remaining_years"] is not None]
    if not valid_outstanding:
        print("  ❌ 存续债期限计算失败")
        return

    closest = min(valid_outstanding, key=lambda x: abs(x["remaining_years"] - target_term))
    print(f"      最接近期限的存续债: {closest['BONDSNAME']} (剩余{closest['remaining_years']:.2f}Y)")

    # 3. 获取该存续债在发行截止日的估值
    print("\n  [2] 获取存续债估值...")

    # 先找发行截止日最近的有估值的日期
    cur.execute("""
        SELECT MAX(TRADEDATE) FROM TQ_QT_CBESTIMATE
        WHERE SECODE = :secode
          AND TRADEDATE <= :trade_date
          AND ISVALID = 1
          AND YIELD IS NOT NULL
    """, {"secode": closest["SECODE"], "trade_date": issue_date})
    val_date_row = cur.fetchone()
    val_date = val_date_row[0] if val_date_row and val_date_row[0] else None

    if not val_date:
        print(f"      ❌ 未找到 {closest['BONDSNAME']} 的估值数据")
        return

    cur.execute("""
        SELECT YIELD, DATASOURCE
        FROM TQ_QT_CBESTIMATE
        WHERE SECODE = :secode
          AND TRADEDATE = :trade_date
          AND ISVALID = 1
          AND YIELD IS NOT NULL
          AND DATASOURCE = '1'
          AND ROWNUM <= 5
    """, {"secode": closest["SECODE"], "trade_date": str(val_date)})
    val_rows = cur.fetchall()
    if not val_rows:
        # 尝试不限制DATASOURCE
        cur.execute("""
            SELECT YIELD, DATASOURCE
            FROM TQ_QT_CBESTIMATE
            WHERE SECODE = :secode
              AND TRADEDATE = :trade_date
              AND ISVALID = 1
              AND YIELD IS NOT NULL
              AND ROWNUM <= 5
        """, {"secode": closest["SECODE"], "trade_date": str(val_date)})
        val_rows = cur.fetchall()

    if not val_rows:
        print(f"      ❌ 估值查询无结果")
        return

    bond_yield = float(val_rows[0][0])
    print(f"      估值日期: {val_date}, 估值收益率: {bond_yield:.4f}%")

    # 4. 获取曲线数据
    print("\n  [3] 获取评级曲线...")
    # 默认用AA+曲线做测试
    curve_code = "216"  # AA+
    rating_label = "AA+"

    # 获取对应日期的曲线（在存续债剩余期限处和目标期限处）
    cur.execute("""
        SELECT MATURITY, YIELD
        FROM TQ_QT_YIELDCURVE
        WHERE TRADEDATE = :trade_date
          AND YCURVECODE = :curve_code
          AND YCURVETYPE = '1'
          AND ISVALID = 1
        ORDER BY MATURITY
    """, {"trade_date": str(val_date), "curve_code": curve_code})
    curve_points = cur.fetchall()

    if not curve_points:
        print(f"      ❌ 曲线数据为空 (日期={val_date}, 代码={curve_code})")
        return

    print(f"      {rating_label}曲线点位数: {len(curve_points)}")

    # 插值：找到存续债期限和目标期限对应的曲线值
    def interpolate_curve(term: float, points: list) -> float | None:
        """线性插值获取曲线上某期限的值"""
        if not points:
            return None
        tenors = [float(p[0]) for p in points]
        yields = [float(p[1]) for p in points]

        if term <= tenors[0]:
            return yields[0]
        if term >= tenors[-1]:
            return yields[-1]

        for i in range(len(tenors) - 1):
            if tenors[i] <= term <= tenors[i + 1]:
                ratio = (term - tenors[i]) / (tenors[i + 1] - tenors[i])
                return yields[i] + ratio * (yields[i + 1] - yields[i])
        return None

    curve_at_outstanding = interpolate_curve(closest["remaining_years"], curve_points)
    curve_at_target = interpolate_curve(target_term, curve_points)

    if curve_at_outstanding is None or curve_at_target is None:
        print(f"      ❌ 曲线插值失败")
        return

    print(f"      曲线@存续债期限({closest['remaining_years']:.2f}Y): {curve_at_outstanding:.4f}%")
    print(f"      曲线@目标期限({target_term:.2f}Y): {curve_at_target:.4f}%")

    # 5. 计算偏离
    print("\n  [4] 计算偏离度...")
    spread = bond_yield - curve_at_outstanding
    fair_price = curve_at_target + spread
    deviation = float(coupon) - fair_price
    deviation_bp = deviation * 100  # 转为BP

    print(f"      存续债偏离: {bond_yield:.4f} - {curve_at_outstanding:.4f} = {spread:.4f}% ({spread*100:.1f}BP)")
    print(f"      合理价格: {curve_at_target:.4f} + {spread:.4f} = {fair_price:.4f}%")
    print(f"      票面利率: {coupon:.4f}%")
    print(f"      一级偏离: {coupon:.4f} - {fair_price:.4f} = {deviation:.4f}% ({deviation_bp:.1f}BP)")
    print()
    if deviation_bp < -config.DEVIATION_THRESHOLD_BP:
        print(f"      ⚠️ 判定: 非市场化发行（低于合理价格 {abs(deviation_bp):.1f}BP > {config.DEVIATION_THRESHOLD_BP}BP）")
    else:
        print(f"      ✅ 判定: 市场化发行（偏离 {deviation_bp:.1f}BP 在合理范围内）")


def main():
    print("=" * 60)
    print("一级发行非市场化评估 - 小范围数据库探查测试")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    with get_connection() as conn:
        # Step 1: 探查表结构
        has_coupon_new, has_issend_new = test_step1_explore_bond_tables(conn)
        has_coupon_basic, has_issend_basic = test_step1b_explore_basicinfo(conn)

        # Step 2: 取样本债券
        sample_bonds, bond_table = test_step2_sample_bonds(conn)

        # Step 3: 探查隐含评级
        test_step3_implied_rating(conn)

        # Step 4: 验证曲线数据
        test_step4_curve_data(conn)

        # Step 5: 验证债券估值
        test_step5_bond_valuation(conn, sample_bonds)

        # Step 6: 查找发行人存续债
        test_step6_issuer_outstanding(conn, sample_bonds, bond_table)

        # Step 7: 端到端计算验证
        test_step7_full_calculation(conn, sample_bonds, bond_table)

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
