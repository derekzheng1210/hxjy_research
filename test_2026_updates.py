# -*- coding: utf-8 -*-
"""2026-08 功能更新回归测试：
路演安排表 / 月报打分关闭 / 外部报告上传人 / 内部报告点赞展示。
"""
import io
import json
import os
import re
import unittest
import uuid
from pathlib import Path

from werkzeug.security import generate_password_hash

TEST_DATA_ROOT = Path(__file__).resolve().parent / ".test_runtime" / uuid.uuid4().hex
os.environ["PORTAL_DATA_ROOT"] = str(TEST_DATA_ROOT)
os.environ["SECRET_KEY"] = "internal-kb-test-secret"
os.environ["SITE_PASSWORD"] = "portal-test-password"

from app import app  # noqa: E402
from internal_knowledge_base.routes import store, public_report  # noqa: E402


class Updates2026Test(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        with store.transaction() as conn:
            for table in ("audit_log", "pdf_cache", "engagement", "ratings", "reports",
                          "roadshow_schedule", "qa_usage", "qa_history", "users"):
                conn.execute(f"DELETE FROM {table}")
        store.add_user({
            "id": "member", "name": "测试成员", "org": "固收中心", "role": "member",
            "password_hash": generate_password_hash("member-password"),
        })
        self.client = app.test_client()

    def portal_session(self):
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def csrf(self):
        self.portal_session()
        response = self.client.get("/internal-knowledge-base/")
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login_member(self):
        token = self.csrf()
        response = self.client.post("/internal-knowledge-base/api/login", json={
            "username": "member", "password": "member-password",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        return token

    def add_report(self, report_id, category="deep", **extra):
        report = {
            "id": report_id, "title": f"报告-{report_id}", "author": "测试成员",
            "authorId": "member", "org": "固收中心", "category": category,
            "theme": "credit", "reportType": "internal", "reportDate": "2026-08-01",
            "uploadedAt": "2026-08-01T09:00:00+08:00", "summary": "", "recommendation": "",
            "tags": [], "fileName": "a.pdf", "fileUrl": "uploads/a.pdf", "fileType": "PDF",
            "fileSize": "1 KB", "fileStored": False, "preset": False,
            "scoringOrgs": ["固收中心"],
        }
        report.update(extra)
        store.add_report(report)
        return report

    def test_monthly_reports_no_longer_scored_and_history_deleted(self):
        """月报打分关闭：月报不可评分；历史月报评分被彻底删除。"""
        self.add_report("r-monthly", category="monthly")
        self.add_report("r-deep", category="deep")
        store.add_rating({
            "id": "rating-1", "reportId": "r-monthly", "userId": "member",
            "inspiration": 8, "depth": 8, "utility": 8, "comment": "",
            "updatedAt": "2026-08-01T10:00:00+08:00",
        })
        with store.transaction() as conn:
            deleted = store._delete_monthly_ratings(conn)
        self.assertEqual(deleted, 1)
        self.assertEqual(store.ratings(), [])

        token = self.login_member()
        monthly = self.client.post("/internal-knowledge-base/api/ratings", json={
            "reportId": "r-monthly", "inspiration": 5, "depth": 5, "utility": 5,
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(monthly.status_code, 400)
        deep = self.client.post("/internal-knowledge-base/api/ratings", json={
            "reportId": "r-deep", "inspiration": 5, "depth": 5, "utility": 5,
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(deep.status_code, 200)

    def test_upload_records_actual_uploader(self):
        """上传人记录实际操作者并通过列表 API 下发。"""
        self.add_report("r-ext", reportType="external",
                        uploadedById="member", uploadedByName="测试成员",
                        sourceAuthor="外部作者", sourceInstitution="外部机构")
        self.login_member()
        listing = self.client.get("/internal-knowledge-base/api/reports")
        items = {item["id"]: item for item in listing.get_json()["reports"]}
        self.assertEqual(items["r-ext"]["uploadedByName"], "测试成员")
        report = store.get_report("r-ext")
        self.assertEqual(public_report(report)["uploadedById"], "member")

    def test_internal_report_like_rejected_and_external_toggles(self):
        """内部报告禁止点赞；外部报告点赞为 toggle 语义。"""
        self.add_report("r-int", category="weekly")
        self.add_report("r-ext2", reportType="external", sourceAuthor="外部作者",
                        sourceInstitution="外部机构")
        token = self.login_member()
        internal = self.client.post("/internal-knowledge-base/api/reports/r-int/like",
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(internal.status_code, 400)
        liked = self.client.post("/internal-knowledge-base/api/reports/r-ext2/like",
                                 headers={"X-CSRF-Token": token})
        self.assertEqual(liked.get_json()["liked"], True)
        unliked = self.client.post("/internal-knowledge-base/api/reports/r-ext2/like",
                                   headers={"X-CSRF-Token": token})
        self.assertEqual(unliked.get_json()["liked"], False)

    def test_roadshow_schedule_crud_and_permissions(self):
        """路演安排表：所有人可新增，按周读取，本人可删，他人不可删。"""
        token = self.login_member()
        created = self.client.post("/internal-knowledge-base/api/roadshow-schedule", json={
            "eventTime": "2026-08-20T14:30", "format": "hybrid",
            "tencentMeetingId": "659-689-968", "meetingRoom": "9层一会",
            "presenter": "陈果", "topic": "四季度市场风格展望",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 200)
        item_id = created.get_json()["item"]["id"]

        listing = self.client.get(
            "/internal-knowledge-base/api/roadshow-schedule?week=2026-08-19")
        data = listing.get_json()
        self.assertEqual(data["weekStart"], "2026-08-17")
        self.assertEqual(data["weekEnd"], "2026-08-23")
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["formatLabel"], "线上+线下")

        # 校验：线下路演必须填会议室
        bad = self.client.post("/internal-knowledge-base/api/roadshow-schedule", json={
            "eventTime": "2026-08-21T10:00", "format": "offline",
            "presenter": "张三", "topic": "测试",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(bad.status_code, 400)

        # 其他成员不可删除他人创建的安排
        store.add_user({"id": "other", "name": "其他成员", "org": "固收中心",
                        "role": "member",
                        "password_hash": generate_password_hash("other-password")})
        token2 = self.csrf()
        self.client.post("/internal-knowledge-base/api/login", json={
            "username": "other", "password": "other-password",
        }, headers={"X-CSRF-Token": token2})
        forbidden = self.client.delete(
            f"/internal-knowledge-base/api/roadshow-schedule/{item_id}",
            headers={"X-CSRF-Token": token2})
        self.assertEqual(forbidden.status_code, 403)

        # 创建人本人可删除
        token3 = self.csrf()
        self.client.post("/internal-knowledge-base/api/login", json={
            "username": "member", "password": "member-password",
        }, headers={"X-CSRF-Token": token3})
        deleted = self.client.delete(
            f"/internal-knowledge-base/api/roadshow-schedule/{item_id}",
            headers={"X-CSRF-Token": token3})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(store.roadshow_items("2026-08-17", "2026-08-23"), [])

    def test_roadshow_end_time_validation(self):
        """结束时间：可选；填写时必须同一天且晚于开始时间。"""
        token = self.login_member()
        ok = self.client.post("/internal-knowledge-base/api/roadshow-schedule", json={
            "eventTime": "2026-08-20T14:30", "endTime": "2026-08-20T16:00",
            "format": "online", "tencentMeetingId": "111-222-333",
            "presenter": "陈果", "topic": "带结束时间的路演",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["item"]["endTime"], "2026-08-20T16:00")

        earlier = self.client.post("/internal-knowledge-base/api/roadshow-schedule", json={
            "eventTime": "2026-08-20T14:30", "endTime": "2026-08-20T14:00",
            "format": "online", "tencentMeetingId": "111", "presenter": "x", "topic": "y",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(earlier.status_code, 400)

        crossday = self.client.post("/internal-knowledge-base/api/roadshow-schedule", json={
            "eventTime": "2026-08-20T14:30", "endTime": "2026-08-21T14:00",
            "format": "online", "tencentMeetingId": "111", "presenter": "x", "topic": "y",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(crossday.status_code, 400)

    def test_roadshow_upload_link_is_persisted(self):
        """从路演安排一键上传：报告 payload 携带 roadshowScheduleId 与上传人。"""
        token = self.login_member()
        created = self.client.post("/internal-knowledge-base/api/roadshow-schedule", json={
            "eventTime": "2026-08-20T14:30", "format": "online",
            "tencentMeetingId": "111-222-333", "presenter": "陈果",
            "topic": "四季度展望",
        }, headers={"X-CSRF-Token": token})
        schedule_id = created.get_json()["item"]["id"]

        meta = json.dumps({
            "reportType": "roadshow", "category": "other", "theme": "credit",
            "org": "固收中心", "reportDate": "2026-08-20", "summary": "",
            "recommendation": "", "sourceAuthor": "", "sourceInstitution": "",
            "tags": "", "titles": {}, "roadshowScheduleId": schedule_id,
        }, ensure_ascii=False)
        data = {
            "meta": io.BytesIO(meta.encode("utf-8")),
            "file": (io.BytesIO(b"%PDF-1.4\n%%EOF"), "roadshow.pdf"),
        }
        upload = self.client.post("/internal-knowledge-base/api/reports/roadshow",
                                  data=data, content_type="multipart/form-data",
                                  headers={"X-CSRF-Token": token})
        self.assertEqual(upload.status_code, 200)
        report = upload.get_json()["reports"][0]
        self.assertEqual(report["roadshowScheduleId"], schedule_id)
        self.assertEqual(report["uploadedByName"], "测试成员")


if __name__ == "__main__":
    unittest.main()
