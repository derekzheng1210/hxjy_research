# -*- coding: utf-8 -*-
"""把公司知识库"内部"根目录的 77 个报告迁移到按月子文件夹（每月 <=200MB）。

流程（安全顺序）：
  1. 上传每个文件到 dir=内部/{年月}（重试3次）
  2. 拉取全量列表核对：每个标题在目标子文件夹恰好1条、无重复孤儿
  3. 全部确认后才删除根目录原条目（subjectFormat 为空）
  4. 终审：根目录=0，各月份数量/体积符合预期，回写 manifest
"""
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).parent))
from upload_fim_reports import (  # noqa: E402
    DELETE_URL, LIST_URL, DEST_ROOT, MANIFEST, all_files, encode_name,
    load_auth,
)

urllib3.disable_warnings()
LOG = DEST_ROOT / "reorganize_log.txt"
BUCKET_MB_LIMIT = 200


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def bucket_of(row: dict) -> str:
    y, m = row["报告日期"][:4], int(row["报告日期"][5:7])
    return f"{y}年{m}月"


def upload(session, headers, file_path: Path, dir_: str) -> dict:
    data = {"dir": dir_}
    with open(file_path, "rb") as f:
        files = {"file": (encode_name(file_path.name), f, "application/octet-stream")}
        resp = session.post(
            "https://iam.hxjyam.com/fim-api/fixed-income-biz/emailInfo/uploadFile",
            headers=headers, data=data, files=files, timeout=900,
        )
    resp.raise_for_status()
    return resp.json()


def delete_ids(session, headers, ids: list) -> bool:
    for k in range(0, len(ids), 10):
        r = session.post(DELETE_URL, headers=headers, json={"idList": ids[k:k + 10]}, timeout=60)
        if r.json().get("code") != "00000":
            log(f"删除失败: {ids[k:k + 10]} -> {r.text[:200]}")
            return False
    return True


def main() -> int:
    company = sys.argv[1] if len(sys.argv) > 1 else "内部"
    rows = [r for r in load_manifest_rows() if r["目标文件夹"] == company]
    if not rows:
        print(f"{company} 无待迁移文件")
        return 1
    headers = load_auth()
    session = requests.Session()
    session.verify = False

    # 分桶核对（每月一桶，均需 <200MB）
    buckets = defaultdict(lambda: [0, 0])
    for r in rows:
        b = bucket_of(r)
        buckets[b][0] += 1
        buckets[b][1] += int(r["大小"])
    log(f"{company} 分桶方案:")
    for b in sorted(buckets):
        n, s = buckets[b]
        flag = "OK" if s < BUCKET_MB_LIMIT * 1048576 else "超限!"
        log(f"  {b}: {n}个 {s / 1048576:.1f}MB {flag}")
    if any(s >= BUCKET_MB_LIMIT * 1048576 for _, s in buckets.values()):
        log("存在超限桶，终止")
        return 1

    # 阶段1：上传到月度子文件夹
    failed_upload = []
    for i, r in enumerate(rows, 1):
        b = bucket_of(r)
        path = DEST_ROOT / company / r["上传文件名"]
        ok = False
        for attempt in range(1, 4):
            try:
                result = upload(session, headers, path, f"{company}/{b}")
                if result.get("code") == "00000":
                    ok = True
                    break
                log(f"[上传] {i}/{len(rows)} 第{attempt}次 code={result.get('code')} {r['上传文件名']}")
            except Exception as e:  # noqa: BLE001
                log(f"[上传] {i}/{len(rows)} 第{attempt}次异常 {e} {r['上传文件名']}")
            time.sleep(3 * attempt)
        if ok:
            if i % 10 == 0 or i == len(rows):
                log(f"[上传] 进度 {i}/{len(rows)}")
        else:
            failed_upload.append(r["上传文件名"])
            log(f"[上传] 最终失败 {r['上传文件名']}（其根目录条目将保留）")
    if failed_upload:
        log(f"有 {len(failed_upload)} 个上传失败，不执行删除。失败清单: {failed_upload}")
        return 1

    # 阶段2：核对子文件夹条目
    time.sleep(2)
    files = all_files(session, headers)
    by_key = defaultdict(list)
    for f in files:
        if f.get("companyShortName") == company:
            by_key[(f.get("subjectFormat") or "", f.get("emailTitle"))].append(f["id"])
    problems = []
    root_ids = []
    for r in rows:
        b = bucket_of(r)
        stem = r["上传文件名"].rsplit(".", 1)[0]
        in_bucket = by_key.get((b, stem), [])
        if len(in_bucket) != 1:
            problems.append(f"{b}/{stem} 子文件夹条目数={len(in_bucket)}")
        root_ids.extend(by_key.get(("", stem), []))
    if problems:
        log("核对发现问题，不执行删除：")
        for p in problems:
            log(f"  {p}")
        return 1
    log(f"[核对] {len(rows)} 个标题在月度子文件夹各恰好1条；根目录待删条目 {len(root_ids)} 个")

    # 阶段3：删除根目录原条目
    if not delete_ids(session, headers, sorted(root_ids)):
        return 1
    log(f"[删除] 根目录 {len(root_ids)} 条已删除")

    # 阶段4：终审
    time.sleep(2)
    files = all_files(session, headers)
    roots = [f for f in files if f.get("companyShortName") == company and not f.get("subjectFormat")]
    stat = defaultdict(lambda: [0, 0])
    for f in files:
        if f.get("companyShortName") == company and f.get("subjectFormat") in buckets:
            stat[f["subjectFormat"]][0] += 1
    log(f"[终审] {company} 根目录剩余 {len(roots)} 条")
    for b in sorted(stat):
        log(f"[终审] {b}: {stat[b][0]} 个")
    if roots:
        log("[终审] 根目录仍有残留，请人工检查")

    # 回写 manifest：目标文件夹 -> {company}/{年月}
    all_rows = load_manifest_rows()
    for r in all_rows:
        if r["目标文件夹"] == company:
            r["目标文件夹"] = f"{company}/{bucket_of(r)}"
    with open(MANIFEST, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    log("[完成] manifest 已更新为 {company}/{年月}".replace("{company}", company))
    return 0


def load_manifest_rows() -> list:
    with open(MANIFEST, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    sys.exit(main())
