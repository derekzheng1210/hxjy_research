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
from internal_knowledge_base.routes import PREVIEW_CACHE_DIR, store  # noqa: E402


class InternalKnowledgeBaseTest(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        with store.transaction() as conn:
            for table in ("audit_log", "pdf_cache", "engagement", "ratings", "reports",
                          "qa_usage", "qa_history", "users"):
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


if __name__ == "__main__":
    unittest.main()
