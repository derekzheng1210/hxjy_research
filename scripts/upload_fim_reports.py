# -*- coding: utf-8 -*-
"""把 D:\\固收部报告上传 中的报告上传到公司知识库(FIM问答知识库)。

用法：
  python scripts/upload_fim_reports.py --test     # 上传1个最小PDF验证链路，验证后删除
  python scripts/upload_fim_reports.py --batch    # 按 manifest.csv 批量上传（断点续传）
  python scripts/upload_fim_reports.py --check    # 只核对两个文件夹当前文件数

接口（自 FIM 前端 indexPage.1f14873f.js 反推）：
  上传  POST /fim-api/fixed-income-biz/emailInfo/uploadFile      multipart: file(URL编码名), dir=公司名
  列表  POST /fim-api/fixed-income-biz/emailResearch/getEmailList json: {type, pageNo, pageSize, companyShortName...}
  删除  POST /fim-api/fixed-income-biz/emailResearch/deleteEmailFlie json: {idList:[id]}
鉴权头（自 index.149b6fe5.js 拦截器）：
  Authorization: Bearer <portal_token>, userName, userId
"""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

DEST_ROOT = Path(r"D:\固收部报告上传")
MANIFEST = DEST_ROOT / "manifest.csv"
LOG_PATH = DEST_ROOT / "upload_log.txt"
TOKEN_PATH = Path(os.environ.get("TEMP", r"C:\Users\ficc\AppData\Local\Temp")) / "fim_portal_token.json"
BASE = "https://iam.hxjyam.com/fim-api/fixed-income-biz"
UPLOAD_URL = f"{BASE}/emailInfo/uploadFile"
LIST_URL = f"{BASE}/emailResearch/getEmailList"
DELETE_URL = f"{BASE}/emailResearch/deleteEmailFlie"
COMPANIES_URL = f"{BASE}/emailResearch/getCompanyShortNames"
RETRY = 3
# encodeURIComponent 不转义的字段
JS_ENCODE_SAFE = "-_.!~*'()"


def load_auth() -> dict:
    data = json.loads(TOKEN_PATH.read_text("utf8"))
    return {
        "Authorization": f"Bearer {data['token']}",
        "userName": data["userName"],
        "userId": data["userId"],
        "Cookie": f"portal_token={data['token']}",
    }


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def encode_name(name: str) -> str:
    return quote(name, safe=JS_ENCODE_SAFE)


def upload_one(session: requests.Session, headers: dict, file_path: Path, company: str) -> dict:
    data = {"dir": company}
    # 前端行为: file 字段文件名经 encodeURIComponent 编码, dir 不编码
    with open(file_path, "rb") as f:
        files = {"file": (encode_name(file_path.name), f, "application/octet-stream")}
        resp = session.post(UPLOAD_URL, headers=headers, data=data, files=files, timeout=900)
    resp.raise_for_status()
    return resp.json()


def get_list(session: requests.Session, headers: dict, company: str = "", page_size: int = 10, page_no: int = 1) -> dict:
    payload = {"type": "", "pageNo": page_no, "pageSize": page_size}
    if company:
        payload["companyShortName"] = company
    resp = session.post(LIST_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def all_files(session: requests.Session, headers: dict) -> list:
    """全量文件列表（首页聚合 newestEmailList，含 id/emailTitle/companyShortName/subjectFormat）。"""
    data = (get_list(session, headers) or {}).get("data") or {}
    rows = ((data.get("list") or [{}])[0]).get("newestEmailList") or []
    return rows


def delete_by_id(session: requests.Session, headers: dict, id_: str) -> dict:
    resp = session.post(DELETE_URL, headers=headers, json={"idList": [id_]}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def load_manifest() -> list:
    with open(MANIFEST, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_manifest(rows: list) -> None:
    with open(MANIFEST, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def do_test(session: requests.Session, headers: dict) -> int:
    rows = [r for r in load_manifest() if r["目标文件夹"] == "内部"]
    rows.sort(key=lambda r: int(r["大小"]))
    row = rows[0]
    file_path = DEST_ROOT / "内部" / row["上传文件名"]
    log(f"[测试] 上传最小PDF: {file_path.name} ({int(row['大小'])} bytes)")
    result = upload_one(session, headers, file_path, "内部")
    code = result.get("code")
    log(f"[测试] 上传返回: code={code} msg={result.get('msg')} data={json.dumps(result.get('data'), ensure_ascii=False)[:200]}")
    if code != "00000":
        log("[测试] 上传失败，终止")
        return 1

    # 列表验证：标题应为解码后的文件名
    time.sleep(2)
    files = all_files(session, headers)
    log(f"[测试] 全量文件列表共 {len(files)} 条, 其中 companyShortName=内部 的 {sum(1 for f in files if f.get('companyShortName') == '内部')} 条")
    found = None
    for it in files:
        if it.get("companyShortName") == "内部" and (it.get("emailTitle") or "") == file_path.stem:
            found = it
            break
    if not found:
        log("[测试] 未能在文件列表中匹配到测试文件（emailTitle 应为解码后的文件名去扩展名）")
        return 1
    log(f"[测试] 找到测试文件: id={found['id']} emailTitle={found.get('emailTitle')} subjectFormat={found.get('subjectFormat')!r} createDate={found.get('createDate')}")
    # 删除测试文件
    del_result = delete_by_id(session, headers, found["id"])
    log(f"[测试] 删除测试文件 id={found['id']}: code={del_result.get('code')}")
    if del_result.get("code") == "00000":
        time.sleep(2)
        remain = [it for it in all_files(session, headers) if it.get("id") == found["id"]]
        log("[测试] 删除后复核: " + ("已消失" if not remain else f"仍存在 {remain}"))
        return 0 if not remain else 1
    return 1


def do_batch(session: requests.Session, headers: dict) -> int:
    rows = load_manifest()
    done = sum(1 for r in rows if r["上传状态"] == "成功")
    log(f"[批量] 开始: 共 {len(rows)} 个, 已成功 {done}, 待上传 {len(rows) - done}")
    ok = fail = 0
    for i, row in enumerate(rows, 1):
        if row["上传状态"] == "成功":
            continue
        target = row["目标文件夹"]
        file_path = DEST_ROOT / target / row["上传文件名"]
        if not file_path.is_file():
            log(f"[批量] {i}/{len(rows)} [缺文件] {file_path}")
            row["上传状态"] = "缺文件"
            fail += 1
            continue
        success = False
        for attempt in range(1, RETRY + 1):
            try:
                result = upload_one(session, headers, file_path, target)
                if result.get("code") == "00000":
                    success = True
                    break
                log(f"[批量] {i}/{len(rows)} 第{attempt}次失败 code={result.get('code')} msg={result.get('msg')} {row['上传文件名']}")
                if result.get("code") in ("A0230", "401"):  # token 失效则终止
                    log("[批量] 登录凭证失效，请重新获取 token 后再跑")
                    return 1
            except Exception as e:  # noqa: BLE001
                log(f"[批量] {i}/{len(rows)} 第{attempt}次异常: {e} {row['上传文件名']}")
            time.sleep(3 * attempt)
        if success:
            row["上传状态"] = "成功"
            ok += 1
            log(f"[批量] {i}/{len(rows)} 成功 {target}/{row['上传文件名']}")
        else:
            row["上传状态"] = "失败"
            fail += 1
            log(f"[批量] {i}/{len(rows)} 最终失败 {target}/{row['上传文件名']}")
        save_manifest(rows)
        time.sleep(1)
    log(f"[批量] 结束: 本次成功 {ok}, 失败 {fail}")
    remain = [r for r in rows if r["上传状态"] != "成功"]
    if remain:
        log("[批量] 未完成清单:")
        for r in remain:
            log(f"    {r['目标文件夹']}/{r['上传文件名']} 状态={r['上传状态']}")
    return 0 if fail == 0 else 1


def do_check(session: requests.Session, headers: dict) -> int:
    resp = session.post(COMPANIES_URL, headers=headers, json={}, timeout=60)
    companies = resp.json()
    log(f"公司列表: {json.dumps(companies, ensure_ascii=False)[:500]}")
    files = all_files(session, headers)
    from collections import Counter

    dist = Counter(f.get("companyShortName") for f in files)
    log(f"全量文件 {len(files)} 条, 按公司分布: {dict(dist)}")
    for company in ("内部", "外部"):
        items = [f for f in files if f.get("companyShortName") == company]
        subs = Counter(f.get("subjectFormat") or "(根目录)" for f in items)
        log(f"{company}: {len(items)} 条, 主题分布: {dict(subs)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true")
    group.add_argument("--batch", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()

    headers = load_auth()
    session = requests.Session()
    # 内网自签名证书兼容
    session.verify = False
    import urllib3

    urllib3.disable_warnings()

    if args.test:
        return do_test(session, headers)
    if args.batch:
        return do_batch(session, headers)
    return do_check(session, headers)


if __name__ == "__main__":
    sys.exit(main())
