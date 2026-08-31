# -*- coding: utf-8 -*-
"""大模型 provider 注册表 + 后台可配置优先级 + 连通性探测。

四个模型（OpenAI 兼容协议）：
- self               自部署模型（阿里云网关，内网直连）
- mimo               MiMo（小米开放平台，公网）
- deepseek           DeepSeek 官方 API（公网）
- deepseek_internal  DeepSeek 内网部署（vLLM/网关自建，密钥可缺省）

优先级持久化在数据目录 llm_settings.json，由后台"大模型管理"卡片维护；
知识库调用链每次请求重新读取，调整优先级即时生效、无需重启。
密钥不写入文件：优先读环境变量，Windows 下回退读注册表 HKCU\\Environment。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

from paths import DATA_DIR

SETTINGS_FILE = DATA_DIR / "llm_settings.json"
TEST_TIMEOUT = 20  # 连通性探测超时（秒），比业务调用短
DEFAULT_PRIORITY = ["self", "deepseek_internal", "mimo", "deepseek"]

_lock = threading.Lock()


def _env_or_registry(name):
    r"""Resolve an env var with a Windows HKCU\Environment registry fallback."""
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env_key:
            return str(winreg.QueryValueEx(env_key, name)[0]).strip()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# Provider 定义。key_source 之外的属性直接进 provider dict，
# 与 internal_knowledge_base/routes.py 的请求构造（json_mode / thinking 等）保持一致。
# --------------------------------------------------------------------------- #
def _provider_defs() -> list[dict]:
    self_key = _env_or_registry("SELF_LLM_API_KEY")
    mimo_key = _env_or_registry("MIMO_API_KEY")
    deepseek_key = _env_or_registry("DEEPSEEK_API_KEY")
    internal_base = os.environ.get("DEEPSEEK_INTERNAL_BASE_URL", "").strip()
    internal_key = _env_or_registry("DEEPSEEK_INTERNAL_API_KEY")
    return [
        {
            "id": "self", "name": "自部署模型", "kind": "内网",
            "base_url": os.environ.get("SELF_LLM_BASE_URL", "http://10.9.50.201:3005/v1").rstrip("/"),
            "model": os.environ.get("SELF_LLM_MODEL", "glm-5.2"),
            "api_key": self_key, "configured": bool(self_key),
            "key_hint": "SELF_LLM_API_KEY",
            "json_mode": False,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        {
            "id": "deepseek_internal", "name": "DeepSeek 内网部署", "kind": "内网",
            "base_url": internal_base.rstrip("/"),
            "model": os.environ.get("DEEPSEEK_INTERNAL_MODEL", "deepseek-chat"),
            # 内网 vLLM 部署常不校验密钥，配置了地址即视为可用，密钥缺省发 EMPTY
            "api_key": internal_key or "EMPTY", "configured": bool(internal_base),
            "key_hint": "DEEPSEEK_INTERNAL_BASE_URL",
            "json_mode": True,
        },
        {
            "id": "mimo", "name": "MiMo", "kind": "公网",
            "base_url": os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").rstrip("/"),
            "model": os.environ.get("MIMO_MODEL", "mimo-v2.5"),
            "api_key": mimo_key, "configured": bool(mimo_key),
            "key_hint": "MIMO_API_KEY",
            "json_mode": True, "disable_thinking": True,
        },
        {
            "id": "deepseek", "name": "DeepSeek 官方", "kind": "公网",
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "api_key": deepseek_key, "configured": bool(deepseek_key),
            "key_hint": "DEEPSEEK_API_KEY",
            "json_mode": True,
        },
    ]


def get_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    priority = [p for p in data.get("priority") or [] if p in DEFAULT_PRIORITY]
    for pid in DEFAULT_PRIORITY:
        if pid not in priority:
            priority.append(pid)
    return {"priority": priority}


def save_priority(priority: list[str]) -> dict:
    ordered = [p for p in priority if p in DEFAULT_PRIORITY]
    for pid in DEFAULT_PRIORITY:
        if pid not in ordered:
            ordered.append(pid)
    settings = {"priority": ordered}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".llm_settings-", dir=SETTINGS_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False)
        os.replace(tmp_name, SETTINGS_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return settings


def available_providers() -> list[dict]:
    """按后台配置的优先级返回当前可用（已配置密钥/地址）的 provider 列表。"""
    priority = get_settings()["priority"]
    order = {pid: idx for idx, pid in enumerate(priority)}
    providers = [p for p in _provider_defs() if p["configured"]]
    providers.sort(key=lambda p: order.get(p["id"], 99))
    return providers


def llm_api_key() -> str:
    """任一模型可用即视为大模型已配置（兼容旧判断入口）。"""
    for provider in _provider_defs():
        if provider["configured"] and provider["api_key"]:
            return provider["api_key"]
    return ""


def provider_overview() -> list[dict]:
    """给后台展示用的全量 provider 清单（按当前优先级排序，含未配置项）。"""
    priority = get_settings()["priority"]
    order = {pid: idx for idx, pid in enumerate(priority)}
    defs = _provider_defs()
    defs.sort(key=lambda p: order.get(p["id"], 99))
    return [
        {
            "id": p["id"], "name": p["name"], "kind": p["kind"],
            "base_url": p["base_url"] or "未配置", "model": p["model"],
            "configured": p["configured"], "key_hint": p["key_hint"],
        }
        for p in defs
    ]


def test_provider(provider_id: str) -> dict:
    """对单个 provider 发一次最小 chat 请求，返回连通性结果。"""
    provider = next((p for p in _provider_defs() if p["id"] == provider_id), None)
    if provider is None:
        return {"id": provider_id, "ok": False, "error": "未知模型"}
    if not provider["configured"]:
        return {"id": provider_id, "ok": False, "error": f"未配置（{provider['key_hint']}）"}
    payload = {
        "model": provider["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    if provider.get("disable_thinking"):
        payload["thinking"] = {"type": "disabled"}
    if provider.get("chat_template_kwargs"):
        payload["chat_template_kwargs"] = provider["chat_template_kwargs"]
    req = urllib.request.Request(
        provider["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {provider['api_key']}"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.time()
    try:
        with opener.open(req, timeout=TEST_TIMEOUT) as resp:
            json.loads(resp.read().decode("utf-8"))
        return {"id": provider_id, "ok": True, "latency_ms": int((time.time() - started) * 1000), "error": ""}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:160]
        except Exception:
            pass
        return {"id": provider_id, "ok": False,
                "latency_ms": int((time.time() - started) * 1000),
                "error": f"HTTP {exc.code}{(': ' + detail) if detail else ''}"}
    except Exception as exc:
        return {"id": provider_id, "ok": False,
                "latency_ms": int((time.time() - started) * 1000),
                "error": str(exc)[:200]}


def test_all_providers() -> list[dict]:
    """并行探测全部模型，按当前优先级顺序返回结果。"""
    overview = provider_overview()
    results = {p["id"]: None for p in overview}
    threads = []

    def run(pid):
        results[pid] = test_provider(pid)

    for p in overview:
        t = threading.Thread(target=run, args=(p["id"],), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(TEST_TIMEOUT + 5)
    return [results.get(p["id"]) or {"id": p["id"], "ok": False, "error": "探测超时"}
            for p in overview]
