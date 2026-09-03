# -*- coding: utf-8 -*-
"""将生产 SQLite 库以一致性快照刷新到测试数据目录。

直接拷贝 WAL 模式下的 .db 文件可能拿到不一致快照（-wal/-shm 由
生产进程持有），这里统一用 sqlite3 backup API 在线备份到测试库，
可重复执行。配合 sync_test_data.bat（文件镜像）使用：
    sync_test_data.bat          # robocopy 镜像 + 调用本脚本刷新数据库
    python refresh_test_dbs.py  # 也可单独执行，仅刷新数据库
"""
import sqlite3
from pathlib import Path

PROD = Path(r"D:\juyuan_credit_data")
TEST = Path(r"D:\hxjy_test_data")

# 运行态活跃库（migration_backups 等静态文件由 robocopy 直接镜像）
DBS = [
    Path("internal_knowledge_base/knowledge_base.db"),
    Path("data/primary_market_pricing/cache.db"),
    Path("interest_bond/bond_switch.db"),
    Path("interest_bond/spreads.db"),
    Path("interest_bond/tracker.db"),
]


def refresh(rel: Path) -> None:
    src = PROD / rel
    dst = TEST / rel
    if not src.is_file():
        print(f"[skip] 生产库不存在：{rel}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 丢掉测试库残留的 wal/shm，避免新旧混用（测试期未提交事务随快照丢弃）
    for suffix in ("-wal", "-shm"):
        stale = dst.with_name(dst.name + suffix)
        if stale.exists():
            stale.unlink()
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    with d:
        s.backup(d)
    s.close()
    d.close()
    print(f"[ok] {rel}")


if __name__ == "__main__":
    for rel in DBS:
        refresh(rel)
    print("[done] 测试库已刷新为生产一致性快照")
