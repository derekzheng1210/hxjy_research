# -*- coding: utf-8 -*-
"""测试实例 API 冒烟测试：验证四个新功能的接口层行为。

用法（测试实例需已在 5090 端口运行）：
    D:/Python312/python.exe test_new_features.py
"""
import io
import json
import re
import sys
import urllib.request

BASE = "http://127.0.0.1:5090"
KB = BASE + "/internal-knowledge-base"
PASSWORD = "test123456"

passed, failed = [], []


def check(name, condition, detail=""):
    if condition:
        passed.append(name)
        print(f"[PASS] {name}")
    else:
        failed.append(name)
        print(f"[FAIL] {name} {detail}")


class Client:
    """极简 Cookie 会话客户端（http.cookiejar 处理 Set-Cookie）。"""

    def __init__(self):
        import http.cookiejar
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.ProxyHandler({}))

    def get(self, url):
        return self._open(url, None)

    def post_form(self, url, form):
        return self._open(url, urllib.parse.urlencode(form).encode(), method="POST")

    def request(self, url, method, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        return self._open(url, data, method=method, headers=headers or {})

    def _open(self, url, data, method="GET", headers=None):
        req = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            resp = self.opener.open(req, timeout=180)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        raw = resp.read().decode("utf-8")
        return resp.status, raw


import urllib.parse  # noqa: E402


def portal_login(client):
    client.post_form(BASE + "/login", {"password": "test2026"})


def kb_login(client, username):
    # 先 GET 一次页面：建立 KB session 并取得 CSRF token
    client.csrf = csrf_of(client)
    status, raw = client.request(KB + "/api/login", "POST",
                                 {"username": username, "password": PASSWORD},
                                 {"Content-Type": "application/json",
                                  "X-CSRF-Token": client.csrf})
    data = json.loads(raw)
    assert data.get("user"), f"KB login failed: {raw}"
    return data["user"]


def csrf_of(client):
    # 从 KB 页面 meta 标签拿 CSRF token
    status, raw = client.get(KB + "/")
    match = re.search(r'name="csrf-token" content="([^"]+)"', raw)
    return match.group(1) if match else ""


def api(client, path, method="GET", body=None):
    headers = {"X-CSRF-Token": client.csrf, "Content-Type": "application/json"}
    status, raw = client.request(KB + path, method, body, headers)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"_raw": raw[:200]}
    return status, data


def main():
    admin = Client()
    member = Client()
    portal_login(admin)
    portal_login(member)
    admin_user = kb_login(admin, "testadmin")
    member_user = kb_login(member, "testuser")
    check("登录（行政/普通用户）", admin_user["role"] == "admin" and member_user["role"] == "member")

    # ---------- 需求3：路演安排可编辑 ----------
    status, data = api(admin, "/api/roadshow-schedule")
    week_items = data.get("items", [])
    check("路演列表（含 reportIds 字段）", status == 200 and all("reportIds" in it for it in week_items))

    # 新增一条（普通用户创建）
    status, data = api(member, "/api/roadshow-schedule", "POST", {
        "eventTime": "2026-09-15T10:00", "endTime": "2026-09-15T11:00",
        "format": "online", "institution": "兴业证券", "organizer": "",
        "tencentMeetingId": "123-456-789", "meetingRoom": "", "presenter": "测试路演人",
        "topic": "信用债市场展望",
    })
    check("新增路演安排", status == 200 and data.get("ok"), str(data)[:120])
    item_id = data.get("item", {}).get("id", "")

    # 修改（创建人本人）
    status, data = api(member, f"/api/roadshow-schedule/{item_id}", "PUT", {
        "eventTime": "2026-09-15T14:30", "endTime": "2026-09-15T15:30",
        "format": "offline", "institution": "中金公司", "organizer": "",
        "tencentMeetingId": "", "meetingRoom": "9层一会", "presenter": "测试路演人改",
        "topic": "信用债市场展望（修订）",
    })
    updated = data.get("item", {})
    check("修改路演安排（创建人）", status == 200 and updated.get("event_time") == "2026-09-15T14:30"
          and updated.get("meeting_room") == "9层一会", str(data)[:150])

    # 无关第三方（非创建人非行政）不可修改：403
    third = Client()
    portal_login(third)
    kb_login(third, "testuser2")
    status, data = api(third, f"/api/roadshow-schedule/{item_id}", "PUT", {
        "eventTime": "2026-09-15T16:00", "format": "online",
        "tencentMeetingId": "999-999-999", "presenter": "x", "topic": "y"})
    check("无关用户修改被拒（403）", status == 403, f"got {status}")
    third_item_id = item_id  # 供匹配权限测试用
    # 行政可以改任何人的
    status, data = api(admin, f"/api/roadshow-schedule/{item_id}", "PUT", {
        "eventTime": "2026-09-16T09:00", "format": "online",
        "tencentMeetingId": "999-999-999", "presenter": "行政改的路演人", "topic": "行政改的主题"})
    check("行政修改任意路演安排", status == 200, str(data)[:120])

    # 非法字段校验
    status, data = api(admin, f"/api/roadshow-schedule/{item_id}", "PUT", {
        "eventTime": "bad", "format": "online", "tencentMeetingId": "1", "presenter": "a", "topic": "b"})
    check("修改字段校验", status == 400)

    # ---------- 需求2：匹配 ----------
    status, data = api(admin, "/api/roadshow-schedule/options?date=2026-09-16&days=10")
    options = data.get("options", [])
    check("匹配候选项接口", status == 200 and any(o["id"] == item_id for o in options), str(data)[:150])

    # 上传一份路演报告（不带 roadshowScheduleId，触发自动匹配）
    boundary = "----smoketest"
    report_title = "信用债市场展望（行政改的主题）"
    meta = json.dumps({
        "reportType": "roadshow", "category": "", "theme": "credit",
        "org": "固收中心", "reportDate": "2026-09-16", "summary": "冒烟测试摘要",
        "sourceAuthor": "行政改的路演人", "sourceInstitution": "中金公司",
        "tags": "", "titles": {}, "authorId": "",
    })
    file_bytes = b"%PDF-1.4 smoke test"
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"meta\"\r\n\r\n{meta}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{report_title}.pdf\"\r\n"
                 "Content-Type: application/pdf\r\n\r\n".encode() + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
               "X-CSRF-Token": member.csrf}
    req = urllib.request.Request(KB + "/api/reports/roadshow", data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = member.opener.open(req, timeout=180)
        up_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"上传失败 HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}")
    report = (up_data.get("reports") or [{}])[0]
    check("上传路演报告成功", bool(report.get("id")), str(up_data)[:150])
    check("自动匹配（规则：日期+路演人+机构全命中）",
          report.get("roadshowScheduleId") == item_id and report.get("roadshowMatchedBy") == "rule",
          f"got schedule={report.get('roadshowScheduleId')!r} by={report.get('roadshowMatchedBy')!r}")
    report_id = report.get("id", "")

    # 报告列表附带 roadshowSchedule 摘要
    status, data = api(member, "/api/reports")
    matched = next((r for r in data["reports"] if r["id"] == report_id), {})
    check("报告列表附带关联路演摘要", matched.get("roadshowSchedule", {}).get("id") == item_id)

    # 手工匹配：换绑到另一条路演（先建第二条）
    status, data = api(admin, "/api/roadshow-schedule", "POST", {
        "eventTime": "2026-09-18T10:00", "format": "offline", "institution": "中信证券",
        "meetingRoom": "8层二会", "presenter": "另一个路演人", "topic": "可转债策略"})
    item2_id = data.get("item", {}).get("id", "")
    status, data = api(member, "/api/roadshow-schedule/match", "POST",
                       {"reportId": report_id, "scheduleId": item2_id})
    check("手工匹配（报告作者本人）", status == 200 and data["report"]["roadshowScheduleId"] == item2_id, str(data)[:150])

    # 无关第三方（非行政、非作者/上传人、非安排创建人）不可匹配：403
    status, data = api(third, "/api/roadshow-schedule/match", "POST",
                       {"reportId": report_id, "scheduleId": item2_id})
    check("无关第三方匹配被拒（403）", status == 403, f"got {status}")
    # member 是上传人+作者（相关本人），应成功换回 item1
    status, data = api(member, "/api/roadshow-schedule/match", "POST",
                       {"reportId": report_id, "scheduleId": item_id})
    check("相关本人换绑匹配", status == 200 and data["report"]["roadshowScheduleId"] == item_id)

    # 取消关联
    status, data = api(member, "/api/roadshow-schedule/match", "POST",
                       {"reportId": report_id, "scheduleId": ""})
    check("取消关联", status == 200 and data["report"]["roadshowScheduleId"] == "")

    # 重新关联回 item2，供前端测试
    status, data = api(admin, "/api/roadshow-schedule/match", "POST",
                       {"reportId": report_id, "scheduleId": item2_id})
    check("行政匹配任意报告", status == 200)

    # 非路演报告匹配被拒
    status, data = api(admin, "/api/roadshow-schedule/match", "POST",
                       {"reportId": "report-upload-nonexist", "scheduleId": item2_id})
    check("匹配不存在的报告返回 404", status == 404)

    # ---------- 需求4：LLM 链路（走自部署 glm-5.2） ----------
    status, data = api(member, "/api/roadshow-schedule/ai-parse", "POST", {
        "text": "🔥策略陈果路演！主题：四季度市场风格展望，时间：9月25日13:30，地点：9层一会",
        "weekStart": "2026-09-21"})
    parsed_ok = status == 200 and data.get("eventTime") == "2026-09-25T13:30"
    check("LLM 路演文本识别（自部署模型，JSON 输出无围栏问题）", parsed_ok, str(data)[:200])

    # ---------- 清理：删除测试数据 ----------
    status, data = api(member, "/api/reports/%s" % report_id, "DELETE")
    check("删除测试报告", status == 200)
    status, data = api(admin, f"/api/roadshow-schedule/{item_id}", "DELETE")
    status2, data2 = api(admin, f"/api/roadshow-schedule/{item2_id}", "DELETE")
    check("删除测试路演安排", status == 200 and status2 == 200)

    print("\n========== 汇总 ==========")
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：", failed)
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    main()
