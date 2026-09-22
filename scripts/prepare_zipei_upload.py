# -*- coding: utf-8 -*-
"""资产配置部报告上传公司知识库"资配"文件夹 —— 本地预备。

从本地内部知识库 SQLite 读取 org=资产配置部 全部未删除报告，
按报告标题改名复制到 D:\\资配部报告上传\\资配\\，按月分桶（单月>=200MB 拆上/下半年），
超过 15MB（网关限制）的文件转 PDF 后替换，生成 manifest.csv。

manifest 的 目标文件夹 直接写为 "资配/{年月}月" 或 "资配/{年月}月(上/下)"，供上传脚本使用。
"""
import csv
import hashlib
import json
import shutil
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shrink_for_fim import SAFE_LIMIT, cached_pdf, convert_pdf, shrink_office, shrink_pdf  # noqa: E402,F401
from rasterize_for_fim import rasterize  # noqa: E402

DB_PATH = Path(r"D:\juyuan_credit_data\internal_knowledge_base\knowledge_base.db")
UPLOADS_DIR = Path(r"D:\juyuan_credit_data\internal_knowledge_base")
DEST_ROOT = Path(r"D:\资配部报告上传")
DEST_DIR = DEST_ROOT / "资配"
BACKUP_DIR = DEST_ROOT / "_原件备份"
MANIFEST = DEST_ROOT / "manifest.csv"
ORG = "资产配置部"
BUCKET_LIMIT = 200 * 1048576

ILLEGAL = set('\\/:*?"<>|\r\n\t')


def sanitize(title: str) -> str:
    t = "".join("＿" if c in ILLEGAL else c for c in (title or "")).strip().strip(".")
    return (t[:120].rstrip()) or "未命名"


def unique_name(folder: Path, stem: str, ext: str) -> str:
    cand, n = f"{stem}{ext}", 2
    while (folder / cand).exists():
        cand = f"{stem}({n}){ext}"
        n += 1
    return cand


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def shrink_to_limit(src: Path, sha256: str) -> Path | None:
    """把超 15MB 的文件压到限内：office 优先已有PDF缓存/转PDF；PDF 重写/光栅化。"""
    SHRINK_WORK = DEST_ROOT / "_shrink_work"
    SHRINK_WORK.mkdir(exist_ok=True)
    attempts = []
    if src.suffix.lower() in (".pptx", ".docx"):
        cand = cached_pdf(src, sha256)
        if cand:
            attempts.append(lambda: shutil.copy2(cand, SHRINK_WORK / (src.stem + ".pdf")))
        attempts.append(lambda: convert_pdf(src, SHRINK_WORK / src.name))
    else:
        attempts.append(lambda: shrink_pdf(src, SHRINK_WORK / src.name))
        attempts.append(lambda: rasterize(src, SHRINK_WORK / src.name))
    for fn in attempts:
        try:
            out = fn()
        except Exception as e:  # noqa: BLE001
            print(f"    [转换失败] {e}")
            continue
        if out.stat().st_size < SAFE_LIMIT:
            return out
        print(f"    [仍超限] {out.name} {out.stat().st_size / 1048576:.1f}MB，尝试下一方案")
    return None


def main() -> int:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, payload FROM reports WHERE deleted_at IS NULL").fetchall()
    conn.close()

    recs = []
    for rid, payload in rows:
        p = json.loads(payload)
        if p.get("org") != ORG:
            continue
        src = UPLOADS_DIR / (p.get("fileUrl") or "").replace(chr(92), "/")
        if not src.is_file():
            m = list((UPLOADS_DIR / "uploads").glob(f"{rid}__*"))
            src = m[0] if m else None
        if not src:
            print(f"[缺文件] {rid} {p.get('title')}")
            continue
        recs.append({"rid": rid, "title": (p.get("title") or "").strip() or src.stem,
                     "date": p.get("reportDate") or "", "rtype": p.get("reportType") or "",
                     "src": src, "size": src.stat().st_size})

    # 月度分桶；单月 >=200MB 拆上/下（按报告日 15 号分界），逐桶校验
    month_tot = defaultdict(int)
    for r in recs:
        month_tot[r["date"][:7]] += r["size"]
    split_months = {m for m, s in month_tot.items() if s >= BUCKET_LIMIT}

    def bucket_of(r: dict) -> str:
        d = r["date"]
        y, m = d[:4], int(d[5:7])
        base = f"{y}年{m}月"
        if r["date"][:7] in split_months:
            base += "(上)" if int(d[8:10]) <= 15 else "(下)"
        return base

    buckets = defaultdict(lambda: [0, 0])
    for r in recs:
        buckets[bucket_of(r)][0] += 1
        buckets[bucket_of(r)][1] += r["size"]

    print(f"共 {len(recs)} 篇，分桶 {len(buckets)} 个（拆分月份: {sorted(split_months) or '无'}）")
    manifest_rows = []
    for i, r in enumerate(recs, 1):
        bucket = bucket_of(r)
        stem = sanitize(r["title"])
        name = unique_name(DEST_DIR, stem, r["src"].suffix.lower())
        dst = DEST_DIR / name
        shutil.copy2(r["src"], dst)
        converted = ""
        if dst.stat().st_size >= SAFE_LIMIT:
            fixed = shrink_to_limit(dst, sha256_of(dst))
            if fixed:
                (BACKUP_DIR / name).unlink(missing_ok=True)
                shutil.move(str(dst), BACKUP_DIR / name)  # 原件移入备份目录，不改本地知识库
                final = DEST_DIR / (dst.stem + ".pdf")
                shutil.copy2(fixed, final)
                dst = final
                converted = "转PDF"
            else:
                print(f"  [警告] 无法压到限内，保留原件待人工处理: {name}")
        size = dst.stat().st_size
        manifest_rows.append({
            "report_id": r["rid"], "标题": r["title"], "上传文件名": dst.name,
            "目标文件夹": f"资配/{bucket}", "报告类型": r["rtype"], "报告日期": r["date"],
            "大小": size, "SHA256": sha256_of(dst), "源路径": str(r["src"]),
            "转换": converted, "上传状态": "",
        })
        if i % 20 == 0 or i == len(recs):
            print(f"  进度 {i}/{len(recs)}")

    manifest_rows.sort(key=lambda x: (x["目标文件夹"], x["报告日期"], x["上传文件名"]))
    with open(MANIFEST, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        w.writeheader()
        w.writerows(manifest_rows)

    fin = defaultdict(lambda: [0, 0])
    for r in manifest_rows:
        fin[r["目标文件夹"]][0] += 1
        fin[r["目标文件夹"]][1] += r["大小"]
    print("\n最终分桶（转换后实际体积）:")
    bad = []
    for b in sorted(fin):
        n, s = fin[b]
        flag = "OK" if s < BUCKET_LIMIT else "超限!"
        if flag != "OK":
            bad.append(b)
        print(f"  {b}: {n}个 {s / 1048576:6.1f}MB {flag}")
    n_pdf = sum(1 for r in manifest_rows if r["转换"])
    print(f"转PDF: {n_pdf} 个; manifest: {MANIFEST}")
    if bad:
        print(f"[错误] 仍有超限桶: {bad}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
