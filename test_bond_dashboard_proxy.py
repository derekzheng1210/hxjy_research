"""信用债一级发行看板反向代理与页面目录改动单测。

不真实拉起 Node 服务：上游用进程内 ThreadingHTTPServer 桩代替，
自动拉起逻辑只在“上游关闭 + AUTOSTART=0”分支上验证返回值。
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bond_dashboard_proxy as proxy
import page_registry
from app import app as portal_app


class StubUpstreamHandler(BaseHTTPRequestHandler):
    """按路径返回不同响应，并记录最近一次请求供断言。"""

    last_request = None

    def _respond(self, body: bytes, status: int, headers):
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).last_request = {
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": body,
        }
        if self.path.startswith("/bond-dashboard/api/"):
            payload = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
            self._respond(payload, 200, [("Content-Type", "application/json")])
            return
        if self.path.startswith("/bond-dashboard/static/"):
            self._respond(b"console.log(1)", 200, [("Content-Type", "application/javascript"), ("Cache-Control", "public, max-age=31536000, immutable")])
            return
        html = (
            "<!DOCTYPE html><html><head><title>stub</title></head>"
            "<body><div id=\"root\">dashboard-shell</div></body></html>"
        ).encode("utf-8")
        self._respond(
            html,
            200,
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Set-Cookie", "stub=1; Path=/"),
                ("ETag", '"upstream-etag"'),
            ],
        )

    do_GET = do_POST = do_HEAD = _handle

    def log_message(self, *args):
        pass


class RegistryTests(unittest.TestCase):
    def test_credit_primary_section_sits_after_interest_rate(self):
        keys = [section["key"] for section in page_registry.PAGE_SECTIONS]
        self.assertIn("credit_primary", keys)
        self.assertEqual(keys.index("credit_primary"), keys.index("interest_rate") + 1)
        section = next(s for s in page_registry.PAGE_SECTIONS if s["key"] == "credit_primary")
        self.assertEqual(section["title"], "信用债一级发行")
        self.assertEqual(section["pages"][0]["endpoint"], "bond_dashboard.index")
        # 一级偏离统计挪入本板块，排在一级发行看板之后
        self.assertEqual([p["key"] for p in section["pages"]], ["bond_dashboard", "primary_market_pricing"])
        credit = next(s for s in page_registry.PAGE_SECTIONS if s["key"] == "credit")
        self.assertNotIn("primary_market_pricing", [p["key"] for p in credit["pages"]])

    def test_primary_market_pricing_renamed(self):
        page = next(p for p in page_registry.all_pages() if p["key"] == "primary_market_pricing")
        self.assertEqual(page["title"], "一级偏离统计")

    def test_dashboard_route_registered(self):
        rules = {rule.rule for rule in portal_app.url_map.iter_rules()}
        self.assertIn("/bond-dashboard/", rules)
        self.assertIn("/bond-dashboard/<path:subpath>", rules)


class ProxyForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubUpstreamHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls._orig = (proxy.UPSTREAM, proxy.UPSTREAM_PORT, proxy.AUTOSTART, proxy._started)
        proxy.UPSTREAM = f"http://127.0.0.1:{cls.server.server_address[1]}"
        proxy.UPSTREAM_PORT = cls.server.server_address[1]
        proxy.AUTOSTART = False
        proxy._started = True

    @classmethod
    def tearDownClass(cls):
        proxy.UPSTREAM, proxy.UPSTREAM_PORT, proxy.AUTOSTART, proxy._started = cls._orig
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        portal_app.config["TESTING"] = True
        self.client = portal_app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True
        StubUpstreamHandler.last_request = None

    def test_unauthenticated_request_redirects_to_login(self):
        client = portal_app.test_client()
        response = client.get("/bond-dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

    def test_html_response_injects_portal_nav_loader_and_adjust(self):
        response = self.client.get("/bond-dashboard/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        # 导航本体经由 load 后脚本插入（App Router 水合会清掉 body 首部外来节点），
        # HTML 里应为 loader 脚本 + 内嵌 nav JSON + 偏移样式
        self.assertIn('id="portal-nav-loader"', html)
        self.assertIn("data-portal-nav", html)
        self.assertIn("portal-nav-dashboard-adjust", html)
        # 统一导航菜单包含新板块与改名后的入口（ensure_ascii=False 原样内嵌）
        self.assertIn("信用债一级发行", html)
        self.assertIn("一级偏离统计", html)
        # 注入后丢弃上游校验器，避免 304 拿到未注入的缓存页
        self.assertNotIn("ETag", response.headers)
        self.assertEqual(response.headers.get("Cache-Control"), "private, no-cache")
        # Set-Cookie 透传
        self.assertIn("stub=1", response.headers.get("Set-Cookie", ""))

    def test_json_api_passthrough_without_nav(self):
        response = self.client.get("/bond-dashboard/api/bids?limit=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/json")
        payload = json.loads(response.get_data(as_text=True))
        self.assertTrue(payload["ok"])
        self.assertNotIn("portal-nav", response.get_data(as_text=True))
        # 查询串原样转发到上游
        sent = StubUpstreamHandler.last_request
        self.assertEqual(sent["path"], "/bond-dashboard/api/bids?limit=5")

    def test_static_asset_keeps_cache_headers(self):
        response = self.client.get("/bond-dashboard/static/chunk.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/javascript")
        self.assertEqual(response.headers.get("Cache-Control"), "public, max-age=31536000, immutable")

    def test_post_body_and_content_type_forwarded(self):
        response = self.client.post(
            "/bond-dashboard/api/excel",
            data=b"binary-upload-bytes",
            headers={"Content-Type": "multipart/form-data; boundary=xyz"},
        )
        self.assertEqual(response.status_code, 200)
        sent = StubUpstreamHandler.last_request
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["body"], b"binary-upload-bytes")
        self.assertEqual(sent["headers"].get("Content-Type"), "multipart/form-data; boundary=xyz")
        # 明文转发（identity），门户侧再统一压缩
        self.assertEqual(sent["headers"].get("Accept-Encoding"), "identity")

    def test_ensure_server_returns_false_when_autostart_disabled(self):
        # 指向一个必然关闭的端口：AUTOSTART 关闭时不得尝试拉起进程
        saved = (proxy.UPSTREAM_PORT, proxy._started)
        try:
            proxy.UPSTREAM_PORT = 1  # tcpmux 端口本机基本不可能监听
            proxy._started = False
            self.assertFalse(proxy.ensure_server())
        finally:
            proxy.UPSTREAM_PORT, proxy._started = saved


if __name__ == "__main__":
    unittest.main()
