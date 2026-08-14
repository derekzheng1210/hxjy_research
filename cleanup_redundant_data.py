"""Audit and remove redundant portal data without touching active files.

Run without --apply first to inspect candidates. The script only removes a
mirror after confirming that its JS payload is semantically identical, and only
removes the legacy pricing cache after confirming the active cache is newer and
at least as complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UPLOADS_DIR = ROOT / "uploads"
ACTIVE_PRICING_CACHE = ROOT / "data" / "primary_market_pricing" / "cache.db"
LEGACY_PRICING_CACHE = ROOT / "primary_market_pricing" / "cache.db"
MIRROR_PAIRS = (
    (ROOT / "data" / "spread_monitor" / "spread_data.json", ROOT / "data" / "spread_monitor" / "spread_data.js"),
    (ROOT / "data" / "credit_std_dev" / "data" / "spread_data.json", ROOT / "data" / "credit_std_dev" / "data" / "spread_data.js"),
)
BACKUP_PATTERN = re.compile(
    r"^(?P<key>.+)_backup_(?P<stamp>\d{8}_\d{6}(?:_\d{6})?)(?P<suffix>\.[^.]+)$"
)
JS_PATTERN = re.compile(r"var\s+SPREAD_DATA\s*=\s*(\{.*\})\s*;?\s*$", re.S)
CACHE_TABLES = ("bond_deviations", "issuer_summary", "valuation_slices", "unpriced_cache_repair_log")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def js_payload(path: Path) -> dict | None:
    match = JS_PATTERN.fullmatch(path.read_text(encoding="utf-8", errors="replace").strip())
    return json.loads(match.group(1)) if match else None


def mirrored_payloads_match(json_path: Path, js_path: Path) -> bool:
    try:
        return json.loads(json_path.read_text(encoding="utf-8")) == js_payload(js_path)
    except (OSError, json.JSONDecodeError):
        return False


def cache_is_superseded(active: Path, legacy: Path) -> tuple[bool, str]:
    if not active.exists() or not legacy.exists():
        return False, "active or legacy cache is missing"
    try:
        with sqlite3.connect(f"file:{active.as_posix()}?mode=ro", uri=True) as active_conn, sqlite3.connect(
            f"file:{legacy.as_posix()}?mode=ro", uri=True
        ) as legacy_conn:
            active_tables = {row[0] for row in active_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            legacy_tables = {row[0] for row in legacy_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not set(CACHE_TABLES).issubset(active_tables & legacy_tables):
                return False, "cache table sets are incompatible"
            active_end = active_conn.execute("SELECT last_end_date FROM run_meta WHERE id = 1").fetchone()
            legacy_end = legacy_conn.execute("SELECT last_end_date FROM run_meta WHERE id = 1").fetchone()
            if not active_end or not legacy_end or str(active_end[0]) < str(legacy_end[0]):
                return False, "active cache is not newer"
            for table in CACHE_TABLES:
                active_count = active_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                legacy_count = legacy_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if active_count < legacy_count:
                    return False, f"active cache has fewer {table} rows"
    except sqlite3.Error as exc:
        return False, f"cache validation failed: {exc}"
    return True, "active cache has compatible tables, no fewer rows, and a newer run date"


def add_candidate(report: dict, path: Path, reason: str) -> None:
    report["candidates"].append(
        {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "reason": reason}
    )


def collect_backup_candidates(report: dict, keep: int) -> None:
    groups: dict[tuple[str, str], list[tuple[str, Path]]] = {}
    for path in UPLOADS_DIR.iterdir() if UPLOADS_DIR.exists() else ():
        if not path.is_file():
            continue
        match = BACKUP_PATTERN.fullmatch(path.name)
        if match:
            groups.setdefault((match["key"], match["suffix"].lower()), []).append((match["stamp"], path))

    for files in groups.values():
        distinct_hashes: set[str] = set()
        retained = 0
        for _, path in sorted(files, reverse=True):
            digest = sha256(path)
            if digest in distinct_hashes:
                add_candidate(report, path, "duplicate backup content")
                continue
            distinct_hashes.add(digest)
            if retained >= keep:
                add_candidate(report, path, f"older than {keep} retained distinct backups")
                continue
            retained += 1


def build_report(keep: int) -> dict:
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "retained_backups_per_type": keep,
        "candidates": [],
        "skipped": [],
    }
    for json_path, js_path in MIRROR_PAIRS:
        if json_path.exists() and js_path.exists() and mirrored_payloads_match(json_path, js_path):
            add_candidate(report, json_path, "JSON payload is identical to active JS payload")
        elif json_path.exists():
            report["skipped"].append({"path": str(json_path.relative_to(ROOT)), "reason": "mirror payloads differ or cannot be parsed"})

    cache_ok, cache_reason = cache_is_superseded(ACTIVE_PRICING_CACHE, LEGACY_PRICING_CACHE)
    if cache_ok:
        add_candidate(report, LEGACY_PRICING_CACHE, cache_reason)
    else:
        report["skipped"].append({"path": str(LEGACY_PRICING_CACHE.relative_to(ROOT)), "reason": cache_reason})
    collect_backup_candidates(report, keep)
    report["candidate_bytes"] = sum(item["bytes"] for item in report["candidates"])
    return report


def apply_report(report: dict) -> None:
    removed = []
    failed = []
    for item in report["candidates"]:
        path = ROOT / item["path"]
        if path.exists():
            try:
                path.unlink()
                removed.append(item)
            except OSError as exc:
                failed.append({**item, "error": str(exc)})
    report["removed"] = removed
    report["removed_bytes"] = sum(item["bytes"] for item in removed)
    report["failed"] = failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit or clean redundant portal data")
    parser.add_argument("--apply", action="store_true", help="delete the candidates reported by this run")
    parser.add_argument("--keep-backups", type=int, default=3, help="distinct backups to retain for each upload type")
    parser.add_argument("--report", type=Path, help="optional JSON report destination")
    args = parser.parse_args()
    if args.keep_backups < 1:
        parser.error("--keep-backups must be at least 1")

    report = build_report(args.keep_backups)
    if args.apply:
        apply_report(report)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
