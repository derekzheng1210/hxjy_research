"""Migrate only accounts and the super-administrator password into SQLite.

The source JSON is never modified.  Without ``--apply`` this command only
validates and prints a migration report.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from internal_knowledge_base.storage import SQLiteStore, now_iso  # noqa: E402
from paths import (INTERNAL_KNOWLEDGE_BASE_DB,
                   INTERNAL_KNOWLEDGE_BASE_MIGRATION_BACKUPS)  # noqa: E402


VALID_ROLES = {"leader", "admin", "member"}


def load_source(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    users = payload.get("users")
    admin_password = payload.get("admin_password")
    if not isinstance(users, list):
        raise ValueError("源文件缺少 users 数组")
    if not isinstance(admin_password, str) or not admin_password:
        raise ValueError("源文件缺少有效的超级管理员密码")
    clean = []
    seen = set()
    errors = []
    for index, row in enumerate(users, 1):
        if not isinstance(row, dict):
            errors.append(f"第 {index} 个账号不是对象")
            continue
        uid = str(row.get("id", "")).strip().lower().replace(" ", "")
        name = str(row.get("name", "")).strip()
        org = str(row.get("org", "")).strip()
        role = str(row.get("role", "")).strip()
        password = str(row.get("password", ""))
        if not uid or not name or not password:
            errors.append(f"第 {index} 个账号缺少账号、姓名或密码")
        if uid in seen:
            errors.append(f"重复账号: {uid}")
        if role not in VALID_ROLES:
            errors.append(f"账号 {uid} 的角色无效: {role}")
        seen.add(uid)
        clean.append({"id": uid, "name": name, "org": org, "role": role, "password": password})
    if errors:
        raise ValueError("；".join(errors))
    return clean, admin_password


def report(users, source: Path, database: Path):
    roles = Counter(row["role"] for row in users)
    orgs = Counter(row["org"] or "未设置" for row in users)
    return {
        "sourceFile": source.name,
        "database": str(database.resolve()),
        "accountCount": len(users),
        "roles": dict(sorted(roles.items())),
        "organizations": dict(sorted(orgs.items())),
        "uniqueAccountIds": len({row["id"] for row in users}),
        "reportsMigrated": 0,
        "ratingsMigrated": 0,
        "attachmentsMigrated": 0,
        "passwordsWillBeHashed": len(users) + 1,
    }


def apply_migration(users, admin_password, database: Path, backup_root: Path):
    backup_root.mkdir(parents=True, exist_ok=True)
    if database.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_root / f"knowledge_base_before_account_migration_{stamp}.db"
        shutil.copy2(database, backup)
    store = SQLiteStore(database, {"period": datetime.now().strftime("%Y"), "reportCategory": "deep", "rules": []})
    with store.transaction() as conn:
        for table in ("reports", "ratings", "engagement", "qa_usage", "qa_history", "pdf_cache"):
            if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                raise RuntimeError(f"目标数据库的 {table} 表不是空表，已拒绝账号迁移")
        conn.execute("DELETE FROM users")
        stamp = now_iso()
        conn.executemany(
            "INSERT INTO users(id,name,org,role,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            [(row["id"], row["name"], row["org"], row["role"],
              generate_password_hash(row["password"]), stamp, stamp) for row in users],
        )
        conn.execute(
            "INSERT INTO settings(key,payload,updated_at) VALUES('admin_password_hash',?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (json.dumps(generate_password_hash(admin_password)), stamp),
        )
    return store


def main():
    parser = argparse.ArgumentParser(description="内部知识库账号迁移")
    parser.add_argument("--source", required=True, type=Path, help="原系统 data/store.json")
    parser.add_argument("--database", type=Path, default=INTERNAL_KNOWLEDGE_BASE_DB)
    parser.add_argument("--backup-root", type=Path, default=INTERNAL_KNOWLEDGE_BASE_MIGRATION_BACKUPS)
    parser.add_argument("--apply", action="store_true", help="执行写入；省略时只预检")
    args = parser.parse_args()
    users, admin_password = load_source(args.source)
    result = report(users, args.source, args.database)
    result["mode"] = "apply" if args.apply else "dry-run"
    if args.apply:
        store = apply_migration(users, admin_password, args.database, args.backup_root)
        result["databaseAccountCount"] = len(store.users())
        result["adminPasswordInitialized"] = bool(store.admin_password_hash())
        manifest = args.backup_root / f"account_migration_{datetime.now():%Y%m%d_%H%M%S}.json"
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = str(manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
