from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import app as portal
from broker_market import storage


class BondPickerMarketApiTests(unittest.TestCase):
    def setUp(self):
        portal.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = portal.app.test_client()

    def authenticate(self):
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def test_data_api_requires_login_and_supports_etag(self):
        response = self.client.get("/api/bond-picker/data")
        self.assertEqual(response.status_code, 302)
        self.authenticate()
        response = self.client.get("/api/bond-picker/data")
        self.assertEqual(response.status_code, 200)
        self.assertIn("meta", response.get_json())
        etag = response.headers["ETag"]
        cached = self.client.get("/api/bond-picker/data", headers={"If-None-Match": etag})
        self.assertEqual(cached.status_code, 304)

    def test_preferences_round_trip_without_shared_presets(self):
        self.authenticate()
        target = Path.cwd() / ".test-bond-picker-preferences.json"
        try:
            with patch.object(storage, "PREFERENCES_PATH", target):
                response = self.client.put("/api/bond-picker/preferences", json={
                    "favorites": ["102681601.ib"],
                    "presets": [{"id": "cheap", "name": "便宜卖盘", "filters": {"hasOffer": True}, "sort": []}],
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["favorites"], ["102681601.IB"])
                self.assertNotIn("presets", response.get_json())
                loaded = self.client.get("/api/bond-picker/preferences")
                self.assertNotIn("presets", loaded.get_json())
        finally:
            target.unlink(missing_ok=True)

    def test_secondary_picker_is_separate_primary_tool(self):
        self.authenticate()
        with patch.object(portal, "BONDS_CACHE", []):
            response = self.client.get("/secondary-bond-picker")
        self.assertEqual(response.status_code, 200)
        self.assertIn("二级择券工具", response.get_data(as_text=True))
        inversion = self.client.get("/bond-picker")
        text = inversion.get_data(as_text=True)
        self.assertEqual(inversion.status_code, 200)
        self.assertIn("收益率倒挂挖掘工具", text)
        self.assertNotIn('id="pane-normal"', text)

    def test_secondary_data_alias_requires_login(self):
        anonymous = portal.app.test_client().get("/api/secondary-bond-picker/data")
        self.assertEqual(anonymous.status_code, 302)
        self.authenticate()
        response = self.client.get("/api/secondary-bond-picker/data")
        self.assertEqual(response.status_code, 200)
        self.assertIn("emotion_history", response.get_json()["meta"])


if __name__ == "__main__":
    unittest.main()
