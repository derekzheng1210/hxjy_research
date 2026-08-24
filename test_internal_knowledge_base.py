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
from internal_knowledge_base.routes import (  # noqa: E402
    KNOWLEDGE_INTENT_GENERAL, KNOWLEDGE_INTENT_RETRIEVAL, KNOWLEDGE_SOURCE_MARKER,
    PREVIEW_CACHE_DIR, _index_knowledge_report, _knowledge_candidates, _knowledge_intent,
    _knowledge_qa_messages, _parse_knowledge_filters, _report_matches_knowledge_filters, store,
)


class InternalKnowledgeBaseTest(unittest.TestCase):
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
        store.set_admin_password_hash(generate_password_hash("admin-password"))
        self.client = app.test_client()

    def portal_session(self):
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def csrf(self, path="/internal-knowledge-base/"):
        response = self.client.get(path)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_portal_gate_and_namespaced_login(self):
        response = self.client.get("/internal-knowledge-base/")
        self.assertEqual(response.status_code, 302)
        self.portal_session()
        token = self.csrf()
        missing_csrf = self.client.post("/internal-knowledge-base/api/login", json={
            "username": "member", "password": "member-password",
        })
        self.assertEqual(missing_csrf.status_code, 403)
        response = self.client.post("/internal-knowledge-base/api/login", json={
            "username": "member", "password": "member-password",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["internal_knowledge_base_user_id"], "member")
            self.assertTrue(session["authenticated"])

    def test_database_starts_without_business_records_and_password_is_hashed(self):
        self.assertEqual(store.reports(), [])
        self.assertEqual(store.ratings(), [])
        user = store.get_user("member")
        self.assertNotEqual(user["password_hash"], "member-password")
        self.assertNotIn("password", user)

    def test_reminder_config_is_separate_from_empty_business_records(self):
        store.set_reminder_config({
            "period": "2026", "reportCategory": "deep",
            "rules": [{"id": "rule-1", "label": "测试专题", "mode": "person",
                       "target": 2, "userIds": ["member"]}],
        })
        config = store.reminder_config()
        self.assertEqual(config["period"], "2026")
        self.assertEqual(config["rules"][0]["target"], 2)
        self.assertEqual(store.reports(), [])
        self.assertEqual(store.ratings(), [])

    def test_admin_route_is_isolated_and_password_can_change(self):
        self.portal_session()
        portal_admin = self.client.get("/admin")
        self.assertEqual(portal_admin.status_code, 302)
        token = self.csrf("/internal-knowledge-base/admin")
        response = self.client.post("/internal-knowledge-base/api/admin/login",
                                    json={"password": "admin-password"},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        changed = self.client.post("/internal-knowledge-base/api/admin/change-password", json={
            "oldPassword": "admin-password", "newPassword": "new-admin-password",
            "confirmPassword": "new-admin-password",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(changed.status_code, 200)
        self.client.post("/internal-knowledge-base/api/admin/logout",
                         headers={"X-CSRF-Token": token})
        old_login = self.client.post("/internal-knowledge-base/api/admin/login",
                                     json={"password": "admin-password"},
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(old_login.status_code, 401)
        new_login = self.client.post("/internal-knowledge-base/api/admin/login",
                                     json={"password": "new-admin-password"},
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(new_login.status_code, 200)

    def test_superadmin_can_list_and_delete_permanent_pdf_cache(self):
        self.portal_session()
        token = self.csrf("/internal-knowledge-base/admin")
        self.client.post("/internal-knowledge-base/api/admin/login",
                         json={"password": "admin-password"},
                         headers={"X-CSRF-Token": token})
        key = "a" * 64
        cache_path = Path(PREVIEW_CACHE_DIR, f"{key}.pdf")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"%PDF-1.4\n%%EOF")
        store.upsert_pdf_cache(key, None, "b" * 64, cache_path.name,
                               cache_path.stat().st_size, "test-version")
        listing = self.client.get("/internal-knowledge-base/api/admin/pdf-cache")
        self.assertEqual(listing.get_json()["count"], 1)
        deleted = self.client.delete(f"/internal-knowledge-base/api/admin/pdf-cache/{key}",
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(cache_path.exists())
        self.assertEqual(store.pdf_caches(), [])

    def test_knowledge_search_defaults_to_all_report_types(self):
        filters = _parse_knowledge_filters({"period": "all"})
        self.assertEqual(filters["report_types"], [])
        self.assertTrue(_report_matches_knowledge_filters(
            {"reportType": "external", "reportDate": "2026-08-20"}, filters
        ))
        self.assertTrue(_report_matches_knowledge_filters(
            {"reportType": "roadshow", "reportDate": "2026-08-20"}, filters
        ))

    def test_knowledge_intent_routes_report_lookup_and_general_work(self):
        retrieval_questions = (
            "有哪些报告讨论了城投利差？",
            "请归纳这篇报告的核心观点",
            "信用利差近期有什么变化？",
        )
        general_questions = (
            "统计最近一个月报告中看多债市的观点数量和占比",
            "比较不同报告核心观点的共同点和分歧",
            "基于这些报告拟一份路演提纲",
            "汇总所有报告的观点并按主题分类",
        )
        for question in retrieval_questions:
            self.assertEqual(_knowledge_intent(question), KNOWLEDGE_INTENT_RETRIEVAL)
        for question in general_questions:
            self.assertEqual(_knowledge_intent(question), KNOWLEDGE_INTENT_GENERAL)

    def test_knowledge_prompt_switches_between_two_frameworks(self):
        candidates = [{
            "id": "r001", "title": "信用利差周度观察", "author": "研究员甲",
            "published_at": "2026-08-20", "theme": "信用", "category": "周报",
            "content": "本周信用利差整体收窄，短端表现更为明显。",
        }]
        retrieval_system, retrieval_prompt = _knowledge_qa_messages(
            "这篇报告的核心观点是什么？", candidates,
        )
        general_system, general_prompt = _knowledge_qa_messages(
            "统计并比较这批报告的观点", candidates,
        )
        self.assertIn("严格使用以下模板", retrieval_system)
        self.assertIn("不强制套用逐篇报告摘要模板", general_system)
        self.assertIn("统计口径、样本范围和样本数量", general_system)
        self.assertIn(KNOWLEDGE_SOURCE_MARKER, retrieval_system)
        self.assertIn(KNOWLEDGE_SOURCE_MARKER, general_system)
        self.assertIn("识别为报告检索或核心观点查询", retrieval_prompt)
        self.assertIn("识别为综合工作任务", general_prompt)
        self.assertIn("[REPORT_ID:r001]", retrieval_prompt)
        self.assertIn("[REPORT_ID:r001]", general_prompt)

    def test_persistent_vector_index_recalls_relevant_external_report(self):
        relevant = {
            "id": "report-vector-relevant", "title": "城投平台流动性跟踪",
            "summary": "地方政府融资平台再融资改善，信用利差出现收窄。",
            "tags": ["城投", "资金面"], "author": "研究员甲", "authorId": "member",
            "sourceAuthor": "外部作者", "sourceInstitution": "研究机构",
            "reportType": "external", "category": "other", "theme": "credit",
            "reportDate": "2026-08-20", "uploadedAt": "2026-08-20T10:00:00+08:00",
            "fileStored": False, "fileUrl": "",
        }
        unrelated = {
            **relevant, "id": "report-vector-unrelated", "title": "消费行业盈利观察",
            "summary": "食品饮料企业盈利增速与渠道库存分析。", "tags": ["消费"],
        }
        store.add_report(relevant)
        store.add_report(unrelated)
        self.assertTrue(_index_knowledge_report(relevant))
        self.assertTrue(_index_knowledge_report(unrelated))
        self.assertEqual(store.knowledge_index_stats()["reports"], 2)

        candidates = _knowledge_candidates(
            "城投公司的资金面和信用利差有什么变化？",
            filters=_parse_knowledge_filters({"period": "all"}),
        )
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["id"], relevant["id"])
        # Unchanged content uses the persisted vector rather than rebuilding.
        self.assertFalse(_index_knowledge_report(relevant))


if __name__ == "__main__":
    unittest.main()
