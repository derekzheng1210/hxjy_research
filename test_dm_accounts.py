from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault(
    "PORTAL_DATA_ROOT",
    str(Path(__file__).resolve().parent / ".test_runtime" / "dm_accounts"),
)

from broker_market import dm_accounts, fetcher  # noqa: E402

try:  # dm_client_local 被 .gitignore 排除，缺失时跳过依赖它的用例
    from broker_market import dm_client_local  # noqa: E402
except Exception:  # pragma: no cover
    dm_client_local = None


class DmAccountSandbox(unittest.TestCase):
    """每个用例使用独立的临时账号存储文件。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="dm_accounts_")
        self._path = Path(self._tmp) / "dm_accounts.json"
        patcher = patch.object(dm_accounts, "DM_ACCOUNTS_PATH", self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = patch.dict(os.environ, {"DM_USERNAME": "", "DM_PASSWORD": ""})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class DmAccountStoreTests(DmAccountSandbox):
    def test_add_update_delete_roundtrip(self) -> None:
        account = dm_accounts.add_account("user1", "pw1", "备注")
        self.assertEqual(account["username"], "user1")
        self.assertTrue(account["enabled"])

        with self.assertRaises(ValueError):
            dm_accounts.add_account("user1", "pw2")  # 重复
        with self.assertRaises(ValueError):
            dm_accounts.add_account("bad name!", "pw")  # 非法字符
        with self.assertRaises(ValueError):
            dm_accounts.add_account("user2", "")  # 空密码

        updated = dm_accounts.update_account("user1", enabled=False, note="新备注")
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["note"], "新备注")

        # 密码变更后设备标识作废
        dm_accounts.update_device_id("user1", "dev-abc")
        dm_accounts.update_account("user1", password="pw3")
        self.assertEqual(dm_accounts.get_account("user1")["device_id"], "")

        dm_accounts.delete_account("user1")
        self.assertIsNone(dm_accounts.get_account("user1"))
        with self.assertRaises(ValueError):
            dm_accounts.delete_account("user1")

    def test_overview_masks_passwords(self) -> None:
        dm_accounts.add_account("user1", "secret-pw")
        view = dm_accounts.overview()
        self.assertEqual(view["total_count"], 1)
        payload_text = repr(view)
        self.assertNotIn("secret-pw", payload_text)
        self.assertTrue(view["accounts"][0]["has_password"])
        self.assertNotIn("password", view["accounts"][0])

    def test_rotation_picks_least_recently_successful(self) -> None:
        dm_accounts.add_account("ua", "pwa")
        dm_accounts.add_account("ub", "pwb")
        dm_accounts.add_account("uc", "pwc")
        dm_accounts.mark_success("ua")
        dm_accounts.mark_success("ub")
        order = [item["username"] for item in dm_accounts.snapshot_attempt_order()]
        # c 从未成功过排最前，其后按最近成功时间升序（b 比 a 新，a 在前）
        self.assertEqual(order, ["uc", "ua", "ub"])

    def test_attempt_order_respects_max_attempts(self) -> None:
        for name in ("ua", "ub", "uc", "ud"):
            dm_accounts.add_account(name, "pw")
        order = dm_accounts.snapshot_attempt_order(max_attempts=2)
        self.assertEqual(len(order), 2)

    def test_env_fallback_appended_last(self) -> None:
        dm_accounts.add_account("pooled", "pw")
        with patch.dict(os.environ, {"DM_USERNAME": "envuser", "DM_PASSWORD": "envpw"}):
            order = [item["username"] for item in dm_accounts.snapshot_attempt_order()]
        self.assertEqual(order, ["pooled", "envuser"])

        # 池内账号与环境变量同名时不重复
        dm_accounts.add_account("envuser", "pw2")
        with patch.dict(os.environ, {"DM_USERNAME": "envuser", "DM_PASSWORD": "envpw"}):
            order = [item["username"] for item in dm_accounts.snapshot_attempt_order()]
        self.assertEqual(order, ["pooled", "envuser"])

    def test_env_fallback_not_appended_when_pool_account_disabled(self) -> None:
        # 环境变量账号录入池内后即使被停用/熔断，也不再作为兜底绕过池的管理
        dm_accounts.add_account("pooled", "pw")
        dm_accounts.add_account("envuser", "pw2")
        dm_accounts.update_account("envuser", enabled=False)
        with patch.dict(os.environ, {"DM_USERNAME": "envuser", "DM_PASSWORD": "envpw"}):
            order = [item["username"] for item in dm_accounts.snapshot_attempt_order()]
            view = dm_accounts.overview()
        self.assertEqual(order, ["pooled"])
        self.assertTrue(view["env_fallback"]["in_pool"])
        self.assertFalse(view["env_fallback"]["active"])

    def test_circuit_breaker_disables_after_threshold(self) -> None:
        dm_accounts.add_account("ua", "pw")
        dm_accounts.mark_failure("ua", "e1")
        dm_accounts.mark_failure("ua", "e2")
        self.assertEqual(len(dm_accounts.usable_accounts()), 1)
        dm_accounts.mark_failure("ua", "e3")
        # 连续失败达到 3 次：熔断，不再参与抓取
        self.assertEqual(dm_accounts.usable_accounts(), [])
        item = dm_accounts.get_account("ua")
        self.assertTrue(item["auto_disabled"])
        self.assertIn("ua", dm_accounts.overview()["accounts"][0]["username"])
        self.assertFalse(dm_accounts.overview()["accounts"][0]["usable"])

        # 重置计数后恢复
        dm_accounts.update_account("ua", reset_failures=True)
        self.assertEqual(len(dm_accounts.usable_accounts()), 1)

    def test_circuit_breaker_auto_recovers_after_cooldown(self) -> None:
        dm_accounts.add_account("ua", "pw")
        for index in range(dm_accounts.AUTO_DISABLE_THRESHOLD):
            dm_accounts.mark_failure("ua", f"e{index}")
        self.assertEqual(dm_accounts.usable_accounts(), [])

        # 把熔断时间改到冷却期之前：应恢复可试
        accounts = dm_accounts.list_accounts()
        accounts[0]["auto_disabled_at"] = (
            datetime.now() - timedelta(minutes=dm_accounts.AUTO_RECOVER_MINUTES + 1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with patch.object(dm_accounts, "_load", return_value=accounts):
            usable = dm_accounts.usable_accounts()
        self.assertEqual(len(usable), 1)

    def test_success_resets_failure_streak(self) -> None:
        dm_accounts.add_account("ua", "pw")
        dm_accounts.mark_failure("ua", "e1")
        dm_accounts.mark_failure("ua", "e2")
        dm_accounts.mark_success("ua")
        item = dm_accounts.get_account("ua")
        self.assertEqual(item["consecutive_failures"], 0)
        self.assertTrue(item["last_success"])

    def test_record_test_result_does_not_affect_breaker(self) -> None:
        dm_accounts.add_account("ua", "pw")
        dm_accounts.record_test_result("ua", False, "boom")
        dm_accounts.record_test_result("ua", True, latency_ms=123)
        item = dm_accounts.get_account("ua")
        self.assertEqual(item["consecutive_failures"], 0)
        self.assertTrue(item["last_test_ok"])
        self.assertEqual(item["last_test_latency_ms"], 123)


@unittest.skipIf(dm_client_local is None, "dm_client_local.py 不在本机（被 .gitignore 排除）")
class FetcherFailoverTests(DmAccountSandbox):
    class StubClient:
        instances: list["FetcherFailoverTests.StubClient"] = []

        def __init__(self, username: str, password: str, timeout: int = 45, device_id: str = ""):
            self.username = username
            self.password = password
            self.device_id = device_id or f"dev-{username}"
            FetcherFailoverTests.StubClient.instances.append(self)

        def login(self) -> None:
            if self.password == "bad":
                raise RuntimeError("DM 登录失败（HTTP 401）")

        def fetch_partitioned_best_quotes(self, *args, **kwargs):
            return [{"bondUniCode": 1, "dataId": "d1"}], []

        def fetch_bond_info(self, bond_ids):
            return []

        def close(self) -> None:
            pass

    def setUp(self) -> None:
        super().setUp()
        FetcherFailoverTests.StubClient.instances = []

    def test_failover_to_next_account(self) -> None:
        # 全新账号按录入顺序参与轮换：broken 排第一，失败后切换 good1
        dm_accounts.add_account("broken", "bad")
        dm_accounts.add_account("good1", "good")
        dm_accounts.add_account("good2", "good")

        saved = {}

        def fake_save_snapshot(rows, generated_at=None):
            saved["rows"] = list(rows)
            return {"quote_count": len(rows), "quotes": list(rows)}

        with patch.object(dm_client_local, "DMClient", self.StubClient), \
             patch.object(fetcher, "save_snapshot", fake_save_snapshot):
            result = fetcher.fetch_and_save_latest(timeout=5)

        self.assertEqual(result["dm_account"], "good1")
        self.assertEqual(result["quote_count"], 1)
        # 失败账号被记录一次失败且不影响其他账号
        self.assertEqual(dm_accounts.get_account("broken")["consecutive_failures"], 1)
        self.assertEqual(dm_accounts.get_account("good1")["consecutive_failures"], 0)
        # 使用过的账号回写了持久设备标识
        self.assertTrue(dm_accounts.get_account("good1")["device_id"])

    def test_all_accounts_failed_raises_last_error(self) -> None:
        dm_accounts.add_account("ua", "bad")
        dm_accounts.add_account("ub", "bad")

        with patch.object(dm_client_local, "DMClient", self.StubClient), \
             patch.object(fetcher, "save_snapshot", lambda *a, **k: {}):
            with self.assertRaises(RuntimeError):
                fetcher.fetch_and_save_latest(timeout=5)
        self.assertEqual(dm_accounts.get_account("ua")["consecutive_failures"], 1)
        self.assertEqual(dm_accounts.get_account("ub")["consecutive_failures"], 1)

    def test_no_accounts_uses_env_fallback(self) -> None:
        with patch.dict(os.environ, {"DM_USERNAME": "envuser", "DM_PASSWORD": "good"}):
            with patch.object(dm_client_local, "DMClient", self.StubClient), \
                 patch.object(fetcher, "save_snapshot", lambda rows, generated_at=None: {"quote_count": len(rows)}):
                result = fetcher.fetch_and_save_latest(timeout=5)
        self.assertEqual(result["dm_account"], "envuser")

    def test_no_accounts_at_all_raises_clear_error(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            fetcher.fetch_and_save_latest(timeout=5)
        self.assertIn("DM 账号未配置", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
