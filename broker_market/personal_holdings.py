"""Personal fixed-income platform holdings; passwords are never persisted.

Only an opaque, random handle reaches the browser. Upstream cookies and personal
data are encrypted at rest; SQLite lets WSGI workers reuse sessions after restart.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken
from requests.cookies import create_cookie

from paths import DATA_ROOT

UPSTREAM = "http://10.6.121.24:8600"
PAGE = "p_hold_bond"
COOKIE = "secondary_holdings_session"
SESSION_TTL = 30 * 86400
CACHE_TTL = 300
DB_PATH = DATA_ROOT / "personal_holdings" / "sessions.sqlite3"
RESEARCH_USERS = frozenset({"zhenghongbin", "cuizhuoju", "maxiao", "zhumeng"})


class HoldingsError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def select_module(page, username):
    """Prefer research permissions; never fall back to leadership holdings."""
    modules = page.get("modules", [])
    research = [m for m in modules if m.get("perm_group") == "pg_research"
                or m.get("id") == "mod_hold_bond_rs" or "研究盘" in m.get("title", "")]
    allowed = [m for m in research if m.get("allowed")]
    if allowed:
        return allowed[0]
    if username.lower() in RESEARCH_USERS:
        raise HoldingsError("此账户的研究盘视角不可用，请联系内部平台管理员。", 403)
    direct = [m for m in modules if m.get("allowed") and m not in research
              and m.get("perm_group") != "pg_all" and m.get("id") != "mod_hold_bond_all"
              and "领导" not in m.get("title", "")]
    if len(direct) != 1:
        raise HoldingsError("未找到唯一可用的个人持仓视角，请联系内部平台管理员。", 403)
    return direct[0]


def table_container(node):
    if node.get("kind") == "content" and node.get("page_tool") == "table" and str(node.get("id", "")).endswith("_table"):
        return node["id"]
    for child in node.get("children", []):
        result = table_container(child)
        if result:
            return result
    return None


def normalize_holdings(result):
    payload = result.get("payload") or {}
    columns = {c.get("key"): c for c in payload.get("columns", [])}
    rows = payload.get("rows")
    if (result.get("ok") is not True or payload.get("kind") != "table"
            or not isinstance(rows, list) or not {"assetid", "qty"}.issubset(columns)):
        raise HoldingsError("内部平台持仓数据格式异常，请稍后刷新。")
    if isinstance(result.get("rows"), int) and result["rows"] > len(rows):
        raise HoldingsError("内部平台返回的持仓明细不完整，请联系管理员。")
    dates = {str(row.get("date") or "")[:10] for row in rows} - {""}
    if len(dates) > 1:
        raise HoldingsError("内部平台返回了多个持仓日期，请检查平台的最新日期设置。")
    bonds = {}
    for row in rows:
        code = str(row.get("assetid") or "").strip().upper()
        if not re.fullmatch(r"\d+\.(IB|SH|SZ|BJ)", code):
            raise HoldingsError("内部平台包含无法识别的债券代码，请联系管理员。")
        try:
            qty = Decimal(str(row["qty"]).replace(",", ""))
            if not qty.is_finite():
                raise InvalidOperation
        except (KeyError, InvalidOperation, ValueError):
            raise HoldingsError("内部平台包含无效的持仓数量，请联系管理员。") from None
        bond = bonds.setdefault(code, {"code": code, "name": str(row.get("asset_name") or ""), "qty": Decimal(0)})
        bond["qty"] += qty
    return {
        "holdings": [{**b, "qty": float(b["qty"])} for b in bonds.values() if b["qty"] != 0],
        "holding_date": next(iter(dates), ""),
        "quantity_unit": columns["qty"].get("unit") or "",
        "source_rows": len(rows),
    }


def upstream_json(http, method, path, **kwargs):
    try:
        response = http.request(method, UPSTREAM + path, timeout=(8, 45), allow_redirects=False, **kwargs)
        if response.status_code == 401:
            if path == "/api/login":
                raise HoldingsError("账号或密码错误，请重新输入。", 401)
            raise HoldingsError("内部平台登录已失效，请重新登录。", 401)
        if response.status_code == 403:
            raise HoldingsError("此账户没有所需持仓权限，请联系内部平台管理员。", 403)
        if path == "/api/login" and response.status_code in {400, 422}:
            raise HoldingsError("账号或密码错误，请重新输入。", 401)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except HoldingsError:
        raise
    except requests.RequestException:
        raise HoldingsError("暂时无法连接固收内部数据平台，请稍后重试。") from None
    except ValueError:
        raise HoldingsError("固收内部数据平台返回格式异常。") from None


def fetch_holdings(http, username):
    me = upstream_json(http, "GET", "/api/me")
    if str(me.get("id", "")).lower() != username.lower():
        raise HoldingsError("内部平台账户会话不匹配，请重新登录。", 401)
    page = upstream_json(http, "GET", f"/api/page/{PAGE}?mode=live")
    module = select_module(page, username)
    container = table_container(module.get("layout") or {})
    if not container:
        raise HoldingsError("内部平台未提供债券持仓明细表。")
    result = upstream_json(http, "POST", "/api/run", json={
        "mode": "live", "page_id": PAGE, "module_id": module["id"],
        "container_id": container, "shared": {}, "own": {},
    })
    data = normalize_holdings(result)
    data["holding_date"] = data["holding_date"] or me.get("latest_date") or ""
    data.update(username=username, display_name=me.get("name") or username,
                perspective=module.get("perm_group_name") or module.get("title"),
                refreshed_at=datetime.now().isoformat(timespec="seconds"))
    return data


class HoldingsStore:
    def __init__(self, secret, path=None):
        self.path = Path(path or DB_PATH)
        self.fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(
            (str(secret) + ":personal-holdings:v1").encode()).digest()))

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires REAL NOT NULL, data BLOB NOT NULL)")
        db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def save(self, token, data, *, existing=False):
        encrypted = self.fernet.encrypt(json.dumps(data, ensure_ascii=False).encode())
        with self.connect() as db:
            if existing:
                # A concurrent logout must never be undone by an in-flight refresh.
                db.execute("UPDATE sessions SET expires=?, data=? WHERE id=?", (time.time() + SESSION_TTL, encrypted, self.key(token)))
            else:
                db.execute("INSERT INTO sessions VALUES (?,?,?)", (self.key(token), time.time() + SESSION_TTL, encrypted))

    def load(self, token):
        if not token or len(token) > 100:
            raise HoldingsError("请登录固收内部数据平台以查看个人持仓。", 401)
        with self.connect() as db:
            row = db.execute("SELECT data FROM sessions WHERE id=?", (self.key(token),)).fetchone()
        if row:
            try:
                return json.loads(self.fernet.decrypt(row[0]))
            except (InvalidToken, ValueError):
                self.delete(token)
        raise HoldingsError("内部平台登录已失效，请重新登录。", 401)

    def delete(self, token):
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE id=?", (self.key(token),))

    @staticmethod
    def cookies(http):
        return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                 "secure": c.secure, "expires": c.expires} for c in http.cookies]

    def login(self, username, password):
        with requests.Session() as http:
            upstream_json(http, "POST", "/api/login", json={"user_id": username, "password": password})
            payload = fetch_holdings(http, username)
            token = secrets.token_urlsafe(32)
            self.save(token, {"username": username, "cookies": self.cookies(http),
                              "payload": payload, "fetched": time.time()})
        return token, payload

    def get(self, token, refresh=False):
        data = self.load(token)
        if not refresh and time.time() - data["fetched"] < CACHE_TTL:
            return data["payload"]
        try:
            with requests.Session() as http:
                for cookie in data["cookies"]:
                    http.cookies.set_cookie(create_cookie(**cookie))
                payload = fetch_holdings(http, data["username"])
                data.update(payload=payload, fetched=time.time(), cookies=self.cookies(http))
                self.save(token, data, existing=True)
                return payload
        except HoldingsError as exc:
            if exc.status in {401, 403}:
                self.delete(token)
            raise


def register(app, login_required):
    from flask import jsonify, request

    def store():
        return HoldingsStore(app.secret_key)

    @app.route("/api/secondary-bond-picker/holdings", methods=["GET", "POST", "DELETE"])
    @login_required
    def personal_holdings():
        token = request.cookies.get(COOKIE, "")
        try:
            if request.method != "GET":
                origin = request.headers.get("Origin")
                parsed_origin = urlparse(origin) if origin else None
                if (parsed_origin and (parsed_origin.scheme not in {"http", "https"}
                                       or parsed_origin.netloc.lower() != request.host.lower())) or not request.is_json:
                    raise HoldingsError("请求来源无效，请刷新页面后重试。", 403)
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                if not isinstance(body, dict):
                    raise HoldingsError("请输入有效的账号和密码。", 400)
                username = str(body.get("username") or "").strip()
                password = body.get("password")
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", username) or not isinstance(password, str) or not 0 < len(password) <= 1000:
                    raise HoldingsError("请输入有效的账号和密码。", 400)
                new_token, payload = store().login(username, password)
                store().delete(token)
                token = new_token
            elif request.method == "DELETE":
                store().delete(token)
                payload = {"ok": True}
            else:
                payload = store().get(token, refresh=request.args.get("refresh") == "1")
            response = jsonify(payload)
            if request.method == "DELETE":
                response.delete_cookie(COOKIE, path="/api/secondary-bond-picker/holdings")
            else:
                response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True,
                                    secure=request.is_secure or app.config.get("SESSION_COOKIE_SECURE", False)
                                    or request.headers.get("X-Forwarded-Proto") == "https",
                                    samesite="Strict", path="/api/secondary-bond-picker/holdings")
        except HoldingsError as exc:
            response = jsonify({"error": str(exc), "login_required": exc.status == 401})
            response.status_code = exc.status
            if request.method == "GET" and exc.status in {401, 403}:
                response.delete_cookie(COOKIE, path="/api/secondary-bond-picker/holdings")
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        return response
