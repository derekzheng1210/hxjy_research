from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from . import dm_accounts
from .storage import save_snapshot

# 一次快照抓取最多尝试几个账号（每个账号失败后切换下一个）
MAX_ACCOUNT_ATTEMPTS = 3


def _fetch_with_account(account: dict[str, Any], timeout: int) -> dict:
    """用指定账号完成一次完整快照抓取并原子替换缓存。"""
    # dm_client_local 含接口逆向细节且被 .gitignore 排除，保持延迟导入，
    # 缺文件时错误能进入调度器状态而不是打断门户启动。
    from .dm_client_local import DMClient, choose_value, enrich_market_rows

    client = DMClient(
        account["username"],
        account["password"],
        timeout=timeout,
        device_id=account.get("device_id") or "",
    )
    try:
        client.login()
        quotes, _audit = client.fetch_partitioned_best_quotes(73_000, 450)
        if not quotes:
            raise RuntimeError("DM 未返回有效经纪商行情")
        bond_ids = [row.get("bondUniCode") for row in quotes if row.get("bondUniCode")]
        bond_info = client.fetch_bond_info(bond_ids)
        bonds_by_code = {}
        for item in bond_info:
            value = choose_value(item, ("bondUniCode", "bond_uni_code", "uniCode"))
            try:
                bonds_by_code[int(value)] = item
            except (TypeError, ValueError):
                continue
        enrich_market_rows(quotes, bonds_by_code)
        return save_snapshot(quotes, datetime.now())
    finally:
        # 回写本次设备标识，账号池下次沿用同一“设备”
        dm_accounts.update_device_id(account["username"], client.device_id)
        client.close()


def fetch_and_save_latest(timeout: int = 45) -> dict:
    """抓取一次完整 DM 最优报价快照：账号池轮换 + 失败自动切换。

    账号按“最久未成功使用优先”选取（摊薄单账号请求量），单个账号失败
    自动记录并切换下一个，全部失败时抛出最后一个错误由调度器重试。
    """
    attempts = dm_accounts.snapshot_attempt_order(MAX_ACCOUNT_ATTEMPTS)
    if not attempts:
        raise RuntimeError(
            "DM 账号未配置：请在后台“DM 经纪商行情账号”中录入账号，"
            "或在 .env 设置 DM_USERNAME / DM_PASSWORD"
        )
    last_error: Exception | None = None
    for account in attempts:
        try:
            result = _fetch_with_account(account, timeout)
            dm_accounts.mark_success(account["username"])
            result["dm_account"] = account["username"]
            return result
        except Exception as exc:
            last_error = exc
            dm_accounts.mark_failure(account["username"], f"{type(exc).__name__}: {exc}")
    raise last_error  # type: ignore[misc]


def test_connectivity(username: str | None = None) -> list[dict[str, Any]]:
    """后台连通性测试：登录 + 读取权限资源（均为只读接口，不产生行情抓取）。

    username 为空时测试全部启用中的账号；结果写回账号池的测试状态字段，
    不参与抓取熔断计数。
    """
    from .dm_client_local import DMClient, RESOURCE_PATH

    if username:
        account = dm_accounts.get_account(username)
        if not account:
            raise ValueError(f"账号不存在：{username}")
        if not account.get("enabled"):
            raise ValueError(f"账号已停用：{username}")
        targets = [account]
    else:
        targets = [item for item in dm_accounts.list_accounts() if item.get("enabled")]
        if not targets:
            raise ValueError("没有启用中的账号可供测试")

    results: list[dict[str, Any]] = []
    for account in targets:
        started = time.perf_counter()
        outcome: dict[str, Any] = {"username": account["username"]}
        client = None
        try:
            client = DMClient(
                account["username"], account["password"],
                device_id=account.get("device_id") or "",
            )
            client.login()
            try:
                permissions = client.get_json(RESOURCE_PATH)
                outcome["permission_count"] = _permission_count(permissions)
            except Exception:
                # 权限明细读取失败不视为不可用：登录会话本身已验证账号有效
                outcome["permission_count"] = None
            outcome["ok"] = True
            outcome["latency_ms"] = round((time.perf_counter() - started) * 1000)
            dm_accounts.record_test_result(
                account["username"], True, latency_ms=outcome["latency_ms"],
            )
        except Exception as exc:
            outcome["ok"] = False
            outcome["error"] = f"{type(exc).__name__}: {exc}"[:200]
            dm_accounts.record_test_result(account["username"], False, outcome["error"])
        finally:
            if client is not None:
                dm_accounts.update_device_id(account["username"], client.device_id)
                client.close()
        results.append(outcome)
    return results


def _permission_count(payload: Any) -> int | None:
    """粗略统计权限资源数量，仅供后台展示。"""
    try:
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            for key in ("data", "list", "resources", "items", "records"):
                value = payload.get(key)
                if isinstance(value, list):
                    return len(value)
    except Exception:
        pass
    return None
