import json
import tempfile
import unittest
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

import app as portal_app
from cleanup_redundant_data import mirrored_payloads_match
from juyuan_update import institution_flow_rates


def uploaded(content: bytes, filename: str = "data.js") -> FileStorage:
    return FileStorage(stream=BytesIO(content), filename=filename)


class UploadRetentionTests(unittest.TestCase):
    def test_same_content_does_not_create_a_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "data" / "current.js"
            destination.parent.mkdir()
            destination.write_bytes(b"original")
            with patch.object(portal_app, "UPLOADS_DIR", root / "uploads"):
                changed = portal_app.save_upload(uploaded(b"original"), destination, {".js"}, "test_js")
            self.assertFalse(changed)
            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(list((root / "uploads").glob("test_js_backup_*")), [])

    def test_changed_content_is_atomic_and_retains_three_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "data" / "current.js"
            destination.parent.mkdir()
            destination.write_bytes(b"original")
            with patch.object(portal_app, "UPLOADS_DIR", root / "uploads"):
                for content in (b"one", b"two", b"three", b"four"):
                    self.assertTrue(portal_app.save_upload(uploaded(content), destination, {".js"}, "test_js"))
            backups = list((root / "uploads").glob("test_js_backup_*.js"))
            self.assertEqual(destination.read_bytes(), b"four")
            self.assertEqual(len(backups), 3)
            self.assertEqual({path.read_bytes() for path in backups}, {b"one", b"two", b"three"})

    def test_invalid_extension_is_rejected_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "current.js"
            destination.write_bytes(b"original")
            with self.assertRaises(ValueError):
                portal_app.save_upload(uploaded(b"new", "data.txt"), destination, {".js"}, "test_js")
            self.assertEqual(destination.read_bytes(), b"original")


class CompatibilityTests(unittest.TestCase):
    def test_json_js_mirror_comparison_is_semantic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "spread_data.json"
            js_path = root / "spread_data.js"
            json_path.write_text('{"data": [1], "update_time": "now"}', encoding="utf-8")
            js_path.write_text('var SPREAD_DATA = {"update_time":"now","data":[1]};\n', encoding="utf-8")
            self.assertTrue(mirrored_payloads_match(json_path, js_path))

    def test_authenticated_pages_remain_available(self):
        client = portal_app.app.test_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
        for url in ("/", "/bond-picker", "/spread-monitor", "/industry-prosperity", "/credit-std-dev", "/primary-market-pricing/"):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)


class InstitutionFlowTests(unittest.TestCase):
    def setUp(self):
        self.client = portal_app.app.test_client()
        portal_app._institution_flow_options.update(timestamp=0.0, payload=None)

    def authenticate(self, admin=False):
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            if admin:
                session["admin_authenticated"] = True

    def test_institution_flow_requires_site_login_and_injects_navigation(self):
        response = self.client.get("/institution-flow")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/institution-flow", response.headers["Location"])

        self.authenticate()
        response = self.client.get("/institution-flow")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-portal-nav="credit-tools"', html)
        self.assertIn("机构行为监测", html)
        self.assertIn("/static/institution_flow/dashboard.js", html)

    def test_overlay_api_validates_parameters_and_reads_migrated_cache(self):
        self.authenticate()
        self.assertEqual(self.client.get("/institution-flow/api/overlay/yield").status_code, 400)
        response = self.client.get("/institution-flow/api/overlay/yield?curve=国债&tenor=30Y")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["curve"], "国债")
        self.assertEqual(payload["tenor"], "30Y")
        self.assertGreater(len(payload["dates"]), 1000)

    def test_proxy_preserves_repeated_query_arguments_and_caches_options(self):
        self.authenticate()
        upstream = SimpleNamespace(
            ok=True,
            content=b'{"ok":true}',
            headers={"Content-Type": "application/json"},
            status_code=200,
            json=lambda: {"ok": True},
        )
        with patch.object(portal_app._institution_flow_http, "get", return_value=upstream) as get:
            response = self.client.get("/institution-flow/bondflow/api/options?institution=A&institution=B")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(get.call_args.kwargs["params"], [("institution", "A"), ("institution", "B")])

            self.client.get("/institution-flow/bondflow/api/options")
            self.client.get("/institution-flow/bondflow/api/options")
            self.assertEqual(get.call_count, 2)

    def test_proxy_returns_502_when_upstream_fails(self):
        self.authenticate()
        with patch.object(portal_app._institution_flow_http, "get", side_effect=portal_app.requests.RequestException("offline")):
            response = self.client.get("/institution-flow/bondflow/api/options")
        self.assertEqual(response.status_code, 502)
        self.assertIn("上游数据请求失败", response.get_json()["error"])

    def test_admin_requires_second_password(self):
        self.authenticate()
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login?next=/admin", response.headers["Location"])

        response = self.client.post("/admin/login", data={"password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("后台密码错误", response.get_data(as_text=True))

        response = self.client.post("/admin/login?next=/admin", data={"password": "123456"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self.assertEqual(self.client.get("/api/update-status").status_code, 200)

        response = self.client.get("/admin/logout")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_rate_curve_updater_merges_new_points_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "rate_curves_cache.json"
            cache.write_text(
                '{"curves":{"国债":{"1":{"dates":["2026-07-31"],"values":[1.1]}}}}',
                encoding="utf-8",
            )
            with (
                patch.object(institution_flow_rates, "CACHE", cache),
                patch.object(institution_flow_rates, "connect", return_value=nullcontext(object())),
                patch.object(institution_flow_rates, "latest_curve_date", return_value="20260803"),
                patch.object(institution_flow_rates, "_fetch_chunk", return_value={1.0: {"20260803": 1.2}}),
            ):
                result = institution_flow_rates.update_rate_curves()
            payload = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(payload["curves"]["国债"]["1"]["dates"][-1], "2026-08-03")
            self.assertEqual(payload["curves"]["国债"]["1"]["values"][-1], 1.2)
            self.assertEqual(result["end_date"], "2026-08-03")


if __name__ == "__main__":
    unittest.main()
