from __future__ import annotations

import math
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault(
    "PORTAL_DATA_ROOT",
    str(Path(__file__).resolve().parent / ".test_runtime" / "dm_deals"),
)

from broker_market import dm_deals  # noqa: E402

PAST_DAY = "20260817"
PAST_DAY2 = "20260818"


def _stats_row(day: str, num: int, gvn: int = 0, tkn: int = 0) -> dict:
    return {
        "issue_date": f"{day[:4]}-{day[4:6]}-{day[6:8]}",
        "trading_num": float(num),
        "gvn_trade_num": float(gvn),
        "tkn_trade_num": float(tkn),
        "trd_trade_num": 0.0,
        "yield": 2.01,
        "high_yield": 2.02,
        "low_yield": 2.0,
        "cb_ytm": 2.005,
        "yield_sub_cb": 0.0005,
    }


def _bar(time: str, yield_v, price_v, num=1, gvn=0, tkn=1) -> dict:
    return {
        "issue_time": time,
        "close_yield": yield_v,
        "close_net_price": price_v,
        "trade_num": num,
        "gvn_trade_num": gvn,
        "tkn_trade_num": tkn,
        "trd_trade_num": 0,
    }


class DmDealsSandbox(unittest.TestCase):
    """每个用例独立的临时缓存目录 + 独立响应缓存。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="dm_deals_")
        self._dir_patcher = patch.object(dm_deals, "_deals_dir", Path(self._tmp))
        self._dir_patcher.start()
        self._cache_patcher = patch.object(dm_deals, "_payload_cache", {})
        self._cache_patcher.start()
        env = {k: "" for k in ("INNO_APP_KEY", "INNO_APP_SECRET")}
        self._env_patcher = patch.dict(os.environ, env)
        self._env_patcher.start()
        self.addCleanup(self._dir_patcher.stop)
        self.addCleanup(self._cache_patcher.stop)
        self.addCleanup(self._env_patcher.stop)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))


class DmDealsHelperTests(DmDealsSandbox):
    def test_clean_bars_row_drops_no_trade_and_nan(self):
        self.assertIsNone(dm_deals._clean_bars_row(_bar("10:00", None, None, num=0)))
        # NaN 收益率但有净价：保留
        row = dm_deals._clean_bars_row(_bar("10:00", math.nan, 100.5))
        self.assertEqual(row["net_price"], 100.5)
        self.assertIsNone(row["yield"])
        # 全 NaN：丢弃
        self.assertIsNone(dm_deals._clean_bars_row(_bar("10:00", math.nan, math.nan, num=2)))

    def test_summary_from_minutes(self):
        minutes = [
            dm_deals._clean_bars_row(_bar("09:30", 2.0, 100.0, num=2, gvn=2)),
            dm_deals._clean_bars_row(_bar("14:00", 2.05, 99.9, num=1, tkn=1)),
        ]
        summary = dm_deals._summary_from_minutes(minutes)
        self.assertEqual(summary["num"], 3)
        self.assertEqual(summary["gvn"], 2)
        self.assertEqual(summary["close_yield"], 2.05)
        self.assertEqual(summary["high_yield"], 2.05)
        self.assertEqual(summary["low_yield"], 2.0)

    def test_iter_chunks_splits_by_90_days(self):
        start = "20260101"
        end = "20260410"  # 100 个自然日
        chunks = dm_deals._iter_chunks(start, end)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], ("20260101", "20260331"))
        self.assertEqual(chunks[1], ("20260401", "20260410"))


class DmDealsBuildTests(DmDealsSandbox):
    def _fake_post(self, stats_days, bars_by_day):
        calls = {"n": 0}

        def fake(payload, api_path):
            calls["n"] += 1
            if api_path == dm_deals._DATE_PATH:
                return [
                    _stats_row(day, *spec) for day, spec in stats_days.items()
                    if str(payload.get("start_date")).replace("-", "") <= day <= str(payload.get("end_date")).replace("-", "")
                ]
            day = str(payload.get("start_datetime")).replace("-", "")
            return bars_by_day.get(day, [])

        return fake, calls

    def test_build_assembles_stats_and_minutes(self):
        stats_days = {PAST_DAY: (3, 1, 2), PAST_DAY2: (0, 0, 0)}
        bars = {
            PAST_DAY: [
                _bar("10:17:00", 2.01, 100.28, num=1, gvn=1),
                _bar("13:28:00", math.nan, math.nan, num=0),  # 无成交行：丢弃
                _bar("14:43:00", 2.0, 100.35, num=2, tkn=2),
            ],
        }
        fake, calls = self._fake_post(stats_days, bars)
        with patch.object(dm_deals, "_post", fake):
            payload = dm_deals.build_deal_history(
                "102581808.IB", name="25电网MTN019",
                range_start=PAST_DAY, range_end=PAST_DAY2, trading_days=2,
            )
        self.assertEqual(len(payload["days"]), 1)  # 无成交日不进列表
        day = payload["days"][0]
        self.assertEqual(day["date"], "2026-08-17")
        self.assertEqual(day["num"], 3)
        self.assertEqual(day["dev_bp"], 0.1)
        self.assertEqual(len(day["minutes"]), 2)
        self.assertEqual(day["minutes"][0]["time"], "10:17")
        self.assertEqual(payload["summary"]["num"], 3)
        self.assertEqual(payload["summary"]["tkn"], 2)
        self.assertEqual(payload["summary"]["days"], 1)
        self.assertTrue(payload["version"])
        # 二次构建全命中缓存：不再调API，版本不变
        with patch.object(dm_deals, "_post", fake):
            again = dm_deals.build_deal_history(
                "102581808.IB", range_start=PAST_DAY, range_end=PAST_DAY2,
            )
        self.assertEqual(again["version"], payload["version"])
        self.assertEqual(calls["n"], 2)  # 首轮：date序列1次 + bars 1次

    def test_build_includes_today_from_minutes(self):
        today = date.today().strftime("%Y%m%d")
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        stats_days = {yesterday: (1, 1, 0)}
        bars = {
            yesterday: [_bar("10:00:00", 2.0, 100.0, num=1, gvn=1)],
            today: [
                _bar("09:45:00", 1.98, 101.0, num=2, gvn=2),
                _bar("10:05:00", 1.985, 100.9, num=1, tkn=1),
            ],
        }
        fake, calls = self._fake_post(stats_days, bars)
        with patch.object(dm_deals, "_post", fake):
            payload = dm_deals.build_deal_history(
                "102581808.IB", range_start=yesterday, range_end=today,
            )
        dates = [d["date"] for d in payload["days"]]
        self.assertEqual(dates, [f"{yesterday[:4]}-{yesterday[4:6]}-{yesterday[6:]}", f"{today[:4]}-{today[4:6]}-{today[6:]}"])
        today_day = payload["days"][-1]
        self.assertTrue(today_day["is_today"])
        self.assertEqual(today_day["num"], 3)
        self.assertEqual(today_day["close_yield"], 1.985)
        self.assertIsNone(today_day["dev_bp"])  # 当日无估值偏离，前端补算
        self.assertEqual(payload["summary"]["num"], 4)
        # 当日TTL内二次构建：命中缓存不再调API
        with patch.object(dm_deals, "_post", fake):
            dm_deals.build_deal_history("102581808.IB", range_start=yesterday, range_end=today)
        self.assertEqual(calls["n"], 3)  # date1 + bars昨日 + bars今日

    def test_build_without_any_stats_rows(self):
        fake, _ = self._fake_post({}, {})
        with patch.object(dm_deals, "_post", fake):
            payload = dm_deals.build_deal_history(
                "102681898.IB", range_start=PAST_DAY, range_end=PAST_DAY2,
            )
        self.assertEqual(payload["days"], [])
        self.assertEqual(payload["summary"]["num"], 0)

    def test_build_rejects_inverted_range(self):
        with self.assertRaises(ValueError):
            dm_deals.build_deal_history("102581808.IB", range_start=PAST_DAY2, range_end=PAST_DAY)


class DmDealsCredentialTests(DmDealsSandbox):
    def test_unavailable_without_credentials(self):
        self.assertFalse(dm_deals.deals_configured())
        with self.assertRaises(dm_deals.DealsUnavailable) as ctx:
            dm_deals.build_deal_history("102581808.IB", range_start=PAST_DAY, range_end=PAST_DAY2)
        self.assertIn("未配置", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
