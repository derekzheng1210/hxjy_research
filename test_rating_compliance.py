# -*- coding: utf-8 -*-
"""合规630主体存续有效评级（研报口径）判定逻辑单测。"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PORTAL_DATA_ROOT", str(Path(__file__).resolve().parent / ".test_runtime" / "rating"))

from juyuan_update.rating_compliance import (
    AGENCY_ALIASES,
    build_issuer_rating_status,
    classify_termination_title,
    compute_issuer_rating_status,
    evaluate_rating_compliance,
    persist_rating_facts,
)

AS_OF = date(2026, 9, 23)
ISSUER = "南京地铁集团有限公司"


class TerminationTitleClassificationTests(unittest.TestCase):
    def test_agency_full_name_prefix(self):
        kind, who = classify_termination_title(
            "联合资信评估股份有限公司关于终止南京地铁集团有限公司主体及相关债项信用评级的公告",
            ISSUER,
        )
        self.assertEqual((kind, who), ("agency", "联合资信评估股份有限公司"))

    def test_agency_short_alias(self):
        kind, who = classify_termination_title(
            "中诚信国际关于终止张家港市金城投资发展集团有限公司主体信用评级的公告",
            "张家港市金城投资发展集团有限公司",
        )
        self.assertEqual((kind, who), ("agency", "中诚信国际信用评级有限责任公司"))

    def test_agency_traditional_alias(self):
        kind, who = classify_termination_title(
            "大公國際關于終止南京溧水城市建設集團有限公司主體及相關債項信用評級的公告",
            "南京溧水城市建设集团有限公司",
        )
        self.assertEqual((kind, who), ("agency", "大公国际资信评估有限公司"))

    def test_issuer_name_prefix(self):
        kind, who = classify_termination_title(
            f"{ISSUER}关于终止主体及相关债项信用评级的公告", ISSUER
        )
        self.assertEqual((kind, who), ("issuer", ISSUER))

    def test_guanyu_style_issuer_announcement_with_zhuti(self):
        title = "关于终止青岛上合控股发展集团有限公司主体信用评级的公告"
        kind, who = classify_termination_title(title, "青岛上合控股发展集团有限公司")
        self.assertEqual((kind, who), ("issuer", "青岛上合控股发展集团有限公司"))

    def test_guanyi_style_without_zhuti_is_not_issuer_level(self):
        title = "关于终止“兴晴2023年第一期个人消费贷款资产支持证券”优先档证券信用等级的公告"
        kind, who = classify_termination_title(title, "兴业消费金融股份有限公司")
        self.assertEqual((kind, who), ("other", None))

    def test_trustee_report_is_other(self):
        title = "国开证券股份有限公司关于南京地铁集团有限公司终止主体及相关债项信用评级的临时受托管理事务报告"
        kind, who = classify_termination_title(title, ISSUER)
        self.assertEqual((kind, who), ("other", None))

    def test_known_agency_from_events_prefix(self):
        kind, who = classify_termination_title(
            "上海资信有限公司关于终止某主体评级的公告", "某主体", {"上海资信有限公司"}
        )
        self.assertEqual((kind, who), ("agency", "上海资信有限公司"))


class IssuerRatingStatusTests(unittest.TestCase):
    def test_recent_event_is_valid(self):
        raw = {"events": {"联合资信评估股份有限公司": ["2026-06-13"]},
               "bond_ratings": {}, "announcements": []}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertTrue(status["valid"])
        self.assertEqual(status["reason"], "valid")
        self.assertEqual(status["agencies"]["联合资信评估股份有限公司"]["valid_until"], "2027-06-13")

    def test_expired_by_rateexpdate(self):
        # 贵安案例：东方金诚2026-06-18评级、有效期只到2026-07-16
        raw = {"events": {"东方金诚国际信用评估有限公司": ["2026-06-18"]},
               "bond_ratings": {"东方金诚国际信用评估有限公司": [["2026-06-18", "2026-07-16"]]},
               "announcements": []}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "expired")

    def test_expired_by_default_one_year(self):
        # 比亚迪案例：中诚信2025-06-19后无跟踪，评级日+365天已过
        raw = {"events": {"中诚信国际信用评级有限责任公司": ["2025-06-19"]},
               "bond_ratings": {}, "announcements": []}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "expired")
        self.assertEqual(status["agencies"]["中诚信国际信用评级有限责任公司"]["valid_until"], "2026-06-19")

    def test_terminated_by_agency_announcement(self):
        raw = {"events": {"联合资信评估股份有限公司": ["2025-06-13"]},
               "bond_ratings": {},
               "announcements": [["2026-04-15", "联合资信评估股份有限公司关于终止南京地铁集团有限公司主体及“21南京地铁绿色债01、G21宁铁1”信用评级的公告"]]}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "terminated")

    def test_restart_after_termination_is_valid(self):
        # 武汉洪山案例：联合资信2025-01终止后2026-07继续评
        raw = {"events": {"联合资信评估股份有限公司": ["2026-07-03"]},
               "bond_ratings": {},
               "announcements": [["2025-01-16", "联合资信评估股份有限公司关于终止武汉洪山大学之城国资投资集团有限公司主体及相关债项信用评级的公告"]]}
        status = compute_issuer_rating_status("武汉洪山大学之城国资投资集团有限公司", raw, AS_OF)
        self.assertTrue(status["valid"])

    def test_full_termination_candidate_covering_all_agencies(self):
        raw = {"events": {"联合资信评估股份有限公司": ["2025-06-13"],
                          "中诚信国际信用评级有限责任公司": ["2024-06-27"]},
               "bond_ratings": {},
               "announcements": [["2026-08-25", f"{ISSUER}关于终止主体及相关债项信用评级的公告"]]}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "terminated")

    def test_issuer_announcement_with_surviving_agency_is_valid(self):
        # 成都香城案例：发行人发终止公告，但另一机构之后仍在评
        raw = {"events": {"联合资信评估股份有限公司": ["2025-06-26"],
                          "东方金诚国际信用评估有限公司": ["2026-07-23"]},
               "bond_ratings": {},
               "announcements": [["2025-09-04", "成都香城投资集团有限公司关于终止主体及债项信用评级的公告"]]}
        status = compute_issuer_rating_status("成都香城投资集团有限公司", raw, AS_OF)
        self.assertTrue(status["valid"])

    def test_guarantor_termination_ignored(self):
        title = "中诚信国际关于确认“22诸新绿色债01G22诸新1”债项信用等级及终止其担保方诸暨市国有资产经营有限公司主体评级的公告"
        raw = {"events": {"中诚信国际信用评级有限责任公司": ["2025-06-27"]},
               "bond_ratings": {}, "announcements": [["2025-06-27", title]]}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "expired")  # 未被终止，只是到期未续

    def test_bond_level_termination_does_not_kill_issuer_rating(self):
        title = "联合资信评估股份有限公司关于终止“20深业MTN002”信用评级的公告"
        raw = {"events": {"联合资信评估股份有限公司": ["2026-06-30"]},
               "bond_ratings": {}, "announcements": [["2023-04-18", title]]}
        status = compute_issuer_rating_status("深业集团有限公司", raw, AS_OF)
        self.assertTrue(status["valid"])

    def test_international_agencies_excluded(self):
        # 泰兴城投案例：境内全部到期，仅剩联合评级国际/中国诚信(亚太)
        raw = {"events": {"东方金诚国际信用评估有限公司": ["2024-06-22"],
                          "联合评级国际有限公司": ["2026-02-11"],
                          "中国诚信(亚太)信用评级有限公司": ["2026-02-03"]},
               "bond_ratings": {}, "announcements": []}
        status = compute_issuer_rating_status("泰兴市城市投资发展集团有限公司", raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertNotIn("联合评级国际有限公司", status["agencies"])

    def test_bank_agencies_excluded(self):
        raw = {"events": {"中国工商银行股份有限公司上海分行": ["2026-06-01"]},
               "bond_ratings": {}, "announcements": []}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "never_rated")

    def test_mixed_terminated_and_expired(self):
        raw = {"events": {"中诚信国际信用评级有限责任公司": ["2023-07-18"]},
               "bond_ratings": {},
               "announcements": [["2024-01-25", "中诚信国际关于终止远洋控股集团(中国)有限公司主体信用评级的公告"]]}
        # 中诚信被终止；再补一家到期未续机构构成 mixed
        raw["events"]["联合资信评估股份有限公司"] = ["2021-05-25"]
        status = compute_issuer_rating_status("北京远洋控股集团有限公司", raw, AS_OF)
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "mixed_terminated_expired")

    def test_stale_rateexpdate_not_attached_to_recent_event(self):
        # 机构最新事件2026-06，但债项评级记录是2023年的（RATEEXPDATE很晚也不采用）
        raw = {"events": {"联合资信评估股份有限公司": ["2026-06-13"]},
               "bond_ratings": {"联合资信评估股份有限公司": [["2023-06-26", "2030-12-31"]]},
               "announcements": []}
        status = compute_issuer_rating_status(ISSUER, raw, AS_OF)
        self.assertEqual(status["agencies"]["联合资信评估股份有限公司"]["valid_until"], "2027-06-13")
        self.assertTrue(status["valid"])

    def test_build_issuer_rating_status_skips_missing(self):
        status = build_issuer_rating_status({}, AS_OF)
        self.assertEqual(status, {})


class ExemptionRevocationTests(unittest.TestCase):
    def _fact(self, issue="2026-07-29", issuer_dates=None, credit_dates=None, rating=None):
        return {
            "issue_date": issue,
            "issuer_dates": issuer_dates or [],
            "credit_dates": credit_dates or [],
            "issuer_rating": rating,
        }

    def test_exemption_ok_when_valid(self):
        fact = self._fact(rating={"valid": True, "note": "主体有存续有效评级"})
        result = evaluate_rating_compliance(AS_OF, fact)
        self.assertEqual(result["status"], "ok")
        self.assertIn("豁免", result["reason"])

    def test_exemption_ok_without_status_key(self):
        # 旧缓存/主体状态抓取失败：无 issuer_rating 键，维持豁免
        fact = self._fact()
        fact.pop("issuer_rating")
        result = evaluate_rating_compliance(AS_OF, fact)
        self.assertEqual(result["status"], "ok")
        self.assertIn("豁免", result["reason"])

    def test_exemption_revoked_fails_window(self):
        # 南京地铁26MTN001：主体终止 + 窗口无跟踪评级 -> fail
        fact = self._fact(
            issuer_dates=["2024-06-27", "2025-06-13"],
            rating={"valid": False, "note": "主体评级已全部终止"},
        )
        result = evaluate_rating_compliance(AS_OF, fact)
        self.assertEqual(result["status"], "fail")
        self.assertIn("主体评级已全部终止", result["reason"])
        self.assertIn("缺少2026年1月1日-6月30日的主体跟踪评级", result["reason"])

    def test_exemption_revoked_but_window_satisfied_stays_ok(self):
        # 浙江创新投：评级已到期未续，但2026-06-26有窗口内跟踪评级 -> ok + 说明
        fact = self._fact(
            issuer_dates=["2026-06-26"],
            rating={"valid": False, "note": "主体评级已到期未续"},
        )
        result = evaluate_rating_compliance(AS_OF, fact)
        self.assertEqual(result["status"], "ok")
        self.assertIn("主体评级已到期未续", result["reason"])
        self.assertIn("630判定仍合规", result["reason"])

    def test_revoked_exemption_with_credit_rating_requirement(self):
        fact = self._fact(
            issuer_dates=["2026-06-26"],
            credit_dates=["2025-06-20"],
            rating={"valid": False, "note": "主体评级已到期未续"},
        )
        result = evaluate_rating_compliance(AS_OF, fact)
        self.assertEqual(result["status"], "fail")
        self.assertIn("债项跟踪评级", result["reason"])

    def test_non_exempt_fail_reason_unchanged(self):
        fact = {
            "issue_date": "2024-03-28",
            "issuer_dates": ["2024-06-27", "2025-06-13"],
            "credit_dates": [],
            "issuer_rating": {"valid": False, "note": "主体评级已全部终止"},
        }
        result = evaluate_rating_compliance(AS_OF, fact)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason"], "缺少2026年1月1日-6月30日的主体跟踪评级")

    def test_first_half_of_year_windows_unchanged(self):
        fact = {
            "issue_date": "2023-05-01",
            "issuer_dates": ["2025-06-30"],
            "credit_dates": [],
        }
        result = evaluate_rating_compliance(date(2026, 3, 31), fact)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reason"], "")

    def test_issue_date_missing_still_unknown(self):
        fact = {"issue_date": "", "issuer_dates": [], "credit_dates": []}
        result = evaluate_rating_compliance(AS_OF, fact)
        self.assertEqual(result["status"], "unknown")


class PersistRatingFactsTests(unittest.TestCase):
    def test_persist_embeds_issuer_rating_and_status(self):
        facts = {
            "26MTN001.IB": {"secode": "s1", "securityid": "x1", "issue_date": "",
                            "credit_dates": [], "issuer_dates": ["2025-06-13"]},
            "OLD.IB": {"secode": "s2", "securityid": "x2", "issue_date": "",
                       "credit_dates": [], "issuer_dates": ["2026-06-26"]},
        }
        bonds = [
            {"code": "26MTN001.IB", "issuer": ISSUER, "issue_date": "2026-07-29"},
            {"code": "OLD.IB", "issuer": "正常主体有限公司", "issue_date": "2024-03-28"},
        ]
        issuer_status = {
            ISSUER: {"valid": False, "reason": "terminated",
                     "note": "主体评级已全部终止", "agencies": {}},
            "正常主体有限公司": {"valid": True, "reason": "valid",
                                 "note": "主体有存续有效评级", "agencies": {}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "rating_facts_cache.json"
            import juyuan_update.rating_compliance as rc
            with patch.object(rc.config, "RATING_FACTS_CACHE", cache_path):
                payload = persist_rating_facts(facts, bonds, AS_OF, issuer_status=issuer_status)
                # 券级嵌入
                self.assertEqual(
                    payload["facts"]["26MTN001.IB"]["issuer_rating"],
                    {"valid": False, "note": "主体评级已全部终止"},
                )
                self.assertEqual(
                    payload["facts"]["OLD.IB"]["issuer_rating"]["valid"], True
                )
                # 主体级明细
                self.assertIn(ISSUER, payload["issuer_status"])
                # 豁免吊销生效：26MTN001 fail；OLD.IB 非豁免但有窗口内跟踪评级 -> ok
                self.assertEqual(payload["compliance"]["26MTN001.IB"][0], "fail")
                self.assertIn("主体评级已全部终止", payload["compliance"]["26MTN001.IB"][1])
                self.assertEqual(payload["compliance"]["OLD.IB"][0], "ok")
                self.assertEqual(payload["compliance"]["OLD.IB"][1], "")
                # 写盘后可读回
                self.assertTrue(cache_path.exists())
                from juyuan_update.rating_compliance import load_rating_facts_cache
                reloaded = load_rating_facts_cache()
                self.assertEqual(reloaded["as_of_date"], "2026-09-23")
                self.assertIn("issuer_status", reloaded)

    def test_persist_without_status_keeps_old_behavior(self):
        facts = {"26MTN001.IB": {"secode": "s1", "securityid": "x1", "issue_date": "",
                                 "credit_dates": [], "issuer_dates": []}}
        bonds = [{"code": "26MTN001.IB", "issuer": ISSUER, "issue_date": "2026-07-29"}]
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "rating_facts_cache.json"
            import juyuan_update.rating_compliance as rc
            with patch.object(rc.config, "RATING_FACTS_CACHE", cache_path):
                payload = persist_rating_facts(facts, bonds, AS_OF)
                self.assertNotIn("issuer_rating", payload["facts"]["26MTN001.IB"])
                self.assertEqual(payload["compliance"]["26MTN001.IB"][0], "ok")


if __name__ == "__main__":
    unittest.main()
