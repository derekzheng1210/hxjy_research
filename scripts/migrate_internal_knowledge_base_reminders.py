"""Migrate the source system's topic-report requirements into SQLite.

Only the reminder configuration is imported. Reports, ratings, attachments,
interaction records and PDF caches are never copied by this command.
Without ``--apply`` the command validates the source and prints a report.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from internal_knowledge_base.storage import SQLiteStore, now_iso  # noqa: E402
from paths import (INTERNAL_KNOWLEDGE_BASE_DB,
                   INTERNAL_KNOWLEDGE_BASE_MIGRATION_BACKUPS)  # noqa: E402


VALID_CATEGORIES = {"weekly", "monthly", "deep", "other"}


def _clean_config(raw_config, source_users):
    if not isinstance(raw_config, dict):
        raise ValueError("源文件缺少 reminder_config 对象")
    period = str(raw_config.get("period", "")).strip()
    if len(period) != 4 or not period.isdigit():
        raise ValueError("专题要求统计年度格式无效，应为 YYYY")
    category = str(raw_config.get("reportCategory", "deep")).strip()
    if category not in VALID_CATEGORIES:
        raise ValueError(f"专题报告分类无效: {category}")
    rules = raw_config.get("rules")
    if not isinstance(rules, list):
        raise ValueError("专题要求 rules 必须是数组")
    if len(rules) > 100:
        raise ValueError("专题要求规则不能超过 100 条")

    source_user_ids = {str(row.get("id", "")).strip().lower().replace(" ", "")
                       for row in source_users if isinstance(row, dict)}
    clean_rules = []
    missing_users = set()
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            raise ValueError(f"第 {index} 条专题要求不是对象")
        label = str(rule.get("label", "")).strip()[:80]
        if not label:
            raise ValueError(f"第 {index} 条专题要求缺少名称")
        mode = "person" if rule.get("mode") == "person" else "group"
        try:
            target = int(rule.get("target", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 条专题要求目标数量无效") from exc
        if not 0 <= target <= 100:
            raise ValueError(f"第 {index} 条专题要求目标数量应在 0-100 之间")
        user_ids = []
        for raw_uid in rule.get("userIds") or []:
            uid = str(raw_uid).strip().lower().replace(" ", "")
            if uid and uid not in user_ids:
                user_ids.append(uid)
                if uid not in source_user_ids:
                    missing_users.add(uid)
        clean_rules.append({
            "id": str(rule.get("id") or f"rule-{index}"),
            "label": label,
            "mode": mode,
            "target": target,
            "userIds": user_ids,
        })
    if missing_users:
        raise ValueError("专题要求引用了源账号中不存在的人员: " + ", ".join(sorted(missing_users)))
    return {"period": period, "reportCategory": category, "rules": clean_rules}


def load_source(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    users = payload.get("users")
    if not isinstance(users, list):
        raise ValueError("源文件缺少 users 数组")
    return _clean_config(payload.get("reminder_config"), users), users


def report(config, source: Path, database: Path):
    rules = config["rules"]
    return {
        "sourceFile": source.name,
        "database": str(database.resolve()),
        "period": config["period"],
        "reportCategory": config["reportCategory"],
        "ruleCount": len(rules),
        "targetTotal": sum(rule["target"] for rule in rules),
        "rules": config["rules"],
        "reportsMigrated": 0,
        "ratingsMigrated": 0,
        "attachmentsMigrated": 0,
        "pdfCachesMigrated": 0,
    }


def apply_migration(config, database: Path, backup_root: Path):
    backup_root.mkdir(parents=True, exist_ok=True)
    if database.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_root / f"knowledge_base_before_reminder_migration_{stamp}.db"
        shutil.copy2(database, backup)
    store = SQLiteStore(database, config)
    target_user_ids = {row["id"] for row in store.users()}
    missing = sorted({uid for rule in config["rules"] for uid in rule["userIds"]
                      if uid not in target_user_ids})
    if missing:
        raise RuntimeError("目标数据库缺少专题要求引用的账号: " + ", ".join(missing))
    store.set_reminder_config(config)
    store.audit(
        "migration", None, "migrate_reminder_config", target_type="settings",
        target_id="reminder_config", detail={"period": config["period"],
                                               "ruleCount": len(config["rules"])},
    )
    return store


def main():
    parser = argparse.ArgumentParser(description="内部知识库专题要求迁移")
    parser.add_argument("--source", required=True, type=Path, help="原系统 data/store.json")
    parser.add_argument("--database", type=Path, default=INTERNAL_KNOWLEDGE_BASE_DB)
    parser.add_argument("--backup-root", type=Path, default=INTERNAL_KNOWLEDGE_BASE_MIGRATION_BACKUPS)
    parser.add_argument("--apply", action="store_true", help="执行写入；省略时只预检")
    args = parser.parse_args()

    config, users = load_source(args.source)
    result = report(config, args.source, args.database)
    result["sourceAccountCount"] = len(users)
    result["mode"] = "apply" if args.apply else "dry-run"
    if args.apply:
        store = apply_migration(config, args.database, args.backup_root)
        result["databaseAccountCount"] = len(store.users())
        manifest = args.backup_root / f"reminder_migration_{datetime.now():%Y%m%d_%H%M%S}.json"
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = str(manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
