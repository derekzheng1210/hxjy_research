from __future__ import annotations

import json
import tempfile
import unittest
from functools import wraps
from pathlib import Path
from unittest.mock import patch

import requests
from flask import Flask, jsonify, session

from broker_market import personal_holdings as h


def module(kind, allowed=True):
    return {"id": f"mod_hold_bond_{kind}", "perm_group": {"rs": "pg_research", "all": "pg_all", "main": "pg_main"}[kind],
            "title": kind, "allowed": allowed, "layout": {"children": [
                {"id": f"c_hold_bond_{kind}_table", "kind": "content", "page_tool": "table"}]}}


def table(rows, unit=None):
    return {"ok": True, "rows": len(rows), "payload": {"kind": "table", "columns": [
        {"key": "assetid"}, {"key": "qty", "unit": unit}], "rows": rows}}


class PersonalHoldingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sessions.db"
        self.store = h.HoldingsStore("test-secret", self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_research_never_falls_back_to_leadership(self):
        self.assertEqual(h.select_module({"modules": [module("all"), module("rs")]}, "maxiao")["id"], "mod_hold_bond_rs")
        for username in ["zhenghongbin", "cuizhuoju", "maxiao", "zhumeng"]:
            with self.assertRaises(h.HoldingsError):
                h.select_module({"modules": [module("all"), module("rs", False)]}, username)
        self.assertEqual(h.select_module({"modules": [module("all", False), module("main"), module("rs", False)]}, "huyongyan")["id"], "mod_hold_bond_main")
        with self.assertRaises(h.HoldingsError):
            h.select_module({"modules": [module("all")]}, "unknown")

    def test_merge_quantity_and_keep_market_suffix(self):
        result = h.normalize_holdings(table([
            {"date": "2026-10-14", "assetid": "123.ib", "qty": "1000.1", "asset_name": "A"},
            {"date": "2026-10-14", "assetid": "123.IB", "qty": "2000.2"},
            {"date": "2026-10-14", "assetid": "123.SH", "qty": 20},
            {"date": "2026-10-14", "assetid": "456.IB", "qty": 0},
        ]))
        self.assertEqual([(b["code"], b["qty"]) for b in result["holdings"]], [("123.IB", 3000.3), ("123.SH", 20)])
        self.assertEqual(result["quantity_unit"], "")
        self.assertEqual(h.normalize_holdings(table([], "张"))["quantity_unit"], "张")

    def test_bad_data_fails_instead_of_showing_zero_or_partial_positions(self):
        for result in [table([{"assetid": "123.IB", "qty": "NaN"}]),
                       table([{"assetid": "123", "qty": 1}]),
                       table([{"assetid": "123.IB", "qty": None}]),
                       table([{"assetid": "123.IB", "qty": 1, "date": "2026-10-14"},
                              {"assetid": "123.IB", "qty": 1, "date": "2026-10-13"}]),
                       {**table([]), "rows": 100}, {"ok": False, "error": "failed"}]:
            with self.assertRaises(h.HoldingsError):
                h.normalize_holdings(result)
        self.assertEqual(h.normalize_holdings(table([]))["holdings"], [])

    def test_refresh_uses_empty_filters_and_correct_container(self):
        with patch.object(h, "upstream_json", side_effect=[{"id": "maxiao", "name": "M"},
             {"modules": [module("all"), module("rs")]}, table([])]) as call:
            result = h.fetch_holdings(requests.Session(), "maxiao")
        body = call.call_args.kwargs["json"]
        self.assertEqual(body["module_id"], "mod_hold_bond_rs")
        self.assertEqual(body["container_id"], "c_hold_bond_rs_table")
        self.assertEqual(body["shared"], {})
        self.assertEqual(body["own"], {})
        self.assertEqual(result["username"], "maxiao")

    def test_encrypted_cross_worker_session_and_isolation(self):
        payload = {"username": "A", "holdings": [{"code": "123.IB", "qty": 10}]}
        private = {"username": "A", "cookies": [{"name": "session", "value": "sensitive-cookie", "domain": "10.6.121.24", "path": "/", "secure": False, "expires": None}],
                   "payload": payload, "fetched": h.time.time()}
        self.store.save("browser-a", private)
        self.store.save("browser-b", {**private, "payload": {"username": "B", "holdings": []}})
        other_worker = h.HoldingsStore("test-secret", self.path)
        with patch.object(h, "fetch_holdings") as upstream:
            self.assertEqual(other_worker.get("browser-a"), payload)
            self.assertEqual(other_worker.get("browser-b")["username"], "B")
            upstream.assert_not_called()
        self.assertNotIn(b"sensitive-cookie", self.path.read_bytes())
        self.assertNotIn(b"browser-a", self.path.read_bytes())
        self.store.delete("browser-a")
        self.store.save("browser-a", private, existing=True)
        with self.assertRaises(h.HoldingsError):
            other_worker.get("browser-a")
        self.assertEqual(other_worker.get("browser-b")["username"], "B")

    def test_expiry_drops_session_and_never_relogs_with_password(self):
        self.store.save("token", {"username": "A", "cookies": [], "payload": {}, "fetched": 0})
        with patch.object(h, "fetch_holdings", side_effect=h.HoldingsError("expired", 401)):
            with self.assertRaises(h.HoldingsError):
                self.store.get("token")
        with self.assertRaises(h.HoldingsError):
            self.store.load("token")

    def test_routes_lazy_login_cookie_and_no_password_persistence(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.testing = True

        def protected(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                if not session.get("authenticated"):
                    return jsonify(error="portal login required"), 401
                return func(*args, **kwargs)
            return wrapper

        h.register(app, protected)
        client = app.test_client()
        path = "/api/secondary-bond-picker/holdings"
        with patch.object(h, "DB_PATH", self.path), patch.object(h, "upstream_json") as login, patch.object(h, "fetch_holdings", return_value={"username": "A", "holdings": []}) as fetch:
            self.assertEqual(client.get(path).status_code, 401)
            with client.session_transaction() as state:
                state["authenticated"] = True
            self.assertEqual(client.get(path).status_code, 401)
            login.assert_not_called()
            fetch.assert_not_called()
            response = client.post(path, json={"username": "A", "password": "do-not-store"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("HttpOnly", response.headers["Set-Cookie"])
            self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            self.assertNotIn("do-not-store", response.get_data(as_text=True))
            token = client.get_cookie(h.COOKIE, path=path).value
            self.assertNotIn("do-not-store", json.dumps(self.store.load(token)))
            self.assertEqual(client.get(path).status_code, 200)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(client.get(path + "?refresh=1").status_code, 200)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(client.delete(path, json={}, headers={"Origin": "http://evil.test"}).status_code, 403)
            self.assertEqual(client.delete(path, json={}).status_code, 200)
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.post(path, json=["invalid"]).status_code, 400)
            secure_response = client.post(path, json={"username": "A", "password": "test"},
                                          headers={"Origin": "https://localhost", "X-Forwarded-Proto": "https"})
            self.assertEqual(secure_response.status_code, 200)
            self.assertIn("Secure;", secure_response.headers["Set-Cookie"])


if __name__ == "__main__":
    unittest.main()
