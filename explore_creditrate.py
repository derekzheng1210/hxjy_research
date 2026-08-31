"""探索债券评级发生日期：以 25浙能源MTN008(科创债) (102584843.IB) 为例。

数据源结论：
- TQ_BD_CREDITRATE        债项评级变动表（附件元数据表），按 SECODE 关联，覆盖有债项评级的债券；
                         本券为科创债、无债项评级，故 0 条。
- TQ_BD_CREDITRATEINFO    债项+主体评级信息表，按 SECURITYID 关联（TQ_BD_BASICINFO.SECURITYID，
                         104 系列），无债项评级的债券也会记录挂钩的主体评级及日期。
- TQ_BD_NEWESTBASICINFO   主档最新/首次评级（NEWISSUERATE/FIRISSUERATE 及日期）。

用法：python explore_creditrate.py [Wind代码，默认 102584843.IB]
"""
import sys

from juyuan_update.db import connect, resolve_bond_codes

BOND_CODE = sys.argv[1] if len(sys.argv) > 1 else "102584843.IB"

with connect() as conn:
    cur = conn.cursor()

    meta = resolve_bond_codes(conn, [BOND_CODE]).get(BOND_CODE)
    if not meta:
        raise SystemExit(f"未解析到 {BOND_CODE} 的 SECODE")
    secode = meta["secode"]
    print(f"[1] {BOND_CODE} -> SECODE={secode}  {meta['name']}")

    cur.execute(
        "SELECT SECURITYID, BONDSNAME, ISSUECOMPCODE FROM TQ_BD_BASICINFO WHERE SECODE = :s",
        {"s": secode},
    )
    basic = cur.fetchone()
    security_id, bondsname, issuecomp = basic
    print(f"    SECURITYID={security_id}  简称={bondsname}  发行人码={issuecomp}")

    print("\n[2] TQ_BD_CREDITRATE 债项评级变动（附件表）:")
    cur.execute(
        """
        SELECT PUBLISHDATE, CREDITDATE, CREDITRATE, RATEMODE, RADJUSTDIR, EXPTRATING,
               RATECOMNAME, ISVALID
        FROM TQ_BD_CREDITRATE
        WHERE SECODE = :s
        ORDER BY CREDITDATE
        """,
        {"s": secode},
    )
    rows = cur.fetchall()
    if not rows:
        print("    （0 条：该债券无债项评级，科创债仅主体评级发行）")
    for r in rows:
        print("   ", r)

    print("\n[3] TQ_BD_CREDITRATEINFO 债项+主体评级（含主体评级日期）:")
    cur.execute(
        """
        SELECT RATEDATE, CREDITRATE, ISSUERATEDATE, ISSUECREDITRATE,
               RATECOMPNAME, ISVALID
        FROM TQ_BD_CREDITRATEINFO
        WHERE SECURITYID = :sid
        ORDER BY ISSUERATEDATE, RATEDATE
        """,
        {"sid": security_id},
    )
    for r in cur.fetchall():
        print("   ", r)

    print("\n[4] 主档 TQ_BD_NEWESTBASICINFO:")
    cur.execute(
        """
        SELECT FIRISSUERATE, FIRISSUERATEDATE, NEWISSUERATE, NEWISSUERATEDATE, NEWISSUERATECOMP
        FROM TQ_BD_NEWESTBASICINFO WHERE SECODE = :s
        """,
        {"s": secode},
    )
    print("   ", cur.fetchone())
