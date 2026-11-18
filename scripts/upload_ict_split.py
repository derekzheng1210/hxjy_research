# -*- coding: utf-8 -*-
"""ICT 大文件兜底：拆分为多部分 + 逐部分光栅化 <15MB 后上传，回写 manifest。"""
import csv
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\hxjy_research\scripts")
from sync_fim_new_reports import (  # noqa: E402
    UPLOAD_URL, all_files, auth_headers, encode_name, fim_login, load_env, sanitize,
)
from rasterize_for_fim import rasterize  # noqa: E402
import pymupdf  # noqa: E402
import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()
OUT = []


def log(m):
    OUT.append(str(m))


def main():
    conn = sqlite3.connect(r"file:D:/juyuan_credit_data/internal_knowledge_base/knowledge_base.db?mode=ro", uri=True)
    src = None
    for rid, payload in conn.execute("SELECT id, payload FROM reports WHERE deleted_at IS NULL"):
        p = json.loads(payload)
        if "ICT" in (p.get("title") or ""):
            src = (rid, p, Path(r"D:\juyuan_credit_data\internal_knowledge_base") / p["fileUrl"].replace("\\", "/"))
    conn.close()
    rid, p, src_path = src
    title = p["title"].strip()
    log(f"源文件 {src_path.stat().st_size / 1048576:.1f}MB report_id={rid[-8:]}")

    work = Path(r"D:\固收部报告上传\外部\2026年9月(下)\_split_work")
    work.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(src_path)
    n = doc.page_count
    doc.close()
    log(f"页数 {n}")

    chosen = None
    for parts in (2, 3, 4, 6):
        per = (n + parts - 1) // parts
        outs, ok = [], True
        for i in range(parts):
            lo, hi = i * per, min((i + 1) * per, n)
            if lo >= hi:
                break
            part_pdf = work / f"part{i + 1}.pdf"
            sd = pymupdf.open(src_path)
            new = pymupdf.open()
            new.insert_pdf(sd, from_page=lo, to_page=hi - 1)
            new.save(part_pdf)
            new.close()
            sd.close()
            r = rasterize(part_pdf, work / f"part{i + 1}_r.pdf", 100, 50)
            size = r.stat().st_size
            log(f"  第{i + 1}部分(页{lo + 1}-{hi}): {size / 1048576:.1f}MB")
            if size >= 15 * 1048576:
                ok = False
            outs.append(r)
        if ok:
            chosen = outs
            break
    if not chosen:
        log("无法拆分到限内")
        return 1
    log(f"拆分方案: {len(chosen)} 部分")

    s, cred = fim_login(load_env())
    h = auth_headers(cred)
    bucket = "外部/2026年9月(下)"
    stem = sanitize(title)
    rows_out = []
    for i, f in enumerate(chosen, 1):
        final = work.parent / f"{stem}（第{i}部分）.pdf"
        if final.exists():
            final.unlink()
        f.rename(final)
        data = {"dir": bucket}
        with open(final, "rb") as fh:
            resp = s.post(UPLOAD_URL, headers=h, data=data,
                          files={"file": (encode_name(final.name), fh, "application/octet-stream")},
                          timeout=300)
        code = resp.json().get("code")
        time.sleep(2)
        files = all_files(s, h)
        hit = [x for x in files
               if x.get("companyShortName") == "外部"
               and (x.get("subjectFormat") or "") == "2026年9月(下)"
               and (x.get("emailTitle") or "") == final.stem]
        log(f"上传 第{i}部分: code={code} 服务器确认={len(hit)}条")
        rows_out.append({"report_id": rid, "标题": title, "上传文件名": final.name,
                         "目标文件夹": bucket, "报告类型": p.get("reportType") or "",
                         "二级分类": "", "报告日期": p.get("reportDate") or "",
                         "大小": final.stat().st_size,
                         "SHA256": hashlib.sha256(final.read_bytes()).hexdigest(),
                         "源路径": str(src_path),
                         "上传状态": "成功" if (code == "00000" and hit) else "失败"})
    shutil.rmtree(work, ignore_errors=True)
    mpath = Path(r"D:\固收部报告上传\manifest.csv")
    rows = list(csv.DictReader(open(mpath, encoding="utf-8-sig")))
    fields = list(rows[0].keys())
    rows.extend({k: r.get(k, "") for k in fields} for r in rows_out)
    with open(mpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    log(f"manifest 已追加, 共 {len(rows)} 行")
    ok_all = all(r["上传状态"] == "成功" for r in rows_out)
    log("结果: " + ("全部成功" if ok_all else "有失败"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    code = main()
    Path(r"D:\固收部报告上传\ict_result.txt").write_text("\n".join(OUT), encoding="utf-8")
    sys.exit(code)
