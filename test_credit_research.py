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
        self.assertIn("不得声称已查询或检索", prompt2)

    def test_report_periods_follow_disclosure_seasons(self):
        from datetime import date

        self.assertEqual(cr._report_periods(date(2026, 9, 4)), ([2023, 2024, 2025], "2026H1"))
        self.assertEqual(cr._report_periods(date(2026, 10, 31)), ([2023, 2024, 2025], "2026H1"))
        self.assertEqual(cr._report_periods(date(2026, 11, 1)), ([2023, 2024, 2025], "2026Q3"))
        self.assertEqual(cr._report_periods(date(2026, 5, 15)), ([2023, 2024, 2025], "2026Q1"))
        self.assertEqual(cr._report_periods(date(2026, 4, 30)), ([2023, 2024, 2025], "2025Q3"))

    def test_prompt_injects_dynamic_period_guidance(self):
        prompt = cr.build_prompt("102682773.IB", {"query_date": "2026-09-01", "bond": {}}, allow_search=True)
        years, interim = cr._report_periods()
        self.assertIn("报告期基准", prompt)
        for year in years:
            self.assertIn(f"{year}年", prompt)
        self.assertIn(interim, prompt)
        self.assertIn("严禁用更早年度数据顶替", prompt)
        # 静态年份示例已移除，避免与动态基准冲突
        self.assertNotIn("例如2023、2024、2025", prompt)

    def test_prompt_requires_rectangular_financial_table(self):
        """财务数据完整性：指标×报告期矩形、有息债务构成、period统一写法。"""
        prompt = cr.build_prompt("102682773.IB", {"bond": {}}, allow_search=True)
        self.assertIn("一期不落", prompt)
        self.assertIn("不得只给最新一期或个别年度", prompt)
        self.assertIn("有息债务=短期借款+一年内到期的非流动负债+应付票据+长期借款+应付债券", prompt)
        self.assertIn("完整的\"指标×报告期\"矩形", prompt)
        self.assertIn("不得某期写成\"净利润（归母）\"", prompt)
        self.assertIn("尽量多期覆盖", prompt)
        # period统一写法：年报写年份、最新一期写interim，禁止日期变体
        years, interim = cr._report_periods()
        self.assertIn(f"最新一期写{interim}", prompt)
        self.assertIn("等变体", prompt)

    def test_instructions_direct_tools_first(self):
        # 取数优先级：数据查询工具优先，检索公告/评级报告只作补充
        self.assertIn("先用数据查询工具直接查询", cr.SYSTEM_INSTRUCTIONS)
        self.assertIn("不得跳过数据查询工具", cr.SYSTEM_INSTRUCTIONS)
        self.assertIn("禁止凭模型记忆或训练知识填充数值", cr.SYSTEM_INSTRUCTIONS)
        # 禁止文件输出：Agent偶发把研究写成附件、正文只留"见文件"（曾致整链失败）
        self.assertIn("禁止生成文件、附件或链接", cr.SYSTEM_INSTRUCTIONS)

    def test_snapshot_note_directs_tools_first(self):
        import sys
        import types

        stub = types.ModuleType("bond_detail.service")
        stub.credit_facility_analysis = lambda bond: {"available": False}
        stub.fetch_instrument_details = lambda code: {}
        stub.rating_compliance_analysis = lambda code: {"status": "unknown"}
        orig_parts = cr._oracle_snapshot_parts
        orig_module = sys.modules.get("bond_detail.service")
        cr._oracle_snapshot_parts = lambda raw_code, issuer: ({}, [], [])
        sys.modules["bond_detail.service"] = stub
        try:
            snapshot, _gaps = cr.build_snapshot(
                {"code": "102682773.IB", "issuer": "测试主体", "raw_code": "102682773"})
        finally:
            cr._oracle_snapshot_parts = orig_parts
            if orig_module is not None:
                sys.modules["bond_detail.service"] = orig_module
            else:
                sys.modules.pop("bond_detail.service", None)
        note = ";".join(snapshot.get("data_notes") or [])
        # 旧文案"聚源财务指标表尚未接入，请通过检索公告/评级报告补充"会把Agent带偏到
        # 只走检索；新文案要求优先用数据查询工具逐年查询
        self.assertIn("数据查询工具", note)
        self.assertIn("以此为准", note)
        self.assertNotIn("尚未接入", note)


class FallbackPromptTests(unittest.TestCase):
    """Agent 失败回退本地模型时，必须改用无工具版提示词。"""

    def test_fallback_prompt_used_when_agent_fails(self):
        captured = {}

        orig = (cr.agent_configured, cr._run_agent_stream, cr._local_llm_available, cr._run_local_llm)
        cr.agent_configured = lambda: True

        def _boom(prompt, on_event):
            raise RuntimeError("agent down")

        def _fake_local(prompt, on_event):
            captured["prompt"] = prompt
            return '{"meta": {}}', {"channel": "local_llm"}

        cr._run_agent_stream = _boom
        cr._local_llm_available = lambda: True
        cr._run_local_llm = _fake_local
        events = []
        try:
            content, meta = cr.run_research_prompt(
                "AGENT_PROMPT", lambda kind, payload: events.append(kind), fallback_prompt="FALLBACK_PROMPT")
        finally:
            (cr.agent_configured, cr._run_agent_stream,
             cr._local_llm_available, cr._run_local_llm) = orig
        self.assertEqual(captured["prompt"], "FALLBACK_PROMPT")
        self.assertEqual(content, '{"meta": {}}')
        self.assertEqual(meta["channel"], "local_llm")
        self.assertIn("agent_error", events)

    def test_no_fallback_prompt_keeps_original(self):
        captured = {}

        orig = (cr.agent_configured, cr._run_agent_stream, cr._local_llm_available, cr._run_local_llm)
        cr.agent_configured = lambda: True
        cr._run_agent_stream = lambda prompt, on_event: (_ for _ in ()).throw(RuntimeError("agent down"))

        def _fake_local(prompt, on_event):
            captured["prompt"] = prompt
            return "{}", {"channel": "local_llm"}

        cr._local_llm_available = lambda: True
        cr._run_local_llm = _fake_local
        try:
            cr.run_research_prompt("AGENT_PROMPT", lambda kind, payload: None)
        finally:
            (cr.agent_configured, cr._run_agent_stream,
             cr._local_llm_available, cr._run_local_llm) = orig
        self.assertEqual(captured["prompt"], "AGENT_PROMPT")


class SalvageJsonTests(unittest.TestCase):
    """确定性JSON救活：真实故障样本（未转义引号/缺逗号/括号失衡/尾随逗号）。"""

    def test_valid_json_untouched(self):
        text = json.dumps({"meta": {"a": 1}, "list": [{"b": "x"}, {"b": "y"}]}, ensure_ascii=False)
        self.assertEqual(cr._salvage_json_text(text), text)

    def test_unescaped_inner_quote(self):
        """合肥产投真实形态：字符串值内嵌中文引号把字符串提前截断。"""
        text = '{"city_analysis":{"定位":"重点承载产业基金与战新产业投资（长鑫存储"相关）"}}'
        parsed = cr.parse_report_json(text)
        self.assertEqual(parsed["city_analysis"]["定位"],
                         '重点承载产业基金与战新产业投资（长鑫存储"相关）')

    def test_missing_comma_between_array_elements(self):
        """合肥产投真实形态：数组元素间直接 }{ 相连。"""
        text = '{"operating_changes":[{"a":1}{"a":2}]}'
        parsed = cr.parse_report_json(text)
        self.assertEqual(parsed["operating_changes"], [{"a": 1}, {"a": 2}])

    def test_missing_comma_between_string_elements(self):
        text = '{"data_gaps":["第一条缺口" "第二条缺口"]}'
        parsed = cr.parse_report_json(text)
        self.assertEqual(parsed["data_gaps"], ["第一条缺口", "第二条缺口"])

    def test_trailing_comma(self):
        text = '{"a":[1,2,],"b":{"c":1,}}'
        parsed = cr.parse_report_json(text)
        self.assertEqual(parsed["a"], [1, 2])
        self.assertEqual(parsed["b"], {"c": 1})

    def test_extra_data_premature_root_close(self):
        """合肥产投真实形态：正文多一个闭括号使根对象提前闭合（Extra data）。"""
        text = ('{"meta":{"code":"X"},"details":{"company_profile":{"a":1}}}'
                ',"operating_changes":[{"b":2}],"sources":[{"ref":"S1"}]}')
        parsed = cr.parse_report_json(text)
        # 括号重挂后可解析；键位漂移（operating_changes在根级）由validate归位
        self.assertIn("operating_changes", parsed)
        self.assertIn("sources", parsed)

    def test_missing_comma_between_pairs_is_fixed(self):
        """键值对之间缺逗号同样以插逗号修复。"""
        parsed = cr.parse_report_json('{"a": "x" "b": "y"}')
        self.assertEqual(parsed, {"a": "x", "b": "y"})

    def test_unfixable_missing_value_left_for_repair_chain(self):
        """缺失值等规则无法安全修复的噪声原样返回，交给Agent重试/本地修复链。"""
        with self.assertRaises(ValueError):
            cr.parse_report_json('{"meta": }')

    def test_relocation_of_misplaced_keys(self):
        """括号修复后的键位漂移按schema归位：details子键掉到根级、sources掉进details。"""
        report = {
            "meta": {"bond_code": BOND["code"], "research_date": "2026-09-01"},
            "classification": {"type": "CITY_PLATFORM", "confidence": "high", "juyuan_city_flag": "是"},
            "brief": {"company_intro": "市级城投平台。", "one_sentence_conclusion": "稳。",
                      "overall_trend": "stable",
                      "public_opinion": {"verdict": "not_found", "conclusion": "未发现。"}},
            "details": {"company_profile": {"controller": "邯郸市国资委"}, "sources": [
                {"ref": "S1", "title": "聚源查询", "source_name": "聚源数据库", "source_type": "juyuan"}]},
            "operating_changes": [{"title": "变化", "conclusion": "x", "evidence_refs": []}],
            "juyuan_metrics": [
                {"category": "主体财务", "metric": "净利润", "value": "1", "unit": "亿元", "period": "2025"}],
        }
        normalized, warnings = cr.validate_and_normalize(report, BOND)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["details"]["operating_changes"],
                         [{"title": "变化", "conclusion": "x", "evidence_refs": []}])
        self.assertEqual(len(normalized["details"]["juyuan_metrics"]), 1)
        self.assertEqual([s["ref"] for s in normalized["sources"]], ["S1"])
        self.assertTrue(any("operating_changes出现在根级" in w for w in warnings))
        self.assertTrue(any("sources出现在details内" in w for w in warnings))


class WriteJsonRetryTests(unittest.TestCase):
    """Windows 文件占用竞态：os.replace 报拒绝访问时短重试。"""

    def test_write_json_retries_on_permission_error(self):
        import tempfile

        orig_replace, orig_sleep = os.replace, time.sleep
        calls = {"n": 0}

        def _flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(5, "拒绝访问")
            orig_replace(src, dst)

        os.replace = _flaky
        time.sleep = lambda seconds: None
        try:
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "job.json"
                cr._write_json(path, {"job_id": "j1"})
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["job_id"], "j1")
                self.assertGreaterEqual(calls["n"], 3)
                self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())
        finally:
            os.replace, time.sleep = orig_replace, orig_sleep


class ProgressHandlerResilienceTests(unittest.TestCase):
    """进度事件回调中的落盘失败不得打断 Agent 流式调用。"""

    def test_handler_swallows_update_failures(self):
        job = {"job_id": "j1", "issuer_key": "k1", "state": "running", "stage": cr.STAGE_AGENT,
               "detail": "", "progress": 10, "plugin_calls": 0, "answer_chars": 0}
        orig_update = cr._update_job
        cr._update_job = lambda *a, **k: (_ for _ in ()).throw(PermissionError(5, "拒绝访问"))
        try:
            handler = cr._on_agent_event(job)
            handler("plugin", {"count": 1})
            handler("answer_delta", {"chars": 500})
            handler("agent_error", {"message": "x"})
        finally:
            cr._update_job = orig_update
        self.assertEqual(job["plugin_calls"], 1)
        self.assertEqual(job["answer_chars"], 500)


class AgentStreamFilterTests(unittest.TestCase):
    """思考过程与中间消息过滤：只采纳最后一条 message。"""

    def _sse(self, events):
        lines = []
        for outer in events:
            lines.append("data: " + json.dumps(outer, ensure_ascii=False))
        lines.append("")
        return "\n".join(lines).encode("utf-8")

    def _run_stream(self, events):
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
            return mod._run_agent_stream("prompt", lambda kind, payload: captured.append(kind)), captured
        finally:
            mod._agent_session, mod.get_agent_token = orig_session, orig_token

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

        (answer, meta), captured = self._run_stream(events)

        self.assertEqual(answer, "最终结论：该主体为城投。")
        self.assertEqual(meta["plugin_calls"], 1)
        self.assertEqual(meta["model"], "deepseek-v4-flash")
        self.assertIn("plugin", captured)

    def test_completed_message_content_captured_without_deltas(self):
        """最终答案只在 completed 消息事件的 content 里下发时也能取到正文。"""
        mid1, final_id = "msg_m1", "msg_m2"

        def outer(inner):
            return {"data": json.dumps(inner, ensure_ascii=False), "type": "supaw"}

        events = [
            outer({"type": "message", "id": mid1, "status": "completed"}),
            outer({"type": "text", "delta": True, "msg_id": mid1, "text": "中间过程消息。"}),
            outer({"type": "message", "id": final_id, "status": "completed",
                   "content": [{"type": "text", "text": "最终结论：正文在content里。"}]}),
            {"type": "complete"},
        ]

        (answer, _meta), _captured = self._run_stream(events)
        self.assertEqual(answer, "最终结论：正文在content里。")

    def test_nondelta_full_text_event_captured(self):
        """非增量全文 text 事件（无delta标记）同样兜底收录。"""
        final_id = "msg_f"

        def outer(inner):
            return {"data": json.dumps(inner, ensure_ascii=False), "type": "supaw"}

        events = [
            outer({"type": "message", "id": final_id, "status": "in_progress"}),
            outer({"type": "text", "delta": False, "msg_id": final_id, "text": "全文一次性下发。"}),
            outer({"type": "message", "id": final_id, "status": "completed"}),
            {"type": "complete"},
        ]

        (answer, _meta), _captured = self._run_stream(events)
        self.assertEqual(answer, "全文一次性下发。")

    def test_whitespace_message_content_does_not_mask_answer(self):
        """末条消息content为纯空白（真实形态"\\n\\n"）不得抢占真答案。

        曾因此误判"Agent未返回正文"触发整次Agent重跑。"""
        answer_id, tail_id = "msg_a", "msg_t"

        def outer(inner):
            return {"data": json.dumps(inner, ensure_ascii=False), "type": "supaw"}

        events = [
            outer({"type": "message", "id": answer_id, "status": "in_progress"}),
            outer({"type": "text", "delta": True, "msg_id": answer_id, "text": "最终结论：真答案在这里。"}),
            outer({"type": "message", "id": answer_id, "status": "completed"}),
            outer({"type": "message", "id": tail_id, "status": "completed",
                   "content": [{"type": "text", "text": "\n\n"}]}),
            {"type": "complete"},
        ]

        (answer, _meta), _captured = self._run_stream(events)
        self.assertEqual(answer, "最终结论：真答案在这里。")

    def test_answer_streamed_under_reasoning_id_is_recovered(self):
        """个别会话把最终答案标记成reasoning类型消息：取最后含"{"的文本块兜底。

        曾发生"正文已流式输出约2000字仍判Agent未返回正文"连放大重试。"""
        reasoning_id, blank_id = "msg_r", "msg_m"

        def outer(inner):
            return {"data": json.dumps(inner, ensure_ascii=False), "type": "supaw"}

        events = [
            outer({"type": "message", "id": blank_id, "status": "completed",
                   "content": [{"type": "text", "text": "\n\n"}]}),
            outer({"type": "reasoning", "id": reasoning_id, "status": "in_progress"}),
            outer({"type": "text", "delta": True, "msg_id": reasoning_id,
                   "text": '{"meta":{"bond_code":"102682773.IB"},"brief":{"company_intro":"x"}}'}),
            outer({"type": "message", "id": reasoning_id, "status": "completed"}),
            {"type": "complete"},
        ]

        (answer, _meta), _captured = self._run_stream(events)
        self.assertIn('"bond_code":"102682773.IB"', answer)

    def test_watchdog_cuts_keepalive_starved_streams(self):
        """只发心跳不发数据的流：看门狗定时器到点强切连接，不再永久挂起任务。

        曾发生：Agent服务端已完成输出，但SSE连接静默无事件，读超时被心跳重置、
        循环内时间检查因无payload永不执行，任务卡死在running。"""
        import time as _time

        class HangingResp:
            status_code = 200

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

            def iter_lines(self, decode_unicode=False):
                while not self.closed:
                    _time.sleep(0.02)
                    yield b""  # 心跳空行：_iter_sse_data跳过，循环体永远拿不到payload
                raise RuntimeError("connection closed by watchdog")  # 模拟requests被切断后抛错

        resp = HangingResp()

        class FakeSession:
            trust_env = False

            def post(self, *a, **kw):
                return resp

        import bond_detail.credit_research as mod
        orig = (mod._agent_session, mod.get_agent_token, mod.STREAM_MAX_SECONDS)
        mod._agent_session = lambda: FakeSession()
        mod.get_agent_token = lambda force=False: "test-token"
        mod.STREAM_MAX_SECONDS = 0.2
        try:
            with self.assertRaises(RuntimeError) as ctx:
                mod._run_agent_stream("prompt", lambda kind, payload: None)
            self.assertIn("看门狗已切断连接", str(ctx.exception))
            self.assertTrue(resp.closed)
        finally:
            mod._agent_session, mod.get_agent_token, mod.STREAM_MAX_SECONDS = orig

    def test_run_research_prompt_retries_empty_answer_once(self):
        """“Agent未返回正文”属可重试错误：原样重试一次Agent，成功则不走本地链。"""
        calls = {"agent": 0}

        def fake_stream(prompt, on_event):
            calls["agent"] += 1
            if calls["agent"] == 1:
                raise RuntimeError("Agent未返回正文（messages=1 completed=0 text_msgs=0 reasoning=1）")
            return "最终正文", {"channel": "company_agent"}

        orig = (cr.agent_configured, cr._run_agent_stream, cr._local_llm_available, cr._run_local_llm)
        cr.agent_configured = lambda: True
        cr._run_agent_stream = fake_stream
        cr._local_llm_available = lambda: True
        cr._run_local_llm = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应回退本地模型"))
        try:
            answer, meta = cr.run_research_prompt("P", lambda kind, payload: None)
        finally:
            (cr.agent_configured, cr._run_agent_stream,
             cr._local_llm_available, cr._run_local_llm) = orig
        self.assertEqual(calls["agent"], 2)
        self.assertEqual(answer, "最终正文")
        self.assertEqual(meta["channel"], "company_agent")


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

    def test_norm_period_folds_date_forms_into_report_periods(self):
        """日期式报告期按报表日月归期：防止"2026-06-30"被截成孤立的"2026"列。"""
        self.assertEqual(cr._norm_period("2026-06-30"), "2026H1")
        self.assertEqual(cr._norm_period("2026年6月末"), "2026H1")
        self.assertEqual(cr._norm_period("2026年6月30日"), "2026H1")
        self.assertEqual(cr._norm_period("2025.06.30"), "2025H1")
        self.assertEqual(cr._norm_period("2026-09-30"), "2026Q3")
        self.assertEqual(cr._norm_period("2025年3月31日"), "2025Q1")
        self.assertEqual(cr._norm_period("2023-12-31"), "2023")
        self.assertEqual(cr._norm_period("2024年12月31日"), "2024")
        # 非季末日期与年中快照月归入所在年份，不拆新列
        self.assertEqual(cr._norm_period("2026-01-13"), "2026")
        self.assertEqual(cr._norm_period("2026-07"), "2026")
        self.assertEqual(cr._norm_period("2026-07快照"), "2026")
        self.assertEqual(cr._norm_period("2023-2025年"), "2023")
        self.assertEqual(cr._norm_period("2025年东方金诚"), "2025")

    def test_financial_completeness_warnings_against_baseline(self):
        """主体财务指标缺基准报告期时记入校验警告，矩形缺口可诊断。"""
        report = _sample_report()
        report["details"]["juyuan_metrics"] = [
            {"category": "主体财务", "metric": "货币资金", "value": "9.88", "unit": "亿元", "period": "2026-06-30"},
            {"category": "主体财务", "metric": "营业收入", "value": "23.25", "unit": "亿元", "period": "2025年报"},
        ]
        normalized, warnings = cr.validate_and_normalize(report, BOND)
        self.assertIsNotNone(normalized)
        years, interim = cr._report_periods()
        fin = [m for m in normalized["details"]["juyuan_metrics"] if m["category"] == "主体财务"]
        self.assertEqual([m["period"] for m in fin], [interim, str(years[-1])])
        joined = " ".join(warnings)
        self.assertIn("货币资金", joined)
        self.assertIn("营业收入", joined)
        self.assertIn("期数据", joined)
        # 覆盖全部基准期的指标不产生缺期警告
        report2 = _sample_report()
        full_rows = []
        for year in years:
            full_rows.append({"category": "主体财务", "metric": "净利润", "value": "1", "unit": "亿元", "period": str(year)})
        full_rows.append({"category": "主体财务", "metric": "净利润", "value": "0.5", "unit": "亿元", "period": interim})
        report2["details"]["juyuan_metrics"] = full_rows
        _normalized2, warnings2 = cr.validate_and_normalize(report2, BOND)
        self.assertFalse(any("净利润" in w for w in warnings2))

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


def _full_report_json(metrics_count=12):
    """合法的完整研究报告JSON（juyuan_metrics可指定条数）。"""
    return {
        "meta": {"bond_code": BOND["code"], "research_date": "2026-09-01"},
        "classification": {"type": "CITY_PLATFORM", "confidence": "high", "juyuan_city_flag": "是"},
        "brief": {"company_intro": "市级城投平台。", "one_sentence_conclusion": "区域支持稳定。",
                  "overall_trend": "stable", "public_opinion": {"verdict": "not_found", "conclusion": "未发现。"}},
        "details": {
            "juyuan_metrics": [
                {"category": "主体财务", "metric": f"指标{i}", "value": str(i), "unit": "亿元", "period": "2025"}
                for i in range(metrics_count)
            ],
        },
        "sources": [{"ref": "S1", "title": "聚源查询", "source_name": "聚源数据库", "source_type": "juyuan"}],
    }


class RunJobParseFlowTests(unittest.TestCase):
    """JSON语法损坏或必填数据表不达标时：先重试Agent，再退本地修复。"""

    def setUp(self):
        import sys
        import types

        stub = types.ModuleType("bond_detail.service")
        stub._bond_indexes = lambda: ({}, {BOND["code"]: dict(BOND)}, {})
        self._stub = stub
        self._orig_module = sys.modules.get("bond_detail.service")
        sys.modules["bond_detail.service"] = stub

        self._written = []
        self._patches = {
            "build_snapshot": (cr.build_snapshot, lambda bond: ({}, [])),
            "get_cached_report": (cr.get_cached_report, lambda issuer: None),
            "agent_configured": (cr.agent_configured, lambda: True),
            "build_prompt": (cr.build_prompt, lambda code, snapshot, **kw: "PROMPT"),
            "_update_job": (cr._update_job, lambda job, **kw: None),
            "_write_json": (cr._write_json, lambda path, payload: self._written.append((path, payload))),
        }
        self._saved = {name: getattr(cr, name) for name in self._patches}
        for name, (_, fake) in self._patches.items():
            setattr(cr, name, fake)

    def tearDown(self):
        import sys

        for name, original in self._saved.items():
            setattr(cr, name, original)
        if self._orig_module is not None:
            sys.modules["bond_detail.service"] = self._orig_module
        else:
            sys.modules.pop("bond_detail.service", None)

    @staticmethod
    def _job():
        return {"job_id": "j-parse", "issuer_key": "k-parse", "bond_code": BOND["code"], "mode": "full",
                "state": "running", "stage": cr.STAGE_AGENT, "detail": "", "progress": 50,
                "plugin_calls": 0, "answer_chars": 0, "started_at": "2026-09-01 10:00:00",
                "updated_at": "2026-09-01 10:00:00", "updated_ts": time.time(),
                "stage_started_at": time.time(), "error": ""}

    def _run_with_script(self, agent_outputs, repair_outputs=None):
        """按脚本依次返回Agent输出（重试也耗尽后按需走本地修复），返回调用与落盘记录。"""
        calls = {"agent": 0, "repair": 0, "repair_hints": []}

        def fake_run(prompt, on_event, fallback_prompt=None):
            calls["agent"] += 1
            return agent_outputs[min(calls["agent"], len(agent_outputs)) - 1]

        def fake_repair(raw_text, error_hint=""):
            calls["repair"] += 1
            calls["repair_hints"].append(error_hint)
            self.assertTrue(raw_text)
            if repair_outputs is None:
                raise AssertionError("不应触发本地修复")
            return repair_outputs[min(calls["repair"], len(repair_outputs)) - 1]

        orig_run, orig_repair = cr.run_research_prompt, cr.repair_json_with_local_llm
        cr.run_research_prompt, cr.repair_json_with_local_llm = fake_run, fake_repair
        try:
            cr._run_job(self._job())
        finally:
            cr.run_research_prompt, cr.repair_json_with_local_llm = orig_run, orig_repair
        return calls, self._written

    def test_broken_json_retries_agent_before_local_repair(self):
        """Agent输出JSON语法损坏时原样重试，而不是让本地修复器丢数据。"""
        calls, written = self._run_with_script([
            ('前言 {"meta":{}}，数据很好但语法坏了', {"channel": "company_agent", "plugin_calls": 5}),
            (json.dumps(_full_report_json(), ensure_ascii=False), {"channel": "company_agent", "plugin_calls": 6}),
        ])
        self.assertEqual(calls["agent"], 2)
        self.assertEqual(calls["repair"], 0)
        self.assertEqual(len(written), 1)
        payload = written[0][1]
        self.assertEqual(len(payload["report"]["details"]["juyuan_metrics"]), 12)

    def test_metrics_below_floor_retries_then_accepts_with_warning(self):
        """必填数据表不足12条：重试一次；仍不足则接受但记录警告。"""
        calls, written = self._run_with_script([
            (json.dumps(_full_report_json(metrics_count=3), ensure_ascii=False), {"channel": "company_agent"}),
            (json.dumps(_full_report_json(metrics_count=3), ensure_ascii=False), {"channel": "company_agent"}),
        ])
        self.assertEqual(calls["agent"], 2)
        payload = written[0][1]
        self.assertEqual(len(payload["report"]["details"]["juyuan_metrics"]), 3)
        self.assertTrue(any("少于要求" in w for w in payload["validation"]["warnings"]))

    def test_repair_used_when_retry_still_broken(self):
        """重试仍不合法时才走本地修复，且修复结果保留数据行。"""
        calls, written = self._run_with_script(
            agent_outputs=[("坏JSON {", {"channel": "company_agent"})] * 2,
            repair_outputs=[json.dumps(_full_report_json(), ensure_ascii=False)],
        )
        self.assertEqual(calls["agent"], 2)
        self.assertEqual(calls["repair"], 1)
        payload = written[0][1]
        self.assertEqual(len(payload["report"]["details"]["juyuan_metrics"]), 12)
        self.assertTrue(any("格式修复" in w for w in payload["validation"]["warnings"]))

    def test_repair_losing_metrics_triggers_feedback_round(self):
        """修复模型丢数据表时不采用：带明确反馈基于原始文本再修一轮。"""
        calls, written = self._run_with_script(
            agent_outputs=[("坏JSON {", {"channel": "company_agent"})] * 2,
            repair_outputs=[
                json.dumps(_full_report_json(metrics_count=3), ensure_ascii=False),  # 第1轮丢表
                json.dumps(_full_report_json(metrics_count=12), ensure_ascii=False),  # 第2轮恢复
            ],
        )
        self.assertEqual(calls["agent"], 2)
        self.assertEqual(calls["repair"], 2)
        # 第2轮修复拿到丢表反馈，且输入仍是原始救活文本而非丢表结果
        self.assertIn("丢失了juyuan_metrics", calls["repair_hints"][1])
        payload = written[0][1]
        self.assertEqual(len(payload["report"]["details"]["juyuan_metrics"]), 12)
        joined = " ".join(payload["validation"]["warnings"])
        self.assertIn("已经本地模型格式修复（第2次）", joined)


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
