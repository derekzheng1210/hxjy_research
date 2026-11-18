# -*- coding: utf-8 -*-
"""固收部+资产配置部新报告 -> 公司知识库(FIM问答知识库) 增量同步，一键执行。

自动完成：IAM 自动登录（免浏览器，凭证读 .env）→ 识别本地内部知识库新增报告
（按 report_id / SHA256 / 服务器同名 三重去重）→ 按既有规则分桶上传 → 核对清理。

既有规则（与历史上传一致）：
  固收中心: internal/research_visit -> 内部；external/roadshow -> 外部
  资产配置部: 全部 -> 资配
  文件夹 = 公司/{年}年{月}月；单桶满 200MB 自动续 (下)/(续N)
  单文件 >15MB（网关限制）自动转 PDF（优先本地知识库 PDF 缓存，其次 LibreOffice，
  仍超限则 PDF 重写/光栅化）

用法：
  python scripts/sync_fim_new_reports.py              # 同步（默认）
  python scripts/sync_fim_new_reports.py --dry-run    # 只看将上传什么，不实际上传
  python scripts/sync_fim_new_reports.py --check-only # 只核对服务器现状与注册表

状态记录：追加到 D:\\固收部报告上传\\manifest.csv 与 D:\\资配部报告上传\\manifest.csv，
日志在 D:\\固收部报告上传\\sync_log.txt。
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
import urllib3
from cryptography.hazmat.decrepit.ciphers.modes import CFB
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

urllib3.disable_warnings()

REPO = Path(r"D:\hxjy_research")
sys.path.insert(0, str(REPO / "scripts"))
from shrink_for_fim import SAFE_LIMIT, cached_pdf, convert_pdf, shrink_pdf  # noqa: E402
from rasterize_for_fim import rasterize  # noqa: E402

IAM = "https://iam.hxjyam.com"
BASE = f"{IAM}/fim-api/fixed-income-biz"
UPLOAD_URL = f"{BASE}/emailInfo/uploadFile"
DELETE_URL = f"{BASE}/emailResearch/deleteEmailFlie"
KB_DB = Path(r"D:\juyuan_credit_data\internal_knowledge_base\knowledge_base.db")
KB_UPLOADS = Path(r"D:\juyuan_credit_data\internal_knowledge_base")
REGISTRIES = {  # 注册表键 -> manifest 路径
    "固收": Path(r"D:\固收部报告上传\manifest.csv"),
    "资配": Path(r"D:\资配部报告上传\manifest.csv"),
}
COMPANY_DIRS = {"内部": Path(r"D:\固收部报告上传\内部"), "外部": Path(r"D:\固收部报告上传\外部"),
                "资配": Path(r"D:\资配部报告上传\资配")}
MANIFEST_KEY_OF_COMPANY = {"内部": "固收", "外部": "固收", "资配": "资配"}
LOG_PATH = Path(r"D:\固收部报告上传\sync_log.txt")
BUCKET_LIMIT = 200 * 1048576
INTERNAL_TYPES = {"internal", "research_visit"}
EXTERNAL_TYPES = {"external", "roadshow"}
ILLEGAL = set('\\/:*?"<>|\r\n\t')
AES_KEY = b"ruijiancloudbase"
FALLBACK_USERID = "1853728989514489857"  # zhenghongbin；其余用户动态获取


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_env() -> dict:
    env = {}
    for line in open(REPO / ".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def _encrypt(plain: str) -> str:
    c = Cipher(algorithms.AES(AES_KEY), CFB(AES_KEY)).encryptor()
    return base64.b64encode(c.update(plain.encode()) + c.finalize()).decode()


def fim_login(env: dict) -> tuple[requests.Session, dict]:
    """IAM 密码登录 -> authCode -> portal_token。返回 (session, {token, userName, userId})。"""
    user = env.get("IAM_USERNAME", "")
    pwd = env.get("IAM_PASSWORD", "")
    if not user or not pwd:
        raise RuntimeError(".env 缺少 IAM_USERNAME / IAM_PASSWORD")
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    r = s.post(f"{IAM}/api/auth/token/form",
               params={"username": user, "randomStr": "blockPitrix", "code": "1",
                       "grant_type": "password", "scope": "server"},
               data={"password": _encrypt(pwd)},
               headers={"Authorization": "Basic " + base64.b64encode(b"rui:rui").decode(),
                        "Content-Type": "application/x-www-form-urlencoded"},
               timeout=30)
    r.raise_for_status()
    if not r.json().get("success"):
        raise RuntimeError("IAM 密码登录失败: " + r.text[:200])
    r = s.get(f"{IAM}/api/auth/oauth2/authorize?scope=server&client_id=portal&response_type=code"
              f"&redirect_uri=https%3A%2F%2Fiam.hxjyam.com%2FssoAuthredirect&state=%2F",
              allow_redirects=True, timeout=30)
    m = re.search(r"code=([^&]+)", r.url)
    if not m:
        raise RuntimeError("authorize 未返回 code，登录链路异常")
    r2 = s.post(f"{IAM}/api/auth/oauth2/token",
                headers={"Authorization": "Basic " + base64.b64encode(b"portal:portal").decode(),
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"code": m.group(1), "redirect_uri": f"{IAM}/ssoAuthredirect",
                      "grant_type": "authorization_code", "scope": "server"},
                timeout=30)
    j = r2.json()
    token = ((j.get("data") or {}).get("accessToken") or {}).get("tokenValue")
    if not token:
        raise RuntimeError("authCode 换 token 失败: " + json.dumps(j, ensure_ascii=False)[:200])
    # 动态取 userId（失败回退已知值）
    user_id = ""
    try:
        info = s.get(f"{IAM}/api/admin/user/info", headers={"Authorization": f"Bearer {token}"},
                     timeout=30).json()
        d = info.get("data") or info
        user_id = str(d.get("id") or d.get("userId") or "")
    except Exception:  # noqa: BLE001
        pass
    if not user_id and user == "zhenghongbin":
        user_id = FALLBACK_USERID
    return s, {"token": token, "userName": user, "userId": user_id}


def auth_headers(cred: dict) -> dict:
    return {"Authorization": f"Bearer {cred['token']}", "userName": cred["userName"],
            "userId": cred["userId"]}


def all_files(session: requests.Session, headers: dict) -> list:
    r = session.post(f"{BASE}/emailResearch/getEmailList", headers=headers,
                     json={"type": "", "pageNo": 1, "pageSize": 10}, timeout=120)
    r.raise_for_status()
    data = (r.json().get("data") or {}).get("list") or [{}]
    return data[0].get("newestEmailList") or []


def delete_ids(session, headers, ids: list) -> bool:
    for k in range(0, len(ids), 10):
        r = session.post(DELETE_URL, headers=headers, json={"idList": ids[k:k + 10]}, timeout=60)
        if r.json().get("code") != "00000":
            log(f"[删除] 失败 {ids[k:k + 10]}: {r.text[:150]}")
            return False
    return True


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


def encode_name(name: str) -> str:
    return quote(name, safe="-_.!~*'()")


def month_key_of_bucket(bucket: str) -> str:
    m = re.match(r"(\d{4}年\d{1,2}月)", bucket.split("/")[-1])
    return m.group(1) if m else ""


def next_bucket_name(company: str, month: str, used: set[str]) -> str:
    base = f"{company}/{month}"
    for cand in [base, f"{base}(下)", *[f"{base}(续{n})" for n in range(2, 21)]]:
        if cand not in used:
            return cand
    raise RuntimeError(f"桶名耗尽: {base}")


def target_company(rtype: str, org: str) -> str | None:
    if org == "资产配置部":
        return "资配"
    if org == "固收中心":
        if rtype in INTERNAL_TYPES:
            return "内部"
        if rtype in EXTERNAL_TYPES:
            return "外部"
    return None


class Registry:
    """两个 manifest 合成的上传注册表 + 分桶尺寸。"""

    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.shas: dict[str, set[str]] = defaultdict(set)   # 公司 -> sha 集合
        self.bucket_sizes: dict[str, int] = defaultdict(int)  # 完整桶路径 -> 字节
        self.month_buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.rows: dict[str, list[dict]] = {}
        self.fieldnames: dict[str, list[str]] = {}
        for key, mpath in REGISTRIES.items():
            if not mpath.is_file():
                self.rows[key], self.fieldnames[key] = [], []
                continue
            with open(mpath, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.rows[key] = rows
            self.fieldnames[key] = list(rows[0].keys()) if rows else []
            for r in rows:
                target = r.get("目标文件夹") or ""
                company = target.split("/")[0] if "/" in target else ""
                ok = r.get("上传状态") in ("", "成功")
                if ok:
                    self.ids.add(r["report_id"])
                    if r.get("SHA256") and company:
                        self.shas[company].add(r["SHA256"])
                    size = int(r["大小"]) if (r.get("大小") or "").isdigit() else 0
                    if target and "/" in target:
                        self.bucket_sizes[target] += size
                        month = month_key_of_bucket(target)
                        if month and target not in self.month_buckets[(company, month)]:
                            self.month_buckets[(company, month)].append(target)

    def append(self, key: str, row: dict) -> None:
        fields = self.fieldnames[key]
        if not fields:
            fields = list(row.keys())
            self.fieldnames[key] = fields
        self.rows[key].append({k: row.get(k, "") for k in fields})

    def save(self) -> None:
        for key, mpath in REGISTRIES.items():
            rows = self.rows[key]
            if not rows:
                continue
            with open(mpath, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=self.fieldnames[key])
                w.writeheader()
                w.writerows(rows)


def fetch_new_reports(reg: Registry) -> tuple[list[dict], list[str]]:
    conn = sqlite3.connect(f"file:{KB_DB.as_posix()}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, payload FROM reports WHERE deleted_at IS NULL").fetchall()
    conn.close()
    new, notes = [], []
    for rid, payload in rows:
        if rid in reg.ids:
            continue
        p = json.loads(payload)
        org = p.get("org") or ""
        if org not in ("固收中心", "资产配置部"):
            continue
        company = target_company(p.get("reportType") or "", org)
        if company is None:
            notes.append(f"[跳过-未知类型] {rid} org={org} type={p.get('reportType')} {p.get('title')}")
            continue
        src = KB_UPLOADS / (p.get("fileUrl") or "").replace(chr(92), "/")
        if not src.is_file():
            m = list((KB_UPLOADS / "uploads").glob(f"{rid}__*"))
            src = m[0] if m else None
        if not src:
            notes.append(f"[跳过-缺文件] {rid} {p.get('title')}")
            continue
        date = p.get("reportDate") or datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d")
        new.append({"rid": rid, "title": (p.get("title") or "").strip() or src.stem,
                    "date": date, "rtype": p.get("reportType") or "", "company": company,
                    "src": src, "size": src.stat().st_size,
                    "sha": p.get("fileSha256") or sha256_of(src)})
    return new, notes


def assign_buckets(items: list[dict], reg: Registry, server_titles: set) -> list[dict]:
    """按 (公司, 月) 分桶：已存在的月桶未满继续用，满了自动续 (下)/(续N)。"""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for it in items:
        d = it["date"]
        groups[(it["company"], f"{d[:4]}年{int(d[5:7])}月")].append(it)
    results = []
    for (company, month), group in sorted(groups.items()):
        group.sort(key=lambda i: (i["date"], i["title"]))
        buckets = list(reg.month_buckets[(company, month)])
        sizes = {b: reg.bucket_sizes.get(b, 0) for b in buckets}
        for it in group:
            if it["size"] > BUCKET_LIMIT:
                it["skip"] = "单文件超过200MB上限"
                results.append(it)
                continue
            target = next((b for b in buckets if sizes.get(b, 0) + it["size"] <= BUCKET_LIMIT), None)
            if target is None:
                target = next_bucket_name(company, month, set(buckets))
                buckets.append(target)
                sizes[target] = 0
                reg.month_buckets[(company, month)] = buckets
            sizes[target] += it["size"]
            it["目标文件夹"] = target
            stem = sanitize(it["title"])
            if (company, stem) in server_titles:
                it["skip"] = "服务器已有同名文件(同名不同内容，请人工判断)"
            results.append(it)
    return results


def convert_oversize(path: Path, sha: str) -> tuple[Path | None, str]:
    """把 >15MB 的文件压到限内。优先级：本地PDF缓存 -> LibreOffice转PDF -> PDF重写 -> 渐进光栅化。"""
    cand = cached_pdf(path, sha)
    if cand and cand.stat().st_size < SAFE_LIMIT:
        return cand, "复用本地PDF缓存"
    work = path.parent / "_shrink_work"
    work.mkdir(exist_ok=True)
    base_pdf = None
    if path.suffix.lower() in (".pptx", ".docx"):
        try:
            base_pdf = convert_pdf(path, work / path.name)
            if base_pdf.stat().st_size < SAFE_LIMIT:
                return base_pdf, "LibreOffice转PDF"
        except Exception as e:  # noqa: BLE001
            log(f"    [LibreOffice转PDF失败] {e}")
    elif path.suffix.lower() == ".pdf":
        base_pdf = path
    else:
        return None, ""
    # PDF 重写（降采样图片重压缩）
    try:
        out = shrink_pdf(base_pdf, work / f"{path.stem}_rw.pdf")
        if out.stat().st_size < SAFE_LIMIT:
            return out, "PDF重写"
    except Exception as e:  # noqa: BLE001
        log(f"    [PDF重写失败] {e}")
    # 渐进光栅化（外观不变，逐档加压）
    if base_pdf is not None:
        for dpi, q in ((130, 60), (100, 50), (90, 42)):
            try:
                out = rasterize(base_pdf, work / f"{path.stem}_r{dpi}.pdf", dpi, q)
            except Exception as e:  # noqa: BLE001
                log(f"    [光栅化{dpi}dpi失败] {e}")
                continue
            if out.stat().st_size < SAFE_LIMIT:
                return out, f"光栅化{dpi}dpi"
            log(f"    [光栅化{dpi}dpi仍超限 {out.stat().st_size / 1048576:.1f}MB]")
    return None, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="固收/资配新报告增量同步到公司知识库")
    parser.add_argument("--dry-run", action="store_true", help="只显示将上传的内容")
    parser.add_argument("--check-only", action="store_true", help="只核对服务器与注册表现状")
    args = parser.parse_args()

    reg = Registry()
    log(f"注册表: 已上传 {len(reg.ids)} 篇, 分桶 {sum(1 for b in reg.bucket_sizes if reg.bucket_sizes[b] > 0)} 个")

    session, cred = fim_login(load_env())
    headers = auth_headers(cred)
    log(f"自动登录成功: {cred['userName']}")

    files = all_files(session, headers)
    server_titles = {(f.get("companyShortName"), f.get("emailTitle")) for f in files}
    if args.check_only:
        dist = defaultdict(int)
        for f in files:
            if f.get("companyShortName") in ("内部", "外部", "资配"):
                dist[f.get("companyShortName")] += 1
        log("[核对] 服务器现状: " + ", ".join(f"{k} {v} 篇" for k, v in sorted(dist.items())) +
            f"（注册表 {len(reg.ids)} 篇）")
        return 0

    new, notes = fetch_new_reports(reg)
    for n in notes:
        log(n)
    if not new:
        log("没有发现新报告，无需同步")
        return 0

    assigned = assign_buckets(new, reg, server_titles)
    todo = [it for it in assigned if not it.get("skip")]
    for it in assigned:
        if it.get("skip"):
            log(f"[跳过] {it.get('目标文件夹', it['company'])}/{sanitize(it['title'])}: {it['skip']}")
    log(f"新报告 {len(new)} 篇: 待上传 {len(todo)}, 跳过 {len(assigned) - len(todo)}")
    if args.dry_run:
        for it in todo:
            log(f"  [将上传] {it['目标文件夹']}/{sanitize(it['title'])}{it['src'].suffix.lower()}"
                f" ({it['size'] / 1048576:.1f}MB, {it['date']})")
        log(f"合计 {sum(i['size'] for i in todo) / 1048576:.0f}MB（dry-run，未实际上传）")
        return 0

    # 阶段1：复制改名到 桶子目录（本地镜像服务器结构）+ 超限转PDF
    staged = []
    for it in todo:
        bucket_dir = COMPANY_DIRS[it["company"]] / it["目标文件夹"].split("/", 1)[1]
        bucket_dir.mkdir(parents=True, exist_ok=True)
        stem = sanitize(it["title"])
        name = unique_name(bucket_dir, stem, it["src"].suffix.lower())
        dst = bucket_dir / name
        shutil.copy2(it["src"], dst)
        converted = ""
        if dst.stat().st_size >= SAFE_LIMIT:
            fixed, label = convert_oversize(dst, it["sha"])
            if fixed:
                if dst.suffix.lower() == ".pdf":
                    final = dst  # PDF 源：转换结果原位覆盖，避免同名(2)后缀
                else:
                    final = bucket_dir / (dst.stem + ".pdf")
                    if final.exists():
                        final = bucket_dir / unique_name(bucket_dir, dst.stem, ".pdf")
                shutil.copy2(fixed, final)
                # 清理 _shrink_work 中间产物（本地PDF缓存不能删，跳过）
                if fixed.parent.name == "_shrink_work":
                    fixed.unlink(missing_ok=True)
                dst = final
                converted = label
                log(f"[转PDF] {name} ({it['size'] / 1048576:.1f}MB -> {dst.stat().st_size / 1048576:.1f}MB, {label})")
            else:
                log(f"[警告] 无法压到 15MB 内，跳过: {name}")
                dst.unlink(missing_ok=True)
                continue
        it.update({"上传文件名": dst.name, "本地路径": str(dst), "最终大小": dst.stat().st_size,
                   "最终sha": sha256_of(dst), "转换": converted})
        staged.append(it)

    # 阶段2：上传（逐个重试3次，成功/失败即写回注册表）
    ok = fail = 0
    for i, it in enumerate(staged, 1):
        path = Path(it["本地路径"])
        key = MANIFEST_KEY_OF_COMPANY[it["company"]]
        base_row = {"report_id": it["rid"], "标题": it["title"], "上传文件名": path.name,
                    "目标文件夹": it["目标文件夹"], "报告类型": it["rtype"], "二级分类": "",
                    "报告日期": it["date"], "大小": it["最终大小"], "SHA256": it["最终sha"],
                    "源路径": str(it["src"]), "转换": it.get("转换", "")}
        success = False
        for attempt in range(1, 4):
            try:
                data = {"dir": it["目标文件夹"]}
                with open(path, "rb") as f:
                    resp = session.post(UPLOAD_URL, headers=headers, data=data,
                                        files={"file": (encode_name(path.name), f, "application/octet-stream")},
                                        timeout=900)
                result = resp.json()
                if result.get("code") == "00000":
                    success = True
                    break
                log(f"[上传] {i}/{len(staged)} 第{attempt}次 code={result.get('code')} {path.name}")
            except Exception as e:  # noqa: BLE001
                log(f"[上传] {i}/{len(staged)} 第{attempt}次异常: {e} {path.name}")
            time.sleep(3 * attempt)
        if success:
            ok += 1
            base_row["上传状态"] = "成功"
            reg.append(key, base_row)
            reg.ids.add(it["rid"])
            reg.shas[it["company"]].add(it["最终sha"])
            reg.bucket_sizes[it["目标文件夹"]] += it["最终大小"]
            if i % 10 == 0 or i == len(staged):
                log(f"[上传] 进度 {i}/{len(staged)} (累计成功 {ok})")
        else:
            fail += 1
            base_row["上传状态"] = "失败"
            reg.append(key, base_row)
            log(f"[上传] {i}/{len(staged)} 最终失败 {it['目标文件夹']}/{path.name}")
        reg.save()
        time.sleep(1)

    # 阶段3：核对本轮（同名>1清孤儿留最新，=0记失败）
    time.sleep(2)
    files = all_files(session, headers)
    by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for f in files:
        comp = f.get("companyShortName")
        if comp in ("内部", "外部", "资配"):
            by_key[(comp, f"{comp}/" + (f.get("subjectFormat") or "(根目录)"), f.get("emailTitle"))].append(f["id"])
    problems = 0
    for it in staged:
        stem = sanitize(it["title"])
        ids = by_key.get((it["company"], it["目标文件夹"], stem), [])
        if len(ids) > 1:
            keep = max(ids)
            orphans = sorted(x for x in ids if x != keep)
            log(f"[核对] 同名 {len(ids)} 条，清理孤儿 {orphans}（保留 {keep}）: {it['目标文件夹']}/{stem}")
            delete_ids(session, headers, orphans)
            problems += 1
        elif len(ids) == 0:
            log(f"[核对] 服务器缺失: {it['目标文件夹']}/{stem}")
            problems += 1
    by_company = defaultdict(int)
    for it in staged:
        by_company[it["company"]] += 1
    log("[总结] 本次新增: " + (", ".join(f"{k} {v} 篇" for k, v in sorted(by_company.items())) or "无") +
        f"; 跳过 {len(assigned) - len(todo)}; 失败 {fail}; 核对异常 {problems}")
    return 0 if fail == 0 and problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
