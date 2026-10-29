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


def table(rows, unit=None, dims=False):
    columns = [{"key": "assetid"}]
    if dims:
        columns += [{"key": key} for key in h.DIMENSION_COLUMNS]
    columns.append({"key": "qty", "unit": unit})
    return {"ok": True, "rows": len(rows), "payload": {"kind": "table", "columns": columns, "rows": rows}}


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

    def test_dimensions_aggregate_per_fund_unit_portfolio(self):
        result = h.normalize_holdings(table([
            {"date": "2026-10-18", "assetid": "123.IB", "qty": 100, "asset_name": "A", "fund_name": "基金甲", "prd_name": "单元一", "l4_name": "组合1"},
            {"date": "2026-10-18", "assetid": "123.IB", "qty": 50, "fund_name": "基金乙", "prd_name": "单元二", "l4_name": "组合2"},
            {"date": "2026-10-18", "assetid": "123.IB", "qty": 25, "fund_name": "基金乙", "prd_name": "单元二", "l4_name": "组合2"},
            {"date": "2026-10-18", "assetid": "456.SH", "qty": 10, "asset_name": "B", "fund_name": "基金甲", "prd_name": "单元一", "l4_name": "组合1"},
            {"date": "2026-10-18", "assetid": "789.IB", "qty": 5, "fund_name": "基金甲", "prd_name": "单元一", "l4_name": "组合1"},
            {"date": "2026-10-18", "assetid": "789.IB", "qty": -5, "fund_name": "基金甲", "prd_name": "单元一", "l4_name": "组合1"},
            {"date": "2026-10-18", "assetid": "111.IB", "qty": 80, "fund_name": "基金甲", "prd_name": "单元一", "l4_name": "组合1"},
            {"date": "2026-10-18", "assetid": "111.IB", "qty": 20, "fund_name": "基金丙", "prd_name": "单元三", "l4_name": "组合3"},
            {"date": "2026-10-18", "assetid": "111.IB", "qty": -20, "fund_name": "基金丙", "prd_name": "单元三", "l4_name": "组合3"},
        ], dims=True))
        by_code = {b["code"]: b for b in result["holdings"]}
        self.assertEqual((by_code["123.IB"]["qty"], by_code["456.SH"]["qty"], by_code["111.IB"]["qty"]), (175, 10, 80))
        self.assertNotIn("789.IB", by_code, "zero-total bonds stay hidden")
        self.assertEqual(by_code["123.IB"]["details"], [
            {"fund": "基金甲", "unit": "单元一", "portfolio": "组合1", "qty": 100},
            {"fund": "基金乙", "unit": "单元二", "portfolio": "组合2", "qty": 75}])
        self.assertEqual(by_code["111.IB"]["details"],
                         [{"fund": "基金甲", "unit": "单元一", "portfolio": "组合1", "qty": 80}],
                         "dimension tuples netting to zero are dropped")
        # Upstream without the dimension columns: holdings still load, without a details key.
        plain = h.normalize_holdings(table([{"date": "2026-10-18", "assetid": "123.IB", "qty": 5, "fund_name": "基金甲"}]))
        self.assertEqual(plain["holdings"][0]["qty"], 5)
        self.assertNotIn("details", plain["holdings"][0])

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

    def test_upstream_prefers_wired_address_and_fails_over_on_transport_errors(self):
        class FakeResponse:
            def __init__(self, status_code=200, data=None):
                self.status_code = status_code
                self._data = {} if data is None else data

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(f"{self.status_code} error")

            def json(self):
                if not isinstance(self._data, dict):
                    raise ValueError("not a json object")
                return self._data

        class FakeHTTP:
            def __init__(self, unreachable=(), status=200):
                self.unreachable = set(unreachable)
                self.status = status
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append(url)
                if any(host in url for host in self.unreachable):
                    raise requests.ConnectionError("unreachable")
                return FakeResponse(self.status)

        self.assertEqual(h.UPSTREAMS[0], "http://10.6.8.190:8600")
        http = FakeHTTP()
        self.assertEqual(h.upstream_json(http, "GET", "/api/me"), {})
        self.assertEqual(http.calls, [f"{h.UPSTREAMS[0]}/api/me"])

        http = FakeHTTP(unreachable=["10.6.8.190"])
        self.assertEqual(h.upstream_json(http, "GET", "/api/me"), {})
        self.assertEqual(http.calls, [f"{h.UPSTREAMS[0]}/api/me", f"{h.UPSTREAMS[1]}/api/me"])

        http = FakeHTTP(unreachable=["10.6.8.190", "10.6.121.24"])
        with self.assertRaises(h.HoldingsError):
            h.upstream_json(http, "GET", "/api/me")
        self.assertEqual(len(http.calls), 2)

        # A host that answers with an HTTP error is final — never retried elsewhere.
        http = FakeHTTP(status=500)
        with self.assertRaises(h.HoldingsError):
            h.upstream_json(http, "GET", "/api/me")
        self.assertEqual(http.calls, [f"{h.UPSTREAMS[0]}/api/me"])

    def test_session_cookies_are_replayed_across_upstream_addresses(self):
        cookies = [{"name": "session", "value": "upstream-cookie", "domain": "10.6.121.24",
                    "path": "/", "secure": False, "expires": None}]
        self.store.save("token", {"username": "A", "cookies": cookies, "payload": {}, "fetched": 0})
        sent = {}

        def fake_fetch(http, username):
            for base in h.UPSTREAMS:
                prepared = requests.Request("GET", f"{base}/api/me").prepare()
                sent[base] = requests.cookies.get_cookie_header(http.cookies, prepared)
            return {"username": username, "holdings": []}

        with patch.object(h, "fetch_holdings", side_effect=fake_fetch):
            self.store.get("token")
        for base in h.UPSTREAMS:
            self.assertIn("upstream-cookie", sent[base] or "")

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
