"""
模拟实际业务场景的速度测试

场景：计算单个发行人在某日的偏离度
需要查询：该发行人的 10-30 只存续债在发行日的估值

对比：
A. 逐只点查（SYMBOL + TDATE）
B. 小批量 IN（所有 symbols + TDATE）
C. 日期切片全量（当前方案）—— 可能超慢，设超时保护

运行：python test_business_scenario.py
"""
import sys, time, signal
from datetime import datetime, timedelta
sys.path.insert(0, ".")
import config
from db_utils import get_connection

def fmt(s): return f"{s*1000:.0f}ms"

print("=" * 60)
print("业务场景速度测试")
print("=" * 60)

with get_connection() as conn:
    cur = conn.cursor()
    
    # ─── 准备：找一个真实的发行人和一批存续债 ───
    print("\n[准备] 查找测试发行人...")
    
    # 用一个确定的近期日期（避免MAX扫描）
    # 从最近的数据开始试
    test_dates = ["20260703", "20260702", "20260701", "20260630", "20260627", "20260625"]
    trade_date = None
    
    for d in test_dates:
        t0 = time.perf_counter()
        cur.execute("""
            SELECT 1 FROM BESTIMATE
            WHERE TDATE = :d AND DATASOURCE = '1' AND ROWNUM = 1
        """, {"d": d})
        row = cur.fetchone()
        t1 = time.perf_counter()
        if row:
            trade_date = d
            print(f"  找到有效日期: {d} (检查耗时: {fmt(t1-t0)})")
            break
        else:
            print(f"  {d} 无数据 ({fmt(t1-t0)})")
    
    if not trade_date:
        # 尝试更早的日期
        for d in ["20260620", "20260613", "20260606", "20260530", "20260101", "20251230"]:
            cur.execute("""
                SELECT 1 FROM BESTIMATE
                WHERE TDATE = :d AND DATASOURCE = '1' AND ROWNUM = 1
            """, {"d": d})
            if cur.fetchone():
                trade_date = d
                print(f"  找到有效日期: {d}")
                break
    
    if not trade_date:
        print("  ❌ 找不到有效日期，退出")
        sys.exit(1)
    
    # 找该日期下有估值的一批symbols（模拟某发行人的存续债）
    print(f"\n[准备] 获取 {trade_date} 的测试 symbols...")
    t0 = time.perf_counter()
    cur.execute("""
        SELECT SYMBOL FROM BESTIMATE
        WHERE TDATE = :d AND YIELD IS NOT NULL AND DATASOURCE = '1'
          AND ROWNUM <= 25
    """, {"d": trade_date})
    symbols = [str(r[0]) for r in cur.fetchall()]
    t1 = time.perf_counter()
    print(f"  获取 {len(symbols)} 只 symbols ({fmt(t1-t0)})")
    
    if len(symbols) < 3:
        print("  ❌ symbols 不足")
        sys.exit(1)
    
    # 预热：让 Oracle 缓存表的元数据
    print("\n[预热] 3次查询消除冷启动...")
    for _ in range(3):
        cur.execute("""
            SELECT YIELD FROM BESTIMATE
            WHERE SYMBOL = :s AND TDATE = :d AND DATASOURCE = '1'
        """, {"s": symbols[0], "d": trade_date})
        cur.fetchone()
    
    # ─────────────────────────────────────────────
    # 测试 A：逐只点查
    # ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"测试 A: 逐只精确查询 ({len(symbols)} 只, 每只1条SQL)")
    print("─" * 60)
    
    sql_point = """
        SELECT YIELD, TERMTOMATURITY FROM BESTIMATE
        WHERE SYMBOL = :s AND TDATE = :d
          AND YIELD IS NOT NULL AND DATASOURCE = '1'
    """
    
    t0 = time.perf_counter()
    results_a = {}
    for sym in symbols:
        cur.execute(sql_point, {"s": sym, "d": trade_date})
        row = cur.fetchone()
        if row:
            results_a[sym] = {"yield": float(row[0]), "term": float(row[1])}
    t1 = time.perf_counter()
    time_a = t1 - t0
    print(f"  总耗时: {fmt(time_a)}")
    print(f"  命中: {len(results_a)}/{len(symbols)}")
    print(f"  每只均摊: {fmt(time_a/len(symbols))}")
    
    # ─────────────────────────────────────────────
    # 测试 B：IN 查询
    # ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"测试 B: 单条 IN 查询 ({len(symbols)} 只 SYMBOL + 1 个 TDATE)")
    print("─" * 60)
    
    placeholders = ",".join(f":s{i}" for i in range(len(symbols)))
    params = {f"s{i}": s for i, s in enumerate(symbols)}
    params["d"] = trade_date
    
    sql_in = f"""
        SELECT SYMBOL, YIELD, TERMTOMATURITY FROM BESTIMATE
        WHERE SYMBOL IN ({placeholders})
          AND TDATE = :d
          AND YIELD IS NOT NULL AND DATASOURCE = '1'
    """
    
    t0 = time.perf_counter()
    cur.execute(sql_in, params)
    rows = cur.fetchall()
    results_b = {str(r[0]): {"yield": float(r[1]), "term": float(r[2])} for r in rows}
    t1 = time.perf_counter()
    time_b = t1 - t0
    print(f"  总耗时: {fmt(time_b)}")
    print(f"  命中: {len(results_b)}/{len(symbols)}")
    
    # ─────────────────────────────────────────────
    # 测试 C：日期切片（限时）
    # ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"测试 C: 全量日期切片 (TDATE={trade_date}, 限时30s)")
    print("─" * 60)
    
    cur.arraysize = 5000
    t0 = time.perf_counter()
    try:
        cur.execute("""
            SELECT SYMBOL, YIELD, TERMTOMATURITY FROM BESTIMATE
            WHERE TDATE = :d AND YIELD IS NOT NULL AND DATASOURCE = '1'
        """, {"d": trade_date})
        
        results_c = {}
        batch_count = 0
        while True:
            batch = cur.fetchmany(5000)
            if not batch:
                break
            batch_count += 1
            for sym, yld, term in batch:
                results_c[str(sym)] = {"yield": float(yld), "term": float(term)}
            # 超时保护
            elapsed = time.perf_counter() - t0
            if elapsed > 30:
                print(f"  ⚠️ 超时30s，已读 {len(results_c)} 行，中止")
                break
        t1 = time.perf_counter()
        time_c = t1 - t0
        hit = sum(1 for s in symbols if s in results_c)
        print(f"  总耗时: {fmt(time_c)}")
        print(f"  全量行数: {len(results_c)}")
        print(f"  目标命中: {hit}/{len(symbols)}")
    except Exception as e:
        t1 = time.perf_counter()
        time_c = t1 - t0
        print(f"  ❌ 失败: {e} (耗时 {fmt(time_c)})")
    
    # ─────────────────────────────────────────────
    # 测试 D：回退场景模拟
    # ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("测试 D: 回退场景 (逐日回退找估值, IN查询)")
    print("─" * 60)
    
    target_dt = datetime.strptime(trade_date, "%Y%m%d")
    
    t0 = time.perf_counter()
    remaining = list(symbols)
    results_d = {}
    for delta in range(6):
        if not remaining:
            break
        check_date = (target_dt - timedelta(days=delta)).strftime("%Y%m%d")
        
        ph = ",".join(f":s{i}" for i in range(len(remaining)))
        p = {f"s{i}": s for i, s in enumerate(remaining)}
        p["d"] = check_date
        
        cur.execute(f"""
            SELECT SYMBOL, YIELD, TERMTOMATURITY FROM BESTIMATE
            WHERE SYMBOL IN ({ph}) AND TDATE = :d
              AND YIELD IS NOT NULL AND DATASOURCE = '1'
        """, p)
        for sym, yld, term in cur.fetchall():
            results_d[str(sym)] = {"yield": float(yld), "term": float(term)}
        
        old_remaining = len(remaining)
        remaining = [s for s in remaining if s not in results_d]
        found = old_remaining - len(remaining)
        elapsed_so_far = time.perf_counter() - t0
        print(f"  {check_date}: +{found} 命中, 剩余 {len(remaining)} ({fmt(elapsed_so_far)})")
    
    t1 = time.perf_counter()
    time_d = t1 - t0
    print(f"  总耗时: {fmt(time_d)}")
    print(f"  最终命中: {len(results_d)}/{len(symbols)}")
    
    # ─────────────────────────────────────────────
    # 测试 E：逐只回退
    # ─────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("测试 E: 回退场景 (逐只逐日回退)")
    print("─" * 60)
    
    t0 = time.perf_counter()
    results_e = {}
    for sym in symbols:
        for delta in range(6):
            check_date = (target_dt - timedelta(days=delta)).strftime("%Y%m%d")
            cur.execute(sql_point, {"s": sym, "d": check_date})
            row = cur.fetchone()
            if row:
                results_e[sym] = {"yield": float(row[0]), "term": float(row[1])}
                break
    t1 = time.perf_counter()
    time_e = t1 - t0
    print(f"  总耗时: {fmt(time_e)}")
    print(f"  命中: {len(results_e)}/{len(symbols)}")
    
    # ─────────────────────────────────────────────
    # 汇总
    # ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("性能对比汇总")
    print("=" * 60)
    
    best_direct = min(time_a, time_b)
    print(f"""
场景: {len(symbols)} 只存续债, 日期 {trade_date}

直接查询（无回退）:
  A. 逐只点查: {fmt(time_a):>10}  ({len(results_a)} 命中)
  B. IN 查询:  {fmt(time_b):>10}  ({len(results_b)} 命中)
  C. 全量切片: {fmt(time_c):>10}  ({len(results_c)} 全量行)

带回退（逐日向前找）:
  D. IN查询回退:  {fmt(time_d):>10}  ({len(results_d)} 命中)
  E. 逐只点查回退: {fmt(time_e):>10}  ({len(results_e)} 命中)
""")

    # 结论
    print("─" * 60)
    print("结论与建议:")
    print("─" * 60)
    
    if time_b < time_a:
        print(f"  ✅ IN查询 ({fmt(time_b)}) 优于逐只点查 ({fmt(time_a)})")
        print(f"     IN查询减少了网络往返次数: {len(symbols)}次 → 1次")
    else:
        print(f"  ⚠️ 逐只点查 ({fmt(time_a)}) 略优于IN查询 ({fmt(time_b)})")
    
    if time_c > 0 and time_b < time_c * 0.5:
        speedup = time_c / time_b
        print(f"  ✅ IN查询比全量切片快 {speedup:.0f}x !")
        print(f"     全量切片读了 {len(results_c)} 行，实际只需 {len(symbols)} 行")
        print(f"     推荐：替换 fetch_all_valuations_for_date 为 IN 查询")
    
    print(f"\n  最终推荐方案: ", end="")
    if time_d <= time_e and time_d < time_c:
        print("IN查询 + 逐日回退（方案D）")
        print(f"     预期单个发行人计算耗时: ~{fmt(time_d)}")
    elif time_e < time_d:
        print("逐只点查 + 逐日回退（方案E）")
        print(f"     预期单个发行人计算耗时: ~{fmt(time_e)}")
    else:
        print("全量日期切片（当前方案C）仍然最优")

print("\n完成!")
