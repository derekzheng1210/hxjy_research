from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("PORTAL_DATA_ROOT", str(Path(__file__).resolve().parent / ".test_runtime" / "credit_research"))

from bond_detail import credit_research as cr


BOND = {
    "code": "102682773.IB",
    "name": "26邯郸城投MTN001A",
    "issuer": "邯郸城市发展投资集团有限公司",
    "term": 2.95,
    "implied_rating": "AA",
    "internal_rating": "BBB-",
    "entity": "地方国有企业",
    "ct": "是",
    "sub": "否",
    "guarantor": "",
    "issue_date": "2026-07-27",
    "effective_maturity_date": "2029-07-27",
    "current_yield": 2.05,
}


def _sample_report(**overrides):
    report = {
        "meta": {"bond_code": "102682773.IB", "bond_name": "26邯郸城投MTN001A",
                 "issuer_name": "邯郸城市发展投资集团有限公司", "research_date": "2026-09-01",
                 "juyuan_query_date": "2026-09-01", "latest_financial_period": "2026-06-30",
                 "data_completeness": "medium"},
        "classification": {"type": "CITY_PLATFORM", "subtype": "传统城投平台", "confidence": "high",
                           "juyuan_city_flag": "是", "reasons": ["聚源城投标志为是", "实控人为地方国资委"],
                           "conflicting_evidence": ["存在一定市场化业务"]},
        "brief": {
            "company_intro": "地方国资委控制的市级城投平台，主营基础设施建设与土地开发。",
            "one_sentence_conclusion": "区域支持稳定但盈利偏弱。",
            "overall_trend": "stable",
            "key_changes": [
                {"direction": "positive", "title": "杠杆压缩", "conclusion": "资产负债率较上年回落。",
                 "evidence_refs": ["S1"]},
                {"direction": "neutral", "title": "盈利仍弱", "conclusion": "净利润微薄。", "evidence_refs": ["S1"]},
                {"direction": "negative", "title": "筹资流出", "conclusion": "筹资现金流转负。", "evidence_refs": ["S9"]},
                {"direction": "positive", "title": "第四条应移入详情", "conclusion": "多出的发现。", "evidence_refs": []},
            ],
            "top_risks": [
                {"severity": "medium", "title": "再融资压力", "conclusion": "短债占比较高。",
                 "monitor": "短期债务占比", "evidence_refs": ["S1"]},
                {"severity": "low", "title": "补助依赖", "conclusion": "盈利依赖政府补助。",
                 "monitor": "补助规模", "evidence_refs": []},
                {"severity": "uncertain", "title": "区域财政", "conclusion": "土地出让下滑。",
                 "monitor": "土地出让金", "evidence_refs": []},
                {"severity": "high", "title": "第四项应移入详情", "conclusion": "多出的风险。",
                 "monitor": "", "evidence_refs": []},
            ],
            "public_opinion": {"verdict": "not_found", "conclusion": "未发现符合标准的重大直接舆情。"},
            "top_monitoring_items": ["短期债务占比", "政府补助到账"],
        },
        "details": {"company_profile": {"controller": "邯郸市国资委"}, "city_analysis": {"level": "市级平台"},
                    "hybrid_analysis": {}, "industry_analysis": {},
                    "operating_changes": ["2026H1营业收入3.37亿元"],
                    "all_risks": ["对外担保约12亿元"],
                    "public_opinion_events": [], "juyuan_metrics": [{"metric": "资产负债率", "value": "49.7%", "period": "2026-06-30"}],
                    "data_gaps": ["聚源财务明细未覆盖"]},
        "sources": [
            {"ref": "S1", "title": "聚源债券快照", "source_name": "聚源数据库", "source_type": "juyuan",
             "publish_date": "2026-09-01", "report_period": "2026-06-30", "is_primary": True},
        ],
    }
    for key, value in overrides.items():
        report[key] = value
    return report


class ExtractJsonTests(unittest.TestCase):
    def test_extract_from_fenced_answer_with_chatter(self):
        text = '过程消息\n依据快照完成研判。\n```json\n{"meta": {}, "classification": {"type": "CITY_PLATFORM"}}\n```'
        extracted = cr.extract_json_text(text)
        data = json.loads(extracted)
        self.assertEqual(data["classification"]["type"], "CITY_PLATFORM")

    def test_extract_from_bare_braces(self):
        text = '前言 {"a": {"b": 1}} 后记'
        self.assertEqual(json.loads(cr.extract_json_text(text))["a"]["b"], 1)


class ValidateNormalizeTests(unittest.TestCase):
    def test_truncates_to_three_and_moves_extras_to_details(self):
        normalized, warnings = cr.validate_and_normalize(_sample_report(), BOND)
        self.assertIsNotNone(normalized)
        self.assertEqual(len(normalized["brief"]["key_changes"]), 3)
        self.assertEqual(len(normalized["brief"]["top_risks"]), 3)
        self.assertEqual(normalized["details"]["operating_changes"][-1]["title"], "第四条应移入详情")
        self.assertEqual(normalized["details"]["all_risks"][-1]["title"], "第四项应移入详情")
        self.assertTrue(any("超出3" in w for w in warnings))

    def test_dangling_evidence_refs_removed(self):
        normalized, _ = cr.validate_and_normalize(_sample_report(), BOND)
        refs = [c["evidence_refs"] for c in normalized["brief"]["key_changes"]]
        self.assertIn([], refs)  # S9 不存在，被剔除

    def test_missing_top_level_field_is_fatal(self):
        report = _sample_report()
        report.pop("sources")
        normalized, _ = cr.validate_and_normalize(report, BOND)
        # sources 属于约定一级字段，缺失为致命结构问题（触发一次格式修复）
        self.assertIsNone(normalized)
        # sources 为 null 视为空数组，仅警告
        report3 = _sample_report()
        report3["sources"] = None
        normalized3, warnings = cr.validate_and_normalize(report3, BOND)
        self.assertIsNotNone(normalized3)
        self.assertEqual(normalized3["sources"], [])
        self.assertTrue(any("sources" in w for w in warnings))
        report2 = _sample_report()
        report2.pop("brief")
        normalized2, _ = cr.validate_and_normalize(report2, BOND)
        self.assertIsNone(normalized2)

    def test_bad_classification_type_defaults_to_uncertain(self):
        """非法/空分类不再整单拒绝：兜底UNCERTAIN并警告（保留上一版结果的反面）。"""
        report = _sample_report()
        report["classification"]["type"] = "SOMETHING"
        normalized, warnings = cr.validate_and_normalize(report, BOND)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["classification"]["type"], "UNCERTAIN")
        self.assertTrue(any("classification.type非法" in w for w in warnings))
        report2 = _sample_report()
        report2["classification"]["type"] = ""
        normalized2, warnings2 = cr.validate_and_normalize(report2, BOND)
        self.assertIsNotNone(normalized2)
        self.assertEqual(normalized2["classification"]["type"], "UNCERTAIN")

    def test_empty_verdict_defaults_to_insufficient(self):
        """空verdict兜底为insufficient（无法判断），不再整单拒绝。"""
        report = _sample_report()
        report["brief"]["public_opinion"]["verdict"] = ""
        normalized, warnings = cr.validate_and_normalize(report, BOND)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["brief"]["public_opinion"]["verdict"], "insufficient")
        self.assertTrue(any("verdict非法" in w for w in warnings))
        report2 = _sample_report()
        report2["brief"]["public_opinion"]["verdict"] = "garbage"
        normalized2, _ = cr.validate_and_normalize(report2, BOND)
        self.assertIsNotNone(normalized2)
        self.assertEqual(normalized2["brief"]["public_opinion"]["verdict"], "insufficient")

    def test_length_warnings(self):
        report = _sample_report()
        report["brief"]["company_intro"] = "长" * 130
        normalized, warnings = cr.validate_and_normalize(report, BOND)
        self.assertIsNotNone(normalized)
        self.assertTrue(any("公司简介" in w for w in warnings))

    def test_meta_filled_from_bond_context(self):
        report = _sample_report()
        report["meta"] = {}
        normalized, _ = cr.validate_and_normalize(report, BOND)
        self.assertEqual(normalized["meta"]["bond_code"], "102682773.IB")
        self.assertEqual(normalized["meta"]["issuer_name"], "邯郸城市发展投资集团有限公司")

    def test_han_char_count(self):
        self.assertEqual(cr.han_chars("abc估计123四个汉字"), 6)


class PromptTests(unittest.TestCase):
    def test_prompt_contains_snapshot_and_schema(self):
        prompt = cr.build_prompt("102682773.IB", {"query_date": "2026-09-01", "bond": {}}, allow_search=True)
        self.assertIn("聚源数据快照", prompt)
        self.assertIn("CITY_HYBRID", prompt)
        self.assertIn("102682773.IB", prompt)
        self.assertIn("not_found", prompt)
        # 无检索工具的回退提示
        prompt2 = cr.build_prompt("102682773.IB", {"bond": {}}, allow_search=False)
        self.assertIn("没有数据查询与检索工具", prompt2)


class AgentStreamFilterTests(unittest.TestCase):
    """思考过程与中间消息过滤：只采纳最后一条 message。"""

    def _sse(self, events):
        lines = []
        for outer in events:
            lines.append("data: " + json.dumps(outer, ensure_ascii=False))
        lines.append("")
        return "\n".join(lines).encode("utf-8")

    def test_reasoning_and_intermediate_messages_excluded(self):
        reasoning_id, mid1, final_id = "msg_r", "msg_m1", "msg_m2"

        def outer(inner):
            return {"data": json.dumps(inner, ensure_ascii=False), "type": "supaw"}

        events = [
            outer({"type": "reasoning", "id": reasoning_id, "status": "in_progress"}),
            outer({"type": "text", "delta": True, "msg_id": reasoning_id, "text": "我在思考……"}),
            outer({"type": "message", "id": mid1, "status": "in_progress"}),
            outer({"type": "text", "delta": True, "msg_id": mid1, "text": "已获取核心财务数据。现查询存续债明细。"}),
            outer({"type": "message", "id": mid1, "status": "completed"}),
            outer({"type": "plugin_call", "status": "completed"}),
            outer({"type": "data", "data": {"name": "juyuan_query", "arguments": "{}"}}),
            outer({"type": "message", "id": final_id, "status": "in_progress"}),
            outer({"type": "text", "delta": True, "msg_id": final_id, "text": "最终结论：该主体为城投。"}),
            outer({"type": "message", "id": final_id, "status": "completed",
                   "usage": {"input_tokens": 10, "output_tokens": 5}}),
            outer({"type": "turn_usage", "usage": {"model_name": "deepseek-v4-flash"}}),
            {"type": "complete"},
        ]

        captured = []

        class FakeResp:
            status_code = 200

            def iter_lines(self, decode_unicode=False):
                for line in self._payload.split(b"\n"):
                    yield line

            def close(self):
                pass

        resp = FakeResp()
        resp._payload = self._sse(events)

        class FakeSession:
            trust_env = False

            def post(self, *a, **kw):
                return resp

        import bond_detail.credit_research as mod
        orig_session, orig_token = mod._agent_session, mod.get_agent_token
        mod._agent_session = lambda: FakeSession()
        mod.get_agent_token = lambda force=False: "test-token"  # 屏蔽真实登录
        try:
            answer, meta = mod._run_agent_stream("prompt", lambda kind, payload: captured.append(kind))
        finally:
            mod._agent_session, mod.get_agent_token = orig_session, orig_token

        self.assertEqual(answer, "最终结论：该主体为城投。")
        self.assertEqual(meta["plugin_calls"], 1)
        self.assertEqual(meta["model"], "deepseek-v4-flash")
        self.assertIn("plugin", captured)


class JobStoreTests(unittest.TestCase):
    def test_job_lifecycle_on_disk(self):
        cr._ensure_dirs()
        issuer = cr.issuer_key(BOND)
        job = {"job_id": "j1", "issuer_key": issuer, "bond_code": BOND["code"], "state": "running",
               "stage": cr.STAGE_AGENT, "detail": "x", "progress": 30, "plugin_calls": 2,
               "answer_chars": 0, "started_at": "2026-09-01 10:00:00", "updated_at": "2026-09-01 10:00:00",
               "updated_ts": time.time(), "stage_started_at": time.time(), "error": ""}
        cr._write_json(cr._job_path(issuer), job)
        latest = cr.get_latest_job(issuer)
        self.assertEqual(latest["job_id"], "j1")
        running = cr.get_running_job(issuer)
        self.assertEqual(running["job_id"], "j1")
        view = cr._job_view(running)
        self.assertGreaterEqual(view["progress"], 10)

        # 僵死任务不再视为运行中
        job["updated_ts"] = time.time() - cr.JOB_STALE_SECONDS - 10
        cr._write_json(cr._job_path(issuer), job)
        self.assertIsNone(cr.get_running_job(issuer))
        self.assertIsNone(cr.get_latest_job(issuer))

    def test_override_classification_updates_report(self):
        cr._ensure_dirs()
        issuer = cr.issuer_key(BOND)
        normalized, _ = cr.validate_and_normalize(_sample_report(), BOND)
        cr._write_json(cr._report_path(issuer), {
            "generated_at": "2026-09-01 10:00:00", "report": normalized, "issuer_key": issuer,
        })
        payload = cr.override_classification(BOND, "CITY_HYBRID", "市场化业务占比较高")
        self.assertIsNotNone(payload)
        classification = payload["report"]["classification"]
        self.assertEqual(classification["type"], "CITY_HYBRID")
        self.assertEqual(classification["auto_type"], "CITY_PLATFORM")
        self.assertEqual(classification["manual_override"]["type"], "CITY_HYBRID")
        self.assertTrue(classification["manual_override"]["at"])


class SectionModeTests(unittest.TestCase):
    def _section_output(self):
        return {
            "meta": {"bond_code": "102682773.IB", "research_date": "2026-09-01"},
            "brief": {"top_risks": [
                {"severity": "high", "title": "短债覆盖不足", "conclusion": "货币资金13.7亿对短债47.3亿。",
                 "monitor": "货币资金/短期债务", "evidence_refs": ["S1"]},
            ]},
            "details": {"all_risks": ["区域财政承压"]},
            "sources": [{"ref": "S1", "title": "聚源财务查询", "source_name": "聚源数据库",
                         "source_type": "juyuan", "publish_date": "2026-09-01"}],
        }

    def test_section_validation_inherits_base(self):
        base = {"report": cr.validate_and_normalize(_sample_report(), BOND)[0]}
        normalized, _ = cr.validate_and_normalize(self._section_output(), BOND, mode="risks", base=base)
        self.assertIsNotNone(normalized)
        # classification/verdict 缺省时继承 base
        self.assertEqual(normalized["classification"]["type"], base["report"]["classification"]["type"])
        self.assertEqual(normalized["brief"]["public_opinion"]["verdict"],
                         base["report"]["brief"]["public_opinion"]["verdict"])
        self.assertEqual(len(normalized["brief"]["top_risks"]), 1)

    def test_merge_section_renumbers_sources(self):
        base_payload = {"generated_at": "2026-09-01 10:00:00",
                        "report": cr.validate_and_normalize(_sample_report(), BOND)[0]}
        section, _ = cr.validate_and_normalize(self._section_output(), BOND, mode="risks")
        merged = cr.merge_section_report(base_payload, section, "risks")
        refs = [s["ref"] for s in merged["sources"]]
        # 原报告已有 S1，章节来源 S1 应重编号为 S2
        self.assertEqual(refs, ["S1", "S2"])
        self.assertEqual(merged["brief"]["top_risks"][0]["evidence_refs"], ["S2"])
        self.assertEqual(merged["brief"]["top_risks"][0]["title"], "短债覆盖不足")
        # 其余章节保持不变
        self.assertEqual(merged["brief"]["key_changes"][0]["title"], "杠杆压缩")

    def test_merge_without_base_marks_partial(self):
        section, _ = cr.validate_and_normalize(self._section_output(), BOND, mode="risks")
        merged = cr.merge_section_report(None, section, "risks")
        self.assertTrue(merged.get("partial"))
        self.assertEqual(merged["partial_modes"], ["risks"])

    def test_section_without_sources_inherits_base_refs(self):
        """模型未带 sources 时引用按已有报告校验，合并不重复追加来源。"""
        base_normalized = cr.validate_and_normalize(_sample_report(), BOND)[0]
        base_payload = {"generated_at": "2026-09-01 10:00:00", "report": base_normalized}
        section_raw = {
            "meta": {}, "classification": {},
            "brief": {"key_changes": [
                {"direction": "negative", "title": "现金覆盖下滑", "conclusion": "货币资金腰斩。",
                 "evidence_refs": ["S1"]},
            ]},
            "details": {}, "sources": [],
        }
        section, warnings = cr.validate_and_normalize(section_raw, BOND, mode="changes",
                                                       base={"report": base_normalized})
        self.assertIsNotNone(section)
        # S1 继承自已有报告，不被剔除
        self.assertEqual(section["brief"]["key_changes"][0]["evidence_refs"], ["S1"])
        merged = cr.merge_section_report(base_payload, section, "changes")
        self.assertEqual(len(merged["sources"]), len(base_normalized["sources"]))
        self.assertEqual(merged["brief"]["key_changes"][0]["evidence_refs"], ["S1"])

    def test_changes_merge_unions_metrics_by_period(self):
        """章节刷新指标按指标+报告期联合：弱化的一次运行不冲掉既有年度。"""
        base_normalized = cr.validate_and_normalize(_sample_report(), BOND)[0]
        base_normalized["details"]["juyuan_metrics"] = [
            {"category": "区域财政", "metric": "GDP", "value": "4382.2", "unit": "亿元", "period": "2023"},
            {"category": "区域财政", "metric": "GDP", "value": "4704.3", "unit": "亿元", "period": "2024"},
            {"category": "主体财务", "metric": "净利润", "value": "0.19", "unit": "亿元", "period": "2024"},
        ]
        base_payload = {"generated_at": "2026-09-01 10:00:00", "report": base_normalized}
        section_raw = {
            "meta": {}, "classification": {}, "brief": {}, "sources": [],
            "details": {"juyuan_metrics": [
                {"category": "区域财政", "metric": "GDP", "value": "4920.1", "unit": "亿元", "period": "2025"},
            ]},
        }
        section, _ = cr.validate_and_normalize(section_raw, BOND, mode="changes",
                                                base={"report": base_normalized})
        merged = cr.merge_section_report(base_payload, section, "changes")
        got = {(m["metric"], m["period"]): m["value"] for m in merged["details"]["juyuan_metrics"]}
        self.assertEqual(got[("GDP", "2023")], "4382.2")
        self.assertEqual(got[("GDP", "2024")], "4704.3")
        self.assertEqual(got[("GDP", "2025")], "4920.1")
        self.assertEqual(got[("净利润", "2024")], "0.19")


class NormalizationTests(unittest.TestCase):
    def test_norm_period_extracts_year_and_marks(self):
        self.assertEqual(cr._norm_period("邯郸市2025年"), "2025")
        self.assertEqual(cr._norm_period("2024末"), "2024")
        self.assertEqual(cr._norm_period("2026中报"), "2026H1")
        self.assertEqual(cr._norm_period("报告期2024Q3"), "2024Q3")
        self.assertEqual(cr._norm_period(""), "")

    def test_source_alias_keys_normalized(self):
        """模型用 id/code/description 变体键名时来源与引用可对上。"""
        report = _sample_report()
        report["sources"] = [
            {"id": "S1", "code": "聚源·发债公司财务报表", "source_type": "聚源(Gildata)数据库",
             "date": "查询-2026-09-01", "description": "财务数据"},
        ]
        report["brief"]["key_changes"][0]["evidence_refs"] = ["S1"]
        normalized, _ = cr.validate_and_normalize(report, BOND)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["sources"][0]["ref"], "S1")
        self.assertEqual(normalized["sources"][0]["title"], "聚源·发债公司财务报表")
        self.assertEqual(normalized["sources"][0]["source_name"], "聚源(Gildata)数据库")
        self.assertEqual(normalized["brief"]["key_changes"][0]["evidence_refs"], ["S1"])


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_start_reuses_single_job(self):
        """两个用户同时点击同一发行人：只起一个任务，双方拿到同一 job。"""
        import threading as _threading

        bond = {**BOND, "code": "CONCURRENT.TEST", "issuer": "并发测试主体有限公司"}
        release = _threading.Event()
        results, errors = [], []

        def fake_run_job(job):
            release.wait(timeout=5)  # 模拟任务运行中，阻止重复创建

        original_run_job = cr._run_job
        cr._run_job = fake_run_job
        try:
            def click():
                try:
                    results.append(cr.start_research(bond, force=True))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = _threading.Thread(target=click)
            t2 = _threading.Thread(target=click)
            t1.start(); t2.start()
            t1.join(5); t2.join(5)
            release.set()
        finally:
            cr._run_job = original_run_job

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["job_id"], results[1]["job_id"])
        # 清理测试任务文件
        (cr.JOBS_DIR / f"{cr.issuer_key(bond)}.job.json").unlink(missing_ok=True)


class MesoSupplementTests(unittest.TestCase):
    """中观高频跟踪：指标级相关性匹配，无相关指标的行业不强行展示。"""

    def setUp(self):
        import tempfile
        from bond_detail import meso as meso_mod

        self.meso = meso_mod
        self.tmp = tempfile.mkdtemp(prefix="meso_test_")
        from pathlib import Path
        self.html = Path(self.tmp) / "行业景气度跟踪.html"
        self.cache = Path(self.tmp) / "indicators_data.json"
        self.html.write_text(
            '<script>var data=['
            '{"name":"测试乳业有限公司","sw_l1":"食品饮料","sw_l2":"乳品及冷冻食品"},'
            '{"name":"测试白酒股份有限公司","sw_l1":"食品饮料","sw_l2":"白酒"},'
            '{"name":"测试城投有限公司","sw_l1":"建筑装饰","sw_l2":"基础设施建设"}];</script>',
            encoding="utf-8",
        )
        self.cache.write_text(json.dumps({
            "updated": "2026-09-02 10:00:00",
            "indicators": {
                "S0000001": {"times": "['2026-08-01','2026-08-15','2026-08-31']",
                              "values": "[10.0, 11.0, 12.5]"},
                "S0000002": {"times": "['2026-08-01','2026-08-31']",
                              "values": "[100.0, 98.0]"},
            },
        }), encoding="utf-8")
        self._orig = (meso_mod.PROSPERITY_HTML, meso_mod.IPM_CACHE, meso_mod.IPM_EXCEL,
                      meso_mod._industry_cache, meso_mod._series_cache)
        meso_mod.PROSPERITY_HTML = self.html
        meso_mod.IPM_CACHE = self.cache
        meso_mod.IPM_EXCEL = Path(self.tmp) / "missing.xlsx"
        meso_mod._meso_table_cache = [
            {"l1": "消费", "l2": "食品饮料", "name": "中国:产量:乳制品:当月值", "sid": "S0000001"},
            {"l1": "消费", "l2": "食品饮料", "name": "中国:零售价:婴幼儿奶粉(国产品牌)", "sid": "S0000002"},
            # 与乳业无关的指标（白酒主体也不该看到）
            {"l1": "消费", "l2": "食品饮料", "name": "中国:市场价:大豆(黄豆)", "sid": "S0000003"},
        ]
        meso_mod._industry_cache = None
        meso_mod._series_cache = {"mtime": -1.0, "data": {}, "updated": ""}

    def tearDown(self):
        meso = self.meso
        (meso.PROSPERITY_HTML, meso.IPM_CACHE, meso.IPM_EXCEL,
         meso._industry_cache, meso._series_cache) = (self._orig[0], self._orig[1], self._orig[2],
                                                      self._orig[3], self._orig[4])
        meso._meso_table_cache = None
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dairy_issuer_gets_only_relevant_indicators(self):
        sup = self.meso.meso_supplement("测试乳业有限公司")
        self.assertIsNotNone(sup)
        names = [x["name"] for x in sup["indicators"]]
        # 乳制品+奶粉相关；大豆（调味品成本端）不应出现
        self.assertIn("中国:产量:乳制品:当月值", names)
        self.assertIn("中国:零售价:婴幼儿奶粉(国产品牌)", names)
        self.assertNotIn("中国:市场价:大豆(黄豆)", names)

    def test_baijiu_issuer_gets_no_supplement(self):
        """白酒（洋河类）无对应高频指标 → 不展示，不强行匹配。"""
        self.assertIsNone(self.meso.meso_supplement("测试白酒股份有限公司"))

    def test_city_platform_gets_no_supplement(self):
        self.assertIsNone(self.meso.meso_supplement("测试城投有限公司"))

    def test_issuer_industry_match(self):
        self.assertEqual(self.meso.issuer_sw_industry("测试乳业有限公司"), ("食品饮料", "乳品及冷冻食品"))
        self.assertEqual(self.meso.issuer_sw_industry("测试乳业有限公司某某分公司"), ("食品饮料", "乳品及冷冻食品"))
        self.assertIsNone(self.meso.issuer_sw_industry("不存在的主体"))

    def test_indicator_values_and_ratio_guard(self):
        # 注入一个同比类指标验证30天变化率保护
        self.meso._meso_table_cache.append(
            {"l1": "消费", "l2": "食品饮料", "name": "中国:产量:乳制品:当月同比", "sid": "S0000002"})
        sup = self.meso.meso_supplement("测试乳业有限公司")
        ratio = next(x for x in sup["indicators"] if "同比" in x["name"])
        self.assertIsNone(ratio["change_pct_30d"])
        first = next(x for x in sup["indicators"] if x["name"].endswith("当月值"))
        self.assertEqual(first["latest"], 12.5)
        self.assertEqual(first["change"], 1.5)

    def test_missing_sources_degrade_silently(self):
        self.meso._industry_cache = {}
        self.assertIsNone(self.meso.meso_supplement("任何主体"))


if __name__ == "__main__":
    unittest.main()
