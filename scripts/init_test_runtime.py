# -*- coding: utf-8 -*-
"""初始化隔离的测试运行时数据目录（.test_runtime）。

从生产数据目录复制一份真实数据供测试实例使用：
- knowledge_base.db：用 sqlite3 backup API 复制（WAL 模式下直接拷贝 .db 文件不安全）
- uploads / pdf_cache：robocopy 增量复制（可重复执行）

生产目录只读访问，不会被修改。用法：
    python scripts/init_test_runtime.py            # 首次（含文件复制）
    python scripts/init_test_runtime.py --db-only  # 只刷新数据库（丢弃测试期数据，重置为生产快照）
"""
import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_DATA_ROOT = Path(os.environ.get("PROD_DATA_ROOT", REPO_ROOT.parent / "juyuan_credit_data"))
PROD_IKB = PROD_DATA_ROOT / "internal_knowledge_base"
TEST_DATA_ROOT = Path(os.environ.get("TEST_DATA_ROOT", REPO_ROOT / ".test_runtime"))
TEST_IKB = TEST_DATA_ROOT / "internal_knowledge_base"


def copy_db(src_db: Path, dst_db: Path) -> None:
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    # 丢掉测试库残留的 wal/shm，避免新旧混用
    for suffix in ("-wal", "-shm"):
        stale = dst_db.with_name(dst_db.name + suffix)
        if stale.exists():
            stale.unlink()
    src = sqlite3.connect(str(src_db))
    dst = sqlite3.connect(str(dst_db))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def robocopy(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["robocopy", str(src), str(dst), "/E", "/XO", "/NFL", "/NDL", "/NJH", "/NP"],
        capture_output=True, text=True,
    )
    # robocopy 退出码 <8 均为成功
    if result.returncode >= 8:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(f"robocopy 失败，退出码 {result.returncode}")


def main() -> None:
    db_src = PROD_IKB / "knowledge_base.db"
    if not db_src.is_file():
        raise SystemExit(f"未找到生产数据库：{db_src}")
    copy_db(db_src, TEST_IKB / "knowledge_base.db")
    print(f"[ok] 数据库已复制 -> {TEST_IKB / 'knowledge_base.db'}")

    args = parse_args()
    if not args.db_only:
        for name in ("uploads", "pdf_cache"):
            src = PROD_IKB / name
            if src.is_dir():
                robocopy(src, TEST_IKB / name)
                print(f"[ok] {name}/ 已同步（增量）")
    for name in ("temp",):
        (TEST_IKB / name).mkdir(parents=True, exist_ok=True)
    print(f"[done] 测试数据目录就绪：{TEST_DATA_ROOT}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-only", action="store_true", help="只刷新数据库，不复制上传文件")
    return parser.parse_args()


if __name__ == "__main__":
    main()
