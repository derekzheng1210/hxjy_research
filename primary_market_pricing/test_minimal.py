"""最小化测试：连接 + 单条查询"""
import sys, time
sys.path.insert(0, ".")
import config
from db_utils import get_connection

print("1. 正在连接数据库...")
t0 = time.perf_counter()
with get_connection() as conn:
    t1 = time.perf_counter()
    print(f"   连接耗时: {(t1-t0)*1000:.0f}ms")
    
    cur = conn.cursor()
    
    # 最简单的查询
    print("2. 简单 SELECT 1...")
    t0 = time.perf_counter()
    cur.execute("SELECT 1 FROM DUAL")
    cur.fetchone()
    t1 = time.perf_counter()
    print(f"   耗时: {(t1-t0)*1000:.0f}ms")
    
    # 查最新日期
    print("3. MAX(TDATE) from BESTIMATE...")
    t0 = time.perf_counter()
    cur.execute("SELECT MAX(TDATE) FROM BESTIMATE WHERE DATASOURCE = '1'")
    row = cur.fetchone()
    t1 = time.perf_counter()
    trade_date = str(row[0]) if row and row[0] else "无"
    print(f"   耗时: {(t1-t0)*1000:.0f}ms, 日期={trade_date}")
    
    if trade_date != "无":
        # 单条精确查询
        print("4. 取1个symbol...")
        t0 = time.perf_counter()
        cur.execute("""
            SELECT SYMBOL FROM BESTIMATE
            WHERE TDATE = :d AND YIELD IS NOT NULL AND DATASOURCE = '1'
              AND ROWNUM = 1
        """, {"d": trade_date})
        row = cur.fetchone()
        t1 = time.perf_counter()
        symbol = str(row[0]) if row else None
        print(f"   耗时: {(t1-t0)*1000:.0f}ms, symbol={symbol}")
        
        if symbol:
            # 精确点查
            print(f"5. 点查 SYMBOL={symbol} TDATE={trade_date}...")
            t0 = time.perf_counter()
            cur.execute("""
                SELECT YIELD, TERMTOMATURITY FROM BESTIMATE
                WHERE SYMBOL = :s AND TDATE = :d
                  AND YIELD IS NOT NULL AND DATASOURCE = '1'
            """, {"s": symbol, "d": trade_date})
            row = cur.fetchone()
            t1 = time.perf_counter()
            print(f"   耗时: {(t1-t0)*1000:.0f}ms, yield={row[0] if row else 'N/A'}")
            
            # 重复5次看稳定性
            print("6. 重复5次点查...")
            for i in range(5):
                t0 = time.perf_counter()
                cur.execute("""
                    SELECT YIELD, TERMTOMATURITY FROM BESTIMATE
                    WHERE SYMBOL = :s AND TDATE = :d
                      AND YIELD IS NOT NULL AND DATASOURCE = '1'
                """, {"s": symbol, "d": trade_date})
                cur.fetchone()
                t1 = time.perf_counter()
                print(f"   [{i+1}] {(t1-t0)*1000:.0f}ms")
            
            # COUNT查该日总行数
            print(f"7. COUNT TDATE={trade_date}...")
            t0 = time.perf_counter()
            cur.execute("""
                SELECT COUNT(*) FROM BESTIMATE
                WHERE TDATE = :d AND DATASOURCE = '1'
            """, {"d": trade_date})
            count = cur.fetchone()[0]
            t1 = time.perf_counter()
            print(f"   耗时: {(t1-t0)*1000:.0f}ms, 行数={count}")

print("\n完成!")
