from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from broker_market import storage


def snapshot(stamp: str, quotes: list[dict], version: str) -> dict:
    return {"generated_at": stamp, "version": version, "quotes": quotes}


def quote(code: str, ofr: float | None) -> dict:
    return {
        "code": code,
        "ofr_yield": ofr,
        "ofr_volume_text": "1000",
        "ofr_volume_value": 1000,
        "ofr_broker": "测试经纪商",
        "ofr_time": "09:30:00",
    }


class OfrMoverTests(unittest.TestCase):
    def write_history(self, directory: Path, name: str, payload: dict) -> None:
        storage.atomic_write_json(directory / name, payload)

    def history_directory(self) -> Path:
        directory = Path.cwd() / ".test_runtime" / "ofr_movers" / self._testMethodName
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def test_same_day_movers_include_boundary_and_exclude_invalid_quotes(self):
        previous = snapshot(
            "2026-09-02 09:30:00",
            [quote("A.IB", 2.00), quote("B.IB", 2.00), quote("C.IB", 2.00), quote("D.IB", 2.00)],
            "previous",
        )
        current = snapshot(
            "2026-09-02 10:00:00",
            [quote("A.IB", 2.02), quote("B.IB", 1.98), quote("C.IB", 2.01), quote("D.IB", 2.31)],
            "current",
        )
        rows = [
            ["A.IB", "上行券", 3, "AA", "主体A", 2.00],
            ["B.IB", "下行券", 3, "AA", "主体B", 2.00],
            ["C.IB", "小变动券", 3, "AA", "主体C", 2.00],
            ["D.IB", "异常券", 3, "AA", "主体D", 2.00],
        ]
        history = self.history_directory()
        self.write_history(history, "20260902_093000.json", previous)
        with patch.object(storage, "HISTORY_DIR", history):
            result = storage.calculate_ofr_movers(rows, current)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["baseline_at"], "2026-09-02 09:30:00")
        movers = {item["code"]: item for item in result["movers"]}
        self.assertEqual(movers["A.IB"]["delta_bp"], 2.0)
        self.assertEqual(movers["A.IB"]["direction"], "up")
        self.assertEqual(movers["B.IB"]["delta_bp"], -2.0)
        self.assertEqual(movers["B.IB"]["direction"], "down")
        self.assertEqual(movers["C.IB"]["delta_bp"], 1.0)
        self.assertNotIn("D.IB", movers)  # 相对中债偏离31BP，沿用异常报价清洗

    def test_requires_a_previous_snapshot_on_the_same_trade_day(self):
        current = snapshot("2026-09-02 09:30:00", [quote("A.IB", 2.02)], "current")
        yesterday = snapshot("2026-09-01 16:00:00", [quote("A.IB", 2.00)], "yesterday")
        history = self.history_directory()
        self.write_history(history, "20260901_160000.json", yesterday)
        with patch.object(storage, "HISTORY_DIR", history):
            result = storage.calculate_ofr_movers([["A.IB", "券", 3, "", "主体", 2.0]], current)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["baseline_at"], "2026-09-01 16:00:00")

    def test_comparison_modes_select_expected_snapshot(self):
        current = snapshot("2026-09-03 10:00:00", [quote("A.IB", 2.04)], "current")
        history = self.history_directory()
        self.write_history(history, "20260902_093000.json", snapshot("2026-09-02 09:30:00", [quote("A.IB", 2.00)], "prev-open"))
        self.write_history(history, "20260902_160000.json", snapshot("2026-09-02 16:00:00", [quote("A.IB", 2.01)], "prev-close"))
        self.write_history(history, "20260903_093000.json", snapshot("2026-09-03 09:30:00", [quote("A.IB", 2.03)], "today-open"))
        rows = [["A.IB", "券", 3, "", "主体", 2.0]]
        with patch.object(storage, "HISTORY_DIR", history):
            today_open = storage.calculate_ofr_movers(rows, current, "today_open")
            yesterday_close = storage.calculate_ofr_movers(rows, current, "previous_day_close")
            yesterday_open = storage.calculate_ofr_movers(rows, current, "previous_day_open")
            custom = storage.calculate_ofr_movers(rows, current, "custom", "2026-09-02 09:30:00")

        self.assertEqual(today_open["baseline_at"], "2026-09-03 09:30:00")
        self.assertEqual(yesterday_close["baseline_at"], "2026-09-02 16:00:00")
        self.assertEqual(yesterday_open["baseline_at"], "2026-09-02 09:30:00")
        self.assertEqual(custom["baseline_at"], "2026-09-02 09:30:00")
        self.assertGreaterEqual(len(custom["available_baselines"]), 3)

    def test_mover_preferences_have_safe_defaults_and_validate_input(self):
        defaults = storage.validate_preferences({})
        self.assertEqual(defaults["ofr_mover_settings"], {"threshold_bp": 2.0, "direction": "both", "comparison_mode": "previous_snapshot", "custom_baseline_at": ""})
        settings = storage.validate_preferences({"ofr_mover_settings": {"threshold_bp": 2, "direction": "up"}})
        self.assertEqual(settings["ofr_mover_settings"], {"threshold_bp": 2.0, "direction": "up", "comparison_mode": "previous_snapshot", "custom_baseline_at": ""})
        with self.assertRaises(ValueError):
            storage.validate_preferences({"ofr_mover_settings": {"threshold_bp": 101}})
        with self.assertRaises(ValueError):
            storage.validate_preferences({"ofr_mover_settings": {"direction": "sideways"}})
        with self.assertRaises(ValueError):
            storage.validate_preferences({"ofr_mover_settings": {"comparison_mode": "last_week"}})


if __name__ == "__main__":
    unittest.main()
