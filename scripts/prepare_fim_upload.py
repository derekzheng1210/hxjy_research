# -*- coding: utf-8 -*-
"""准备上传公司知识库(FIM问答知识库)的固收部报告文件。

从本地内部知识库 SQLite 读取固收中心全部未删除报告，
按"报告标题"重命名后复制到 D:\\固收部报告上传\\{内部,外部}，并生成 manifest.csv。

分类规则（用户确认）：
  内部 <- reportType in (internal, research_visit)
  外部 <- reportType in (external, roadshow)
"""
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DB_PATH = Path(r"D:\juyuan_credit_data\internal_knowledge_base\knowledge_base.db")
UPLOADS_DIR = Path(r"D:\juyuan_credit_data\internal_knowledge_base")
DEST_ROOT = Path(r"D:\固收部报告上传")
MANIFEST = DEST_ROOT / "manifest.csv"

ORG = "固收中心"
INTERNAL_TYPES = {"internal", "research_visit"}
EXTERNAL_TYPES = {"external", "roadshow"}
MAX_FILE_BYTES = 200 * 1024 * 1024  # 公司知识库单文件上限 200MB
MAX_NAME_LEN = 120

ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def sanitize_title(title: str) -> str:
    t = ILLEGAL.sub("＿", title or "").strip().strip(".")
    if len(t) > MAX_NAME_LEN:
        t = t[:MAX_NAME_LEN].rstrip()
    upper = t.upper()
    if upper in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        t = "＿" + t
    return t


def unique_name(folder: Path, stem: str, ext: str) -> str:
    candidate = f"{stem}{ext}"
    n = 2
    while (folder / candidate).exists():
        candidate = f"{stem}({n}){ext}"
        n += 1
    return candidate


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024
    return f"{nbytes:.1f} GB"


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, payload FROM reports WHERE deleted_at IS NULL").fetchall()
    conn.close()

    records, problems = [], []
    for rid, payload in rows:
        p = json.loads(payload)
        if p.get("org") != ORG:
            continue
        rt = p.get("reportType") or ""
        target = "内部" if rt in INTERNAL_TYPES else "外部" if rt in EXTERNAL_TYPES else None
        if target is None:
            problems.append(f"[未知类型] {rid} reportType={rt}")
            continue
        file_url = (p.get("fileUrl") or "").replace("\\", "/")
        src = UPLOADS_DIR / file_url if file_url else None
        if not src or not src.is_file():
            # 兜底：按 report_id 前缀在 uploads 目录查找
            matches = list((UPLOADS_DIR / "uploads").glob(f"{rid}__*")) if (UPLOADS_DIR / "uploads").exists() else []
            src = matches[0] if matches else None
        if not src:
            problems.append(f"[缺文件] {rid} title={p.get('title')}")
            continue
        records.append({
            "report_id": rid,
            "title": (p.get("title") or "").strip() or Path(p.get("fileName") or src.stem).stem,
            "report_type": rt,
            "category": p.get("category") or "",
            "report_date": p.get("reportDate") or "",
            "target": target,
            "src": src,
        })

    if problems:
        print("发现问题：")
        for x in problems:
            print(" ", x)

    for sub in ("内部", "外部"):
        (DEST_ROOT / sub).mkdir(parents=True, exist_ok=True)

    manifest_rows, oversize, used_names = [], [], Counter()
    for r in records:
        folder = DEST_ROOT / r["target"]
        stem = sanitize_title(r["title"])
        if not stem:
            stem = sanitize_title(Path(r["src"].stem).stem)
        ext = r["src"].suffix.lower()
        name = unique_name(folder, stem, ext)
        used_names[(r["target"], name)] += 1
        dst = folder / name
        shutil.copy2(r["src"], dst)
        size = dst.stat().st_size
        digest = sha256_of(dst)
        manifest_rows.append({
            "report_id": r["report_id"],
            "标题": r["title"],
            "上传文件名": name,
            "目标文件夹": r["target"],
            "报告类型": r["report_type"],
            "二级分类": r["category"],
            "报告日期": r["report_date"],
            "大小": size,
            "SHA256": digest,
            "源路径": str(r["src"]),
            "上传状态": "",
        })
        if size >= MAX_FILE_BYTES:
            oversize.append((r["target"], name, size))

    manifest_rows.sort(key=lambda x: (x["目标文件夹"], x["报告日期"], x["上传文件名"]))
    with open(MANIFEST, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(manifest_rows)

    by_target = Counter(r["目标文件夹"] for r in manifest_rows)
    by_type = Counter(r["报告类型"] for r in manifest_rows)
    ext_dist = Counter(Path(r["上传文件名"]).suffix.lower() for r in manifest_rows)
    size_by_target = {t: sum(r["大小"] for r in manifest_rows if r["目标文件夹"] == t) for t in ("内部", "外部")}

    print(f"\n完成：共复制 {len(manifest_rows)} 个文件 -> {DEST_ROOT}")
    print(f"  内部: {by_target.get('内部', 0)} 个, {human(size_by_target['内部'])}")
    print(f"  外部: {by_target.get('外部', 0)} 个, {human(size_by_target['外部'])}")
    print(f"  类型分布: {dict(by_type)}")
    print(f"  扩展名分布: {dict(ext_dist)}")
    print(f"  manifest: {MANIFEST}")

    dup = {k: v for k, v in used_names.items() if v > 1}
    if dup:
        print(f"  [警告] 重名文件: {dup}")
    if oversize:
        print(f"  [警告] 以下 {len(oversize)} 个文件超过 200MB 上限，需请示后处理：")
        for t, n, s in oversize:
            print(f"    {t}/{n} ({human(s)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
