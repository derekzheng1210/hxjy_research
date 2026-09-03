# -*- coding: utf-8 -*-
"""DM 经纪商行情多账号池：存储、轮换与熔断。

设计要点：
- 账号存储在运行态数据目录（PORTAL_DATA_ROOT/data/broker_market/dm_accounts.json），
  与代码分离；密码仅落盘该文件（与 .env 明文口径一致），任何接口返回都不带密码。
- 抓取时按“最久未成功使用优先”轮换账号，把单账号请求量摊薄到整个账号池。
- 连续失败达到阈值自动熔断（auto_disabled），冷却期后自动恢复试运行，
  避免对已失效账号反复撞登录/接口（这也是最常见的封号诱因）。
- 环境变量 DM_USERNAME / DM_PASSWORD 始终作为兜底账号（排在所有池内账号之后），
  未在后台录入任何账号时行为与旧版单账号完全一致。
- 本模块不 import dm_client_local（该文件含接口逆向细节且被 .gitignore 排除），
  门户进程 import broker_market 时不会因缺文件而失败。
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from typing import Any

from .storage import MARKET_DIR, atomic_write_json, load_json

DM_ACCOUNTS_PATH = MARKET_DIR / "dm_accounts.json"

# 连续抓取失败达到该次数后自动熔断（后台可手动重新启用）
AUTO_DISABLE_THRESHOLD = 3
# 熔断冷却时长：超过后视为可再试（转 auto_disabled=False 由下次抓取重新评估）
AUTO_RECOVER_MINUTES = 360

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{2,64}$")

_lock = threading.RLock()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _load() -> list[dict[str, Any]]:
    payload = load_json(DM_ACCOUNTS_PATH, {"accounts": []})
    accounts = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(accounts, list):
        return []
    return [item for item in accounts if isinstance(item, dict) and item.get("username")]


def _save(accounts: list[dict[str, Any]]) -> None:
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(DM_ACCOUNTS_PATH, {"accounts": accounts})


def get_account(username: str) -> dict[str, Any] | None:
    with _lock:
        for item in _load():
            if item.get("username") == username:
                return item
    return None


def list_accounts() -> list[dict[str, Any]]:
    with _lock:
        return _load()


def add_account(username: str, password: str, note: str = "") -> dict[str, Any]:
    username = str(username or "").strip()
    password = str(password or "")
    if not _USERNAME_RE.match(username):
        raise ValueError("账号名仅支持 2-64 位字母、数字及 _ . @ - 字符")
    if not password:
        raise ValueError("密码不能为空")
    with _lock:
        accounts = _load()
        if any(item["username"] == username for item in accounts):
            raise ValueError(f"账号已存在：{username}")
        account = {
            "username": username,
            "password": password,
            "enabled": True,
            "note": str(note or "")[:200],
            "created_at": _now_text(),
            "device_id": "",
            "last_used": "",
            "last_success": "",
            "last_failure": "",
            "last_error": "",
            "consecutive_failures": 0,
            "auto_disabled": False,
            "auto_disabled_at": "",
            "last_tested": "",
            "last_test_ok": None,
            "last_test_error": "",
        }
        accounts.append(account)
        _save(accounts)
        return dict(account)


def update_account(username: str, **fields: Any) -> dict[str, Any]:
    """按字段更新账号：enabled / note / password / reset_failures。"""
    with _lock:
        accounts = _load()
        for item in accounts:
            if item["username"] != username:
                continue
            if "enabled" in fields:
                item["enabled"] = bool(fields["enabled"])
            if "note" in fields:
                item["note"] = str(fields["note"] or "")[:200]
            if "password" in fields:
                if not str(fields["password"] or ""):
                    raise ValueError("密码不能为空")
                item["password"] = str(fields["password"])
                # 密码变更后旧会话与设备标识一并作废
                item["device_id"] = ""
            if fields.get("reset_failures"):
                item["consecutive_failures"] = 0
                item["auto_disabled"] = False
                item["auto_disabled_at"] = ""
                item["last_error"] = ""
            _save(accounts)
            return dict(item)
    raise ValueError(f"账号不存在：{username}")


def delete_account(username: str) -> None:
    with _lock:
        accounts = _load()
        remaining = [item for item in accounts if item["username"] != username]
        if len(remaining) == len(accounts):
            raise ValueError(f"账号不存在：{username}")
        _save(remaining)


def update_device_id(username: str, device_id: str) -> None:
    """抓取完成后回写本次使用的设备标识，保持同一账号长期复用同一设备。"""
    if not device_id:
        return
    with _lock:
        accounts = _load()
        for item in accounts:
            if item["username"] == username:
                if item.get("device_id") != device_id:
                    item["device_id"] = device_id
                    _save(accounts)
                return


def env_fallback_account() -> dict[str, Any] | None:
    username = os.environ.get("DM_USERNAME", "").strip()
    password = os.environ.get("DM_PASSWORD", "")
    if not username or not password:
        return None
    return {
        "username": username, "password": password,
        "enabled": True, "note": "环境变量兜底（DM_USERNAME / DM_PASSWORD）",
    }


def _usable(item: dict[str, Any]) -> bool:
    if not item.get("enabled"):
        return False
    if item.get("auto_disabled"):
        disabled_at = _parse(item.get("auto_disabled_at"))
        if disabled_at and (datetime.now() - disabled_at).total_seconds() < AUTO_RECOVER_MINUTES * 60:
            return False
    return True


def usable_accounts() -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _load() if _usable(item)]


def snapshot_attempt_order(max_attempts: int = 3) -> list[dict[str, Any]]:
    """返回一次快照抓取应依次尝试的账号序列。

    池内可用账号按“最久未成功使用优先”排序（轮换摊薄单账号负载），
    环境变量兜底账号排在最后；最多取 max_attempts 个。
    环境变量账号一旦录入池内（无论启停/熔断状态），就完全由池管理，
    不再作为兜底重复参与，避免绕过启停与熔断控制。
    """
    with _lock:
        accounts = _load()
        candidates = [dict(item) for item in accounts if _usable(item)]
        pool_usernames = {item["username"] for item in accounts}

        def sort_key(item: dict[str, Any]) -> tuple:
            success = _parse(item.get("last_success"))
            used = _parse(item.get("last_used"))
            return (success or datetime.min, used or datetime.min)

        candidates.sort(key=sort_key)
    fallback = env_fallback_account()
    if fallback and fallback["username"] not in pool_usernames:
        candidates.append(fallback)
    return candidates[:max(1, max_attempts)]


def mark_success(username: str) -> None:
    with _lock:
        accounts = _load()
        for item in accounts:
            if item["username"] == username:
                now = _now_text()
                item["last_used"] = now
                item["last_success"] = now
                item["consecutive_failures"] = 0
                item["auto_disabled"] = False
                item["auto_disabled_at"] = ""
                item["last_error"] = ""
                _save(accounts)
                return
    # 环境变量兜底账号不在池内，无需记录。


def mark_failure(username: str, error: str) -> None:
    with _lock:
        accounts = _load()
        for item in accounts:
            if item["username"] == username:
                now = _now_text()
                item["last_used"] = now
                item["last_failure"] = now
                item["last_error"] = str(error or "")[:300]
                item["consecutive_failures"] = int(item.get("consecutive_failures") or 0) + 1
                if item["consecutive_failures"] >= AUTO_DISABLE_THRESHOLD and not item.get("auto_disabled"):
                    item["auto_disabled"] = True
                    item["auto_disabled_at"] = now
                _save(accounts)
                return


def record_test_result(username: str, ok: bool, error: str = "", latency_ms: int | None = None) -> None:
    """记录后台连通性测试结果；只影响展示，不参与抓取熔断计数。"""
    with _lock:
        accounts = _load()
        for item in accounts:
            if item["username"] == username:
                item["last_tested"] = _now_text()
                item["last_test_ok"] = bool(ok)
                item["last_test_error"] = str(error or "")[:300] if not ok else ""
                if latency_ms is not None:
                    item["last_test_latency_ms"] = int(latency_ms)
                _save(accounts)
                return
    raise ValueError(f"账号不存在：{username}")


def _masked(item: dict[str, Any]) -> dict[str, Any]:
    view = {key: value for key, value in item.items() if key != "password"}
    view["has_password"] = bool(item.get("password"))
    view["usable"] = _usable(item)
    return view


def overview() -> dict[str, Any]:
    """后台账号管理页数据：账号列表（脱敏）+ 池状态摘要。"""
    with _lock:
        accounts = _load()
    usable = [item for item in accounts if _usable(item)]
    fallback = env_fallback_account()
    env_in_pool = bool(
        fallback and any(item["username"] == fallback["username"] for item in accounts)
    )
    if fallback and env_in_pool:
        env_note = "该账号已录入池内，与其他账号一同轮换，启停与熔断以池内设置为准"
    else:
        env_note = "未录入池内可用账号时，抓取回退到 .env 的 DM_USERNAME / DM_PASSWORD"
    return {
        "accounts": [_masked(item) for item in accounts],
        "usable_count": len(usable),
        "total_count": len(accounts),
        "auto_disable_threshold": AUTO_DISABLE_THRESHOLD,
        "auto_recover_minutes": AUTO_RECOVER_MINUTES,
        "env_fallback": {
            "username": fallback["username"] if fallback else "",
            "in_pool": env_in_pool,
            "note": env_note,
            "active": bool(fallback and not usable and not env_in_pool),
        } if fallback else None,
    }
