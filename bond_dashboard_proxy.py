"""信用债一级发行看板（bond_dashboard，Next.js）反向代理。

看板是独立的 Node 服务（`next start`，构建产物已随仓库携带），构建期固定
basePath=/bond-dashboard，门户在同前缀下把请求整站转发到本机上游：

- HTML 响应注入统一顶部导航栏（与其他门户页面同一套 portal_nav 机制），
  并附加看板外壳的 44px 顶部偏移样式；
- 静态资源与 API 原样透传（Set-Cookie、immutable 缓存头等）；
- 上传接口为 multipart，请求体整体读入后转发（Excel 文件很小，无流式必要）；
- 刷新接口为同步长任务，读超时放宽到 15 分钟。

进程管理：首次请求时若上游端口未监听则自动拉起 `node scripts/serve.mjs`
（分离进程，绑定 127.0.0.1，避免绕过门户鉴权直连）；DM 凭证、内评文件路径
等环境变量由门户统一下发。`BOND_DASHBOARD_AUTOSTART=0` 关闭自动拉起，
`BOND_DASHBOARD_UPSTREAM` 可改上游地址（默认 http://127.0.0.1:3100）。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Blueprint, Response, request

from paths import BASE_DIR, BOND_DASHBOARD_DIR, DATA_DIR, LOGS_DIR

bp = Blueprint("bond_dashboard", __name__)

DASHBOARD_DIR = BASE_DIR / "bond_dashboard"
SERVE_SCRIPT = DASHBOARD_DIR / "scripts" / "serve.mjs"
UPSTREAM = os.environ.get("BOND_DASHBOARD_UPSTREAM", "http://127.0.0.1:3100").rstrip("/")
UPSTREAM_PORT = urlparse(UPSTREAM).port or 80
AUTOSTART = os.environ.get("BOND_DASHBOARD_AUTOSTART", "1") == "1"
UPSTREAM_TIMEOUT = (5, 900)
BOOT_WAIT_SECONDS = float(os.environ.get("BOND_DASHBOARD_BOOT_WAIT", "90"))

FORWARD_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# 逐跳头与长度/编码头不透传：代理是同机明文转发，长度由 Flask 重算
SKIP_REQUEST_HEADERS = {"host", "content-length", "connection", "keep-alive", "accept-encoding", "transfer-encoding"}
SKIP_RESPONSE_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding"}

# 看板自身的门户导航高亮端点（page_registry 中信用债一级发行板块）
ACTIVE_ENDPOINT = "bond_dashboard.index"

# 看板外壳是固定定位侧边栏/移动端顶栏，需要为 44px 的门户导航让位；
# 数值须与 app.py portal_nav 的 --portal-nav-height 保持一致。
# body 预留 44px 且导航改 fixed：load 后插入导航时零布局跳动。
# 注：样式标签可直插 body（React 水合清理不动它，实测验证）。
_DASHBOARD_ADJUST_STYLE = (
    '<style id="portal-nav-dashboard-adjust">'
    "body{padding-top:44px!important}"
    ".portal-nav{position:fixed!important}"
    "aside.fixed.inset-y-0.left-0{top:44px!important;height:calc(100vh - 44px)!important}"
    "div.fixed.inset-x-0.top-0{top:44px!important}"
    "</style>"
)

_http = requests.Session()
# 严格走本机直连：系统代理环境变量不得劫持 127.0.0.1 上游
_http.trust_env = False

_start_lock = threading.Lock()
_started = False


def _port_listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", UPSTREAM_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _node_executable() -> str | None:
    custom = os.environ.get("BOND_DASHBOARD_NODE")
    if custom and Path(custom).exists():
        return custom
    return shutil.which("node")


def _dashboard_env() -> dict:
    env = dict(os.environ)
    env["PORT"] = str(UPSTREAM_PORT)
    env["HOST"] = "127.0.0.1"
    # 内评文件默认指向门户数据目录下的 portal_data.json（可用环境变量覆盖）
    env.setdefault("INTERNAL_RATINGS_FILE", str(DATA_DIR / "portal_data.json"))
    # 看板运行态数据整体外置到 PORTAL_DATA_ROOT（可用环境变量覆盖）
    env.setdefault("BOND_DASHBOARD_DATA_DIR", str(BOND_DASHBOARD_DIR))
    # DM 凭证等若门户已配置则下发（真实进程环境优先于看板自己的 .env.local）
    for key in ("INNO_APP_KEY", "INNO_APP_SECRET", "WECHAT_WEBHOOK", "DM_REFRESH_AT"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    env.pop("NODE_OPTIONS", None)
    return env


def ensure_server(force: bool = False, wait_seconds: float | None = None) -> bool:
    """确保看板 Node 服务在监听；未监听则分离式拉起并等待就绪。"""
    global _started
    wait_seconds = BOOT_WAIT_SECONDS if wait_seconds is None else wait_seconds
    with _start_lock:
        if _port_listening():
            _started = True
            return True
        if (_started and not force) or not AUTOSTART:
            return False
        node = _node_executable()
        if node is None or not SERVE_SCRIPT.exists():
            return False
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with (LOGS_DIR / "bond_dashboard_spawn.log").open("ab") as log:
            subprocess.Popen(
                [node, str(SERVE_SCRIPT)],
                cwd=str(DASHBOARD_DIR),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=_dashboard_env(),
                creationflags=creationflags,
                close_fds=True,
            )
        deadline = time.monotonic() + max(wait_seconds, 0.0)
        while time.monotonic() < deadline:
            if _port_listening():
                _started = True
                return True
            time.sleep(1.0)
        return False


def _dashboard_loader_script(nav_html: str) -> str:
    # Next.js App Router 对整份 document 做水合：body 首部的陌生元素节点会在
    # 水合失配回退清理时被移除（实测复现），因此导航本体不能直插 HTML，改为
    # load 事件后由脚本插入（GTM 同款做法，水合后插入的节点 React 不再管理）；
    # 带重试兜底极端时序（插入早于水合完成被误删时 1 秒后补插）。
    payload = json.dumps(nav_html, ensure_ascii=False)
    return (
        '<script id="portal-nav-loader">(function(){'
        f"var NAV={payload};"
        "function add(){if(document.querySelector('.portal-nav'))return true;"
        "document.body.insertAdjacentHTML('afterbegin',NAV);return false}"
        "function ensure(n){if(add())return;if(n>0)setTimeout(function(){ensure(n-1)},1000)}"
        "if(document.readyState==='complete')ensure(3);"
        "else window.addEventListener('load',function(){ensure(3)})})();</script>"
    )


def _inject_dashboard_nav(html: str) -> str:
    # 延迟导入避免与 app.py 循环依赖（请求期 app 模块必然已完成加载）
    from app import insert_portal_nav, portal_nav

    block = _DASHBOARD_ADJUST_STYLE + _dashboard_loader_script(portal_nav(ACTIVE_ENDPOINT))
    return insert_portal_nav(html, block)


def _unavailable_page() -> Response:
    detail = (
        "看板上游服务未就绪。已尝试自动拉起 Node 服务"
        f"（127.0.0.1:{UPSTREAM_PORT}），请稍后刷新重试。"
        "若持续失败，请在 bond_dashboard 目录手工执行："
        "<code>node scripts/serve.mjs</code>，"
        "或检查环境变量 BOND_DASHBOARD_AUTOSTART / BOND_DASHBOARD_UPSTREAM。"
    )
    html = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>信用债一级发行看板</title></head>"
        "<body style=\"font-family:'Microsoft YaHei',sans-serif;padding:60px 32px;"
        "color:#152238;background:#eef2f7;text-align:center\">"
        f"<h2 style=\"font-size:20px\">看板暂时不可用</h2><p style=\"color:#66758a\">{detail}</p>"
        "</body></html>"
    )
    try:
        html = _inject_dashboard_nav(html)
    except Exception:
        pass
    return Response(html, status=503, content_type="text/html; charset=utf-8")


def _upstream_request(target: str):
    headers = {}
    for key, value in request.headers.items():
        if key.lower() in SKIP_REQUEST_HEADERS:
            continue
        headers[key] = value
    # 明文请求上游，门户侧再统一 gzip，保证 HTML 可注入导航
    headers["Accept-Encoding"] = "identity"
    headers["X-Forwarded-Host"] = request.host
    body = None if request.method in ("GET", "HEAD") else request.get_data(cache=False)
    return _http.request(request.method, target, headers=headers, data=body, timeout=UPSTREAM_TIMEOUT)


def _relay(upstream_resp) -> Response:
    content_type = upstream_resp.headers.get("Content-Type", "")
    out_headers = []
    skip = SKIP_RESPONSE_HEADERS | {"etag", "last-modified"} if content_type.startswith("text/html") else SKIP_RESPONSE_HEADERS
    for key in upstream_resp.raw.headers.keys():
        if key.lower() in skip:
            continue
        for value in upstream_resp.raw.headers.getlist(key):
            out_headers.append((key, value))
    resp = Response(upstream_resp.content, status=upstream_resp.status_code, headers=out_headers)
    if content_type.startswith("text/html"):
        html = upstream_resp.content.decode("utf-8", errors="replace")
        try:
            html = _inject_dashboard_nav(html)
        except Exception:
            pass
        resp.set_data(html)
        # 注入后长度已变，禁用上游校验器，强制每次回源拿最新导航
        resp.headers["Cache-Control"] = "private, no-cache"
    return resp


@bp.route("/", defaults={"subpath": ""}, methods=FORWARD_METHODS)
@bp.route("/<path:subpath>", methods=FORWARD_METHODS)
def index(subpath: str):
    target = f"{UPSTREAM}/bond-dashboard/{subpath}"
    if request.query_string:
        target += "?" + request.query_string.decode("ascii", "replace")
    # 每请求只做一次回环 TCP 探测的成本也省掉：成功拉起过就直接转发，
    # 上游中途挂掉由下方异常分支强制重拉
    if not _started:
        ensure_server()
    try:
        return _relay(_upstream_request(target))
    except requests.RequestException:
        pass
    # 上游中途退出：强制重走一次拉起流程后重试，仍失败则给出可读提示页
    if not ensure_server(force=True):
        return _unavailable_page()
    try:
        return _relay(_upstream_request(target))
    except requests.RequestException:
        return _unavailable_page()
