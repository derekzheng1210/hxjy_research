# -*- coding: utf-8 -*-
"""资产配置部报告 -> 公司知识库"资配"文件夹 上传脚本。

用法：
  python scripts/upload_zipei.py --test    # 最小文件测试上传到 dir=资配（验证公司文件夹自动创建），验证后删除
  python scripts/upload_zipei.py --batch   # 按 manifest.csv 批量上传到 资配/{年月}（断点续传）
  python scripts/upload_zipei.py --check   # 核对资配各桶数量与标题一致性（含孤儿检测）
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).parent))
from upload_fim_reports import (  # noqa: E402
    BASE, DELETE_URL, UPLOAD_URL, all_files, encode_name, load_auth,
)

urllib3.disable_warnings()
DEST_ROOT = Path(r"D:\资配部报告上传")
DEST_DIR = DEST_ROOT / "资配"
MANIFEST = DEST_ROOT / "manifest.csv"
LOG_PATH = DEST_ROOT / "upload_log.txt"
COMPANY = "资配"
RETRY = 3


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def manifest_rows() -> list:
    with open(MANIFEST, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_manifest(rows: list) -> None:
    with open(MANIFEST, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def upload_one(session: requests.Session, headers: dict, file_path: Path, dir_: str) -> dict:
    data = {"dir": dir_}
    with open(file_path, "rb") as f:
        files = {"file": (encode_name(file_path.name), f, "application/octet-stream")}
        resp = session.post(UPLOAD_URL, headers=headers, data=data, files=files, timeout=900)
    resp.raise_for_status()
    return resp.json()


def delete_ids(session, headers, ids: list) -> bool:
    for k in range(0, len(ids), 10):
        r = session.post(DELETE_URL, headers=headers, json={"idList": ids[k:k + 10]}, timeout=60)
        if r.json().get("code") != "00000":
            log(f"[删除] 失败 {ids[k:k + 10]}: {r.text[:200]}")
            return False
    return True


def do_test(session, headers) -> int:
    rows = sorted(manifest_rows(), key=lambda r: int(r["大小"]))
    row = rows[0]
    path = DEST_DIR / row["上传文件名"]
    log(f"[测试] 上传最小文件: {path.name} ({int(row['大小'])} bytes) -> dir={COMPANY}")
    result = upload_one(session, headers, path, COMPANY)
    log(f"[测试] 返回 code={result.get('code')}")
    if result.get("code") != "00000":
        return 1
    time.sleep(2)
    found = None
    for it in all_files(session, headers):
        if it.get("companyShortName") == COMPANY and (it.get("emailTitle") or "") == path.stem:
            found = it
            break
    if not found:
        log("[测试] 列表中未找到测试文件（标题应为文件名去扩展名）")
        return 1
    log(f"[测试] 列表确认: id={found['id']} emailTitle={found.get('emailTitle')} subjectFormat={found.get('subjectFormat')!r}")
    if not delete_ids(session, headers, [found["id"]]):
        return 1
    time.sleep(2)
    remain = [it for it in all_files(session, headers) if it.get("id") == found["id"]]
    log("[测试] 删除复核: " + ("已消失" if not remain else "仍存在!"))
    companies = session.post(f"{BASE}/emailResearch/getCompanyShortNames", headers=headers, json={}, timeout=60).json()
    log(f"[测试] 公司列表现在: {json.dumps(companies.get('data'), ensure_ascii=False)}")
    return 0 if not remain else 1


def do_batch(session, headers) -> int:
    rows = manifest_rows()
    todo = [r for r in rows if r["上传状态"] != "成功"]
    log(f"[批量] 共 {len(rows)} 个, 待上传 {len(todo)}")
    ok = fail = 0
    for i, row in enumerate(todo, 1):
        path = DEST_DIR / row["上传文件名"]
        if not path.is_file():
            log(f"[批量] {i}/{len(todo)} [缺文件] {path}")
            row["上传状态"] = "缺文件"
            fail += 1
            continue
        success = False
        for attempt in range(1, RETRY + 1):
            try:
                result = upload_one(session, headers, path, row["目标文件夹"])
                if result.get("code") == "00000":
                    success = True
                    break
                log(f"[批量] {i}/{len(todo)} 第{attempt}次 code={result.get('code')} {row['上传文件名']}")
                if result.get("code") in ("A0230", "401"):
                    log("[批量] 凭证失效，终止")
                    return 1
            except Exception as e:  # noqa: BLE001
                log(f"[批量] {i}/{len(todo)} 第{attempt}次异常: {e} {row['上传文件名']}")
            time.sleep(3 * attempt)
        if success:
            row["上传状态"] = "成功"
            ok += 1
            if i % 10 == 0 or i == len(todo):
                log(f"[批量] 进度 {i}/{len(todo)} (累计成功 {ok})")
        else:
            row["上传状态"] = "失败"
            fail += 1
            log(f"[批量] {i}/{len(todo)} 最终失败 {row['目标文件夹']}/{row['上传文件名']}")
        save_manifest(rows)
        time.sleep(1)
    log(f"[批量] 结束: 成功 {ok}, 失败 {fail}")
    return 0 if fail == 0 else 1


def do_check(session, headers) -> int:
    rows = manifest_rows()
    files = all_files(session, headers)
    mine = [r for r in rows if r["目标文件夹"].startswith(COMPANY)]
    expect = Counter((r["目标文件夹"], r["上传文件名"].rsplit(".", 1)[0]) for r in mine)
    server = Counter()
    for f in files:
        comp = f.get("companyShortName")
        if comp == COMPANY:
            bucket = f"{COMPANY}/" + (f.get("subjectFormat") or "(根目录)")
            server[(bucket, f.get("emailTitle"))] += 1
    missing = {k: v for k, v in expect.items() if server.get(k, 0) < v}
    extra = {k: v for k, v in server.items() if expect.get(k, 0) < v}
    total_server = sum(server.values())
    log(f"[核对] 期望 {sum(expect.values())} 个, 服务器 {total_server} 个")
    by_bucket = Counter(b for b, _ in server.elements())
    for b in sorted(by_bucket):
        log(f"[核对] {b}: {by_bucket[b]} 个")
    if missing:
        log(f"[核对] 缺失 {len(missing)}: {list(missing.items())[:10]}")
    if extra:
        log(f"[核对] 多余/孤儿 {len(extra)}: {list(extra.items())[:10]}")
    if not missing and not extra:
        log("[核对] 完全一致 ✓")
        return 0
    # 自动清理孤儿
    orphan_ids = []
    by_title = {}
    for f in files:
        if f.get("companyShortName") == COMPANY:
            bucket = f"{COMPANY}/" + (f.get("subjectFormat") or "(根目录)")
            by_title.setdefault((bucket, f.get("emailTitle")), []).append(f["id"])
    for key, ids in by_title.items():
        want = expect.get(key, 0)
        if len(ids) > want:
            keep = ids[-want:] if want else []
            orphan_ids.extend(i for i in ids if i not in keep)
    if orphan_ids:
        log(f"[核对] 删除孤儿 {len(orphan_ids)} 个: {orphan_ids}")
        if delete_ids(session, headers, sorted(orphan_ids)):
            time.sleep(2)
            return do_check(session, headers)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true")
    group.add_argument("--batch", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    headers = load_auth()
    session = requests.Session()
    session.verify = False
    if args.test:
        return do_test(session, headers)
    if args.batch:
        return do_batch(session, headers)
    return do_check(session, headers)


if __name__ == "__main__":
    sys.exit(main())
