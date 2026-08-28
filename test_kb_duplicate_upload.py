# -*- coding: utf-8 -*-
"""知识库上传内容级查重（SHA-256）测试。

使用 Flask test client 与独立临时数据目录，不触碰任何正式/测试数据。
运行：D:/Python312/python.exe test_kb_duplicate_upload.py
"""
import json
import os
import tempfile
import uuid

DATA_ROOT = tempfile.mkdtemp(prefix="kb_dup_test_")
os.environ["PORTAL_DATA_ROOT"] = DATA_ROOT

import app as portal  # noqa: E402
from internal_knowledge_base import routes  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

passed, failed = [], []


def check(name, condition, detail=""):
    if condition:
        passed.append(name)
        print(f"[PASS] {name}")
    else:
        failed.append(name)
        print(f"[FAIL] {name} {detail}")


def make_client():
    portal.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    client = portal.app.test_client()

    user = {
        "id": f"user-{uuid.uuid4().hex[:8]}",
        "name": "查重测试用户",
        "org": "固收中心",
        "role": "member",
        "password_hash": generate_password_hash("pw123456"),
        "createdAt": "",
        "updatedAt": "",
    }
    routes.store.add_user(user)
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["internal_knowledge_base_user_id"] = user["id"]
        sess["internal_knowledge_base_csrf"] = "test-csrf-token"
    return client


def upload(client, content: bytes, filename: str):
    meta = {
        "reportType": "internal",
        "category": "other",
        "theme": "credit",
        "org": "固收中心",
        "reportDate": "2026-08-28",
        "summary": "",
        "recommendation": "",
        "tags": "",
        "titles": {},
    }
    data = {
        "file": (io.BytesIO(content), filename),
        "meta": json.dumps(meta),
    }
    return client.post(
        "/internal-knowledge-base/api/reports",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": "test-csrf-token"},
    )


import io  # noqa: E402


def main():
    client = make_client()

    content_a = b"KB duplicate detection test content A " + os.urandom(32)
    content_a2 = b"KB duplicate detection test content A " + os.urandom(32)  # 同名不同内容
    pdf_header = b"%PDF-1.4\n"

    # 1. 首次上传成功
    resp = upload(client, pdf_header + content_a, "测试报告A.pdf")
    check("首次上传返回 200", resp.status_code == 200, f"got {resp.status_code}: {resp.data[:200]}")
    body = resp.get_json()
    check("首次上传返回报告", bool(body.get("reports")))

    # 2. 相同内容 + 相同文件名 → 拦截
    resp = upload(client, pdf_header + content_a, "测试报告A.pdf")
    check("相同内容相同文件名被拦截(400)", resp.status_code == 400)
    msg = (resp.get_json() or {}).get("error", "")
    check("拦截信息包含原报告标题", "测试报告A" in msg, msg)

    # 3. 相同内容 + 不同文件名 → 依然拦截（内容级查重，不只看文件名）
    resp = upload(client, pdf_header + content_a, "换个名字的相同文件.pdf")
    check("相同内容不同文件名被拦截(400)", resp.status_code == 400)
    msg = (resp.get_json() or {}).get("error", "")
    check("拦截信息说明内容相同", "完全相同" in msg, msg)

    # 4. 不同内容 + 相同文件名 → 放行
    resp = upload(client, pdf_header + content_a2, "测试报告A.pdf")
    check("不同内容相同文件名放行(200)", resp.status_code == 200,
          f"got {resp.status_code}: {resp.data[:200]}")

    # 5. 新上传的报告已存哈希（后续比对走快路径）
    reports = routes.store.reports()
    with_hash = [r for r in reports if r.get("fileSha256")]
    check("报告记录包含 fileSha256", len(with_hash) == 2, f"{len(with_hash)}/2")

    # 6. 历史报告（无哈希）懒回填路径：手工删掉哈希后再次上传同内容仍能拦截
    first = next(r for r in reports if r.get("fileName") == "测试报告A.pdf" and "换个" not in r.get("fileName", ""))
    routes.store.update_report(first["id"], {"fileSha256": ""})
    resp = upload(client, pdf_header + content_a, "再次尝试上传旧内容.pdf")
    check("历史报告懒回填后仍拦截(400)", resp.status_code == 400)
    backfilled = routes.store.get_report(first["id"], include_deleted=True).get("fileSha256")
    check("历史报告哈希已回填", bool(backfilled))

    print(f"\n通过 {len(passed)} / {len(passed) + len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
