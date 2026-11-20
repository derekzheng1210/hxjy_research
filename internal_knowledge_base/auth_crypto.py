# -*- coding: utf-8 -*-
"""登录凭据传输加密（渗透测试"传输过程信息泄漏"整改）。

内网 HTTP 明文环境下，登录/改密接口的密码与 SESSIONID 可被局域网嗅探
（渗透测试抓包复现）。本模块实现"挑战-响应 + RSA-OAEP 信封"：

1. 客户端 GET /api/auth-challenge 取一次性 nonce 与 RSA 公钥（n/e 十六进制）；
2. 客户端本地把 {用户名/密码字段…, nonce} JSON 序列化后 RSA-OAEP-SHA256 加密；
3. 服务端解密、校验 nonce（绑定会话、5 分钟有效、一次性消费）后走原有
   werkzeug 哈希校验——密码明文及任何密码等效摘要全程不出现在网络流量中，
   抓包重放也因 nonce 一次性而失效。

选择 RSA 信封而非"纯摘要传输"的原因：摘要方案要求服务端保存密码等效
摘要（DB 泄漏即可直接登入）或保留明文过渡期，反而引入新的风险面；RSA
信封对现有 werkzeug 哈希存储零改动。

RSA 私钥默认 4096 位（OAEP-SHA256 单信封上限 446 字节，覆盖改密双密码
字段），首次使用时生成并持久化到 SQLite settings 表，之后重启直接加载。
测试环境可用环境变量 KB_AUTH_KEY_BITS 调小以加速。内网 HTTP 属非安全
上下文、无 crypto.subtle，前端配套 js/crypto.js 自带纯 JS 实现。
"""

import base64
import json
import os
import secrets
import threading
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

NONCE_TTL_SECONDS = 300
MAX_PENDING_NONCES = 8
_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                      algorithm=hashes.SHA256(), label=None)
_SETTING_KEY = "auth_rsa_private_key_pem"

_lock = threading.Lock()
_private_key = None


def _key_bits():
    try:
        return int(os.environ.get("KB_AUTH_KEY_BITS", "4096"))
    except ValueError:
        return 4096


def _load_or_create(store):
    """从 settings 加载私钥，缺失则生成并持久化（进程内只做一次）。"""
    global _private_key
    with _lock:
        if _private_key is not None:
            return _private_key
        pem = store.get_auth_setting(_SETTING_KEY)
        if pem:
            _private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
            return _private_key
        _private_key = rsa.generate_private_key(public_exponent=65537, key_size=_key_bits())
        store.set_auth_setting(
            _SETTING_KEY,
            _private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8"))
        return _private_key


def ensure_key(store):
    """确保私钥可用；首次生成 4096 位可能耗时数秒到数十秒。"""
    return _load_or_create(store)


def start_background_init(store):
    """启动后台线程预生成私钥，避免首个登录用户同步等待。"""
    thread = threading.Thread(target=lambda: _safe_init(store), daemon=True, name="kb-auth-keygen")
    thread.start()


def _safe_init(store):
    try:
        _load_or_create(store)
    except Exception:
        # 后台失败不影响请求线程内的同步重试路径
        pass


def ready():
    return _private_key is not None


def public_key_hex():
    """公钥模数与指数（十六进制字符串），供前端纯 JS RSA 使用。"""
    if _private_key is None:
        return None
    numbers = _private_key.public_key().public_numbers()
    return {"n": format(numbers.n, "x"), "e": format(numbers.e, "x")}


def decrypt_credential(ciphertext_b64):
    """解密前端信封，返回明文 JSON dict。任何失败抛 ValueError。"""
    if _private_key is None or not ciphertext_b64 or not isinstance(ciphertext_b64, str):
        raise ValueError("credential unavailable")
    try:
        raw = base64.b64decode(ciphertext_b64, validate=True)
        plaintext = _private_key.decrypt(raw, _OAEP)
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")
        return payload
    except Exception as exc:
        raise ValueError(f"credential decrypt failed: {exc}") from exc


# ---- 一次性 nonce（绑定 Flask 会话） ----

def issue_nonce(session):
    """签发绑定当前会话的一次性 nonce，同时清理过期与超额条目。"""
    pending = dict(session.get("kb_auth_nonces") or {})
    now = time.time()
    pending = {n: exp for n, exp in pending.items() if exp > now}
    nonce = secrets.token_urlsafe(24)
    pending[nonce] = now + NONCE_TTL_SECONDS
    while len(pending) > MAX_PENDING_NONCES:
        pending.pop(next(iter(pending)))
    session["kb_auth_nonces"] = pending
    return nonce


def consume_nonce(session, nonce):
    """消费 nonce：存在且未过期则立即作废并返回 True。"""
    if not nonce or not isinstance(nonce, str):
        return False
    pending = dict(session.get("kb_auth_nonces") or {})
    expiry = pending.get(nonce)
    if expiry is None:
        return False
    del pending[nonce]
    session["kb_auth_nonces"] = pending
    return expiry > time.time()
