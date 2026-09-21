# -*- coding: utf-8 -*-
"""一次性回填债券余额：从聚源 TQ_BD_NEWESTBASICINFO 拉取每只券的
NVL(CURRENTAMT, ACTISSAMT)（单位：亿元），写入 bond_static.json 与
oracle_bond_candidate.json 每只债券的 outstanding_amount 字段。

背景：候选池刷新链路（oracle_bonds.py）已随代码带上余额字段，但两个存量
JSON（含 Excel 时代的 bond_static.json）里没有。本脚本按 SYMBOL 匹配补齐，
跑一次即可，之后由池刷新/应用流程自动维护；找不到的保持 None。

用法（项目根目录执行）：
    .venv/Scripts/python.exe scripts/backfill_outstanding_amount.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import oracledb  # noqa: E402

from juyuan_update import config  # noqa: E402
from juyuan_update.unified_excel import write_json  # noqa: E402


def connect():
    client = os.environ.get("JUYUAN_ORACLE_CLIENT", r"C:\oracle\instantclient_23_0")
    if client and os.path.isdir(client):
        try:
            oracledb.init_oracle_client(lib_dir=client)
        except Exception as exc:
            if "already" not in str(exc).lower():
                raise
    return oracledb.connect(
        user=os.environ.get("JUYUAN_DB_USER", "finchina"),
        password=os.environ.get("JUYUAN_DB_PASSWORD", "finchina"),
        dsn=os.environ.get("JUYUAN_DB_DSN", "10.6.60.118:1521/orcl"),
    )


def fetch_outstanding_by_symbol() -> dict[str, float]:
    """全量拉取有效主表 SYMBOL -> 债券余额（亿元）；跨市场多行时取最大值。"""
    sql = (
        "SELECT SYMBOL, NVL(CURRENTAMT, ACTISSAMT) "
        "FROM TQ_BD_NEWESTBASICINFO WHERE ISVALID = 1 AND SYMBOL IS NOT NULL"
    )
    amounts: dict[str, float] = {}
    with connect() as conn:
        cur = conn.cursor()
        cur.arraysize = 5000
        cur.execute(sql)
        for symbol, amount in cur:
            key = str(symbol).strip()
            if not key or amount is None:
                continue
            try:
                value = float(amount)
            except (TypeError, ValueError):
                continue
            if key not in amounts or value > amounts[key]:
                amounts[key] = value
    return amounts


def patch_file(path: Path, amounts: dict[str, float], dry_run: bool) -> tuple[int, int]:
    """返回（补齐数量，未匹配数量）。文件不存在或无 bonds 时跳过。"""
    if not path.exists():
        print(f"[skip] {path.name} 不存在")
        return 0, 0
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    bonds = payload.get("bonds") or []
    patched = missing = 0
    for bond in bonds:
        # bond_static（Excel 时代）的 raw_code 可能带 .IB/.SH 等后缀，统一剥掉再匹配主表 SYMBOL
        symbol = str(bond.get("raw_code") or bond.get("code") or "").strip()
        if symbol.endswith((".IB", ".SH", ".SZ", ".BJ")):
            symbol = symbol.rsplit(".", 1)[0]
        amount = amounts.get(symbol)
        if amount is None:
            missing += 1
            continue
        bond["outstanding_amount"] = round(amount, 4)
        patched += 1
    print(f"[{'dry-run' if dry_run else 'write'}] {path.name}: 补齐 {patched} / 未匹配 {missing} / 共 {len(bonds)}")
    if not dry_run and bonds:
        write_json(path, payload)
    return patched, missing


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    amounts = fetch_outstanding_by_symbol()
    print(f"聚源主表有效 SYMBOL 余额 {len(amounts):,} 条")
    for path in (config.BOND_STATIC_JSON, config.ORACLE_BOND_CANDIDATE_JSON):
        patch_file(path, amounts, dry_run)


if __name__ == "__main__":
    main()
