import json
import os
import re
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash


TEST_DATA_ROOT = Path(__file__).resolve().parent / ".test_runtime" / uuid.uuid4().hex
os.environ["PORTAL_DATA_ROOT"] = str(TEST_DATA_ROOT)
os.environ["SECRET_KEY"] = "internal-kb-test-secret"
os.environ["SITE_PASSWORD"] = "portal-test-password"

from app import app  # noqa: E402
from internal_knowledge_base import routes  # noqa: E402
from internal_knowledge_base.routes import (  # noqa: E402
    KNOWLEDGE_INTENT_GENERAL, KNOWLEDGE_INTENT_RETRIEVAL, KNOWLEDGE_SOURCE_MARKER,
    PREVIEW_CACHE_DIR, _index_knowledge_report, _knowledge_candidates, _knowledge_intent,
    _knowledge_context, _knowledge_qa_messages, _knowledge_retrieval_query,
    _parse_knowledge_answer,
    _parse_knowledge_filters, _provider_payload, _report_matches_knowledge_filters, store,
)


def assert_isolated_test_store():
    """Fail closed before destructive fixture cleanup if app import order leaked production paths."""
    test_runtime = (Path(__file__).resolve().parent / ".test_runtime").resolve()
    store_path = Path(store.path).resolve()
    try:
        store_path.relative_to(test_runtime)
    except ValueError as exc:
        raise RuntimeError(f"拒绝清理非测试知识库：{store_path}") from exc


def parse_sse(response):
    """解析测试客户端缓冲下来的 SSE 响应体，返回事件 dict 列表。"""
    events = []
    for block in response.get_data(as_text=True).split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


class InternalKnowledgeBaseTest(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        assert_isolated_test_store()
        with store.transaction() as conn:
            for table in ("audit_log", "pdf_cache", "report_summaries", "engagement", "ratings",
                          "reports", "roadshow_schedule", "qa_usage", "qa_history", "users"):
                conn.execute(f"DELETE FROM {table}")
        store.add_user({
            "id": "member", "name": "测试成员", "org": "固收中心", "role": "member",
            "password_hash": generate_password_hash("member-password"),
        })
        store.set_admin_password_hash(generate_password_hash("admin-password"))
        self.client = app.test_client()

    def portal_session(self):
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def csrf(self, path="/internal-knowledge-base/"):
        response = self.client.get(path)
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_portal_gate_and_namespaced_login(self):
        response = self.client.get("/internal-knowledge-base/")
        self.assertEqual(response.status_code, 302)
        self.portal_session()
        token = self.csrf()
        missing_csrf = self.client.post("/internal-knowledge-base/api/login", json={
            "username": "member", "password": "member-password",
        })
        self.assertEqual(missing_csrf.status_code, 403)
        response = self.client.post("/internal-knowledge-base/api/login", json={
            "username": "member", "password": "member-password",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["internal_knowledge_base_user_id"], "member")
            self.assertTrue(session["authenticated"])

    def test_database_starts_without_business_records_and_password_is_hashed(self):
        self.assertEqual(store.reports(), [])
        self.assertEqual(store.ratings(), [])
        user = store.get_user("member")
        self.assertNotEqual(user["password_hash"], "member-password")
        self.assertNotIn("password", user)

    def test_reminder_config_is_separate_from_empty_business_records(self):
        store.set_reminder_config({
            "period": "2026", "reportCategory": "deep",
            "rules": [{"id": "rule-1", "label": "测试专题", "mode": "person",
                       "target": 2, "userIds": ["member"]}],
        })
        config = store.reminder_config()
        self.assertEqual(config["period"], "2026")
        self.assertEqual(config["rules"][0]["target"], 2)
        self.assertEqual(store.reports(), [])
        self.assertEqual(store.ratings(), [])

    def test_admin_route_is_isolated_and_password_can_change(self):
        self.portal_session()
        portal_admin = self.client.get("/admin")
        self.assertEqual(portal_admin.status_code, 302)
        token = self.csrf("/internal-knowledge-base/admin")
        response = self.client.post("/internal-knowledge-base/api/admin/login",
                                    json={"password": "admin-password"},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        changed = self.client.post("/internal-knowledge-base/api/admin/change-password", json={
            "oldPassword": "admin-password", "newPassword": "new-admin-password",
            "confirmPassword": "new-admin-password",
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(changed.status_code, 200)
        self.client.post("/internal-knowledge-base/api/admin/logout",
                         headers={"X-CSRF-Token": token})
        old_login = self.client.post("/internal-knowledge-base/api/admin/login",
                                     json={"password": "admin-password"},
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(old_login.status_code, 401)
        new_login = self.client.post("/internal-knowledge-base/api/admin/login",
                                     json={"password": "new-admin-password"},
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(new_login.status_code, 200)

    def test_superadmin_can_list_and_delete_permanent_pdf_cache(self):
        self.portal_session()
        token = self.csrf("/internal-knowledge-base/admin")
        self.client.post("/internal-knowledge-base/api/admin/login",
                         json={"password": "admin-password"},
                         headers={"X-CSRF-Token": token})
        key = "a" * 64
        cache_path = Path(PREVIEW_CACHE_DIR, f"{key}.pdf")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"%PDF-1.4\n%%EOF")
        store.upsert_pdf_cache(key, None, "b" * 64, cache_path.name,
                               cache_path.stat().st_size, "test-version")
        listing = self.client.get("/internal-knowledge-base/api/admin/pdf-cache")
        self.assertEqual(listing.get_json()["count"], 1)
        deleted = self.client.delete(f"/internal-knowledge-base/api/admin/pdf-cache/{key}",
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(cache_path.exists())
        self.assertEqual(store.pdf_caches(), [])

    def test_knowledge_search_defaults_to_all_report_types(self):
        filters = _parse_knowledge_filters({"period": "all"})
        self.assertEqual(filters["report_types"], [])
        self.assertTrue(_report_matches_knowledge_filters(
            {"reportType": "external", "reportDate": "2026-08-20"}, filters
        ))
        self.assertTrue(_report_matches_knowledge_filters(
            {"reportType": "roadshow", "reportDate": "2026-08-20"}, filters
        ))

    def test_knowledge_intent_is_manual_and_defaults_to_free_qa(self):
        self.assertEqual(_knowledge_intent(), KNOWLEDGE_INTENT_GENERAL)
        self.assertEqual(_knowledge_intent("free"), KNOWLEDGE_INTENT_GENERAL)
        self.assertEqual(_knowledge_intent("report_retrieval"), KNOWLEDGE_INTENT_RETRIEVAL)
        # 问题正文不再参与类型判定，误把正文当类型会被拒绝。
        with self.assertRaises(ValueError):
            _knowledge_intent("有哪些报告讨论了城投利差？")

    def test_free_qa_context_is_bounded_and_enriches_followup_retrieval(self):
        context = _knowledge_context({"context": [
            {"role": "user", "content": "比较城投债与产业债的信用利差"},
            {"role": "assistant", "content": "两者驱动因素有所不同。", "sources": [
                {"id": "report-1", "title": "城投与产业债利差比较"},
            ]},
            {"role": "tool", "content": "应被忽略"},
        ]})
        self.assertEqual([item["role"] for item in context], ["user", "assistant"])
        self.assertEqual(context[1]["sources"][0]["title"], "城投与产业债利差比较")
        query = _knowledge_retrieval_query("展开第二点", context)
        self.assertIn("城投债与产业债", query)
        self.assertIn("城投与产业债利差比较", query)
        self.assertIn("展开第二点", query)

    def test_knowledge_prompt_switches_between_two_frameworks(self):
        candidates = [{
            "id": "r001", "title": "信用利差周度观察", "author": "研究员甲",
            "published_at": "2026-08-20", "theme": "信用", "category": "周报",
            "content": "本周信用利差整体收窄，短端表现更为明显。",
        }]
        retrieval_system, retrieval_prompt = _knowledge_qa_messages(
            "这篇报告的核心观点是什么？", candidates,
            intent=KNOWLEDGE_INTENT_RETRIEVAL,
        )
        general_system, general_prompt = _knowledge_qa_messages(
            "展开第二点", candidates, intent=KNOWLEDGE_INTENT_GENERAL,
            context=[
                {"role": "user", "content": "比较这批报告的观点"},
                {"role": "assistant", "content": "主要有两点共识。"},
            ],
            thinking=True,
        )
        self.assertIn("严格使用以下模板", retrieval_system)
        self.assertIn("不强制套用逐篇报告摘要模板", general_system)
        self.assertIn("善于连续对话", general_system)
        self.assertIn("深度思考已开启", general_system)
        self.assertIn("禁止展示 report-upload", general_system)
        self.assertIn(KNOWLEDGE_SOURCE_MARKER, retrieval_system)
        self.assertIn(KNOWLEDGE_SOURCE_MARKER, general_system)
        self.assertIn("手动选择了找报告", retrieval_prompt)
        self.assertIn("手动选择了自由问答", general_prompt)
        self.assertIn("比较这批报告的观点", general_prompt)
        self.assertIn("展开第二点", general_prompt)
        self.assertIn("[REPORT_ID:r001]", retrieval_prompt)
        self.assertIn("[REPORT_ID:r001]", general_prompt)

    def test_knowledge_answer_uses_real_titles_and_recovers_missing_sources(self):
        candidates = [{
            "id": "report-upload-123-abc", "title": "AI产业趋势报告",
            "author": "研究员甲", "published_at": "2026-08-20",
        }, {
            "id": "report-upload-456-def", "title": "半导体周期复盘",
            "author": "研究员乙", "published_at": "2026-08-18",
        }]
        result = _parse_knowledge_answer(
            "核心来源是报告 `report-upload-123-abc`，并据此判断景气回升。",
            candidates,
        )
        self.assertNotIn("report-upload-123-abc", result["answer"])
        self.assertIn("《AI产业趋势报告》", result["answer"])
        self.assertEqual([item["id"] for item in result["sources"]], ["report-upload-123-abc"])

        fallback = _parse_knowledge_answer("多份报告显示景气度正在改善。", candidates)
        self.assertEqual(len(fallback["sources"]), 2)
        self.assertNotIn("本轮参考报告", fallback["answer"])
        self.assertTrue(fallback["answer"].startswith("本回答主要依据"))
        self.assertIn("《AI产业趋势报告》", fallback["answer"])
        self.assertIn("《半导体周期复盘》", fallback["answer"])

    def test_persistent_vector_index_recalls_relevant_external_report(self):
        relevant = {
            "id": "report-vector-relevant", "title": "城投平台流动性跟踪",
            "summary": "地方政府融资平台再融资改善，信用利差出现收窄。",
            "tags": ["城投", "资金面"], "author": "研究员甲", "authorId": "member",
            "sourceAuthor": "外部作者", "sourceInstitution": "研究机构",
            "reportType": "external", "category": "other", "theme": "credit",
            "reportDate": "2026-08-20", "uploadedAt": "2026-08-20T10:00:00+08:00",
            "fileStored": False, "fileUrl": "",
        }
        unrelated = {
            **relevant, "id": "report-vector-unrelated", "title": "消费行业盈利观察",
            "summary": "食品饮料企业盈利增速与渠道库存分析。", "tags": ["消费"],
        }
        store.add_report(relevant)
        store.add_report(unrelated)
        self.assertTrue(_index_knowledge_report(relevant))
        self.assertTrue(_index_knowledge_report(unrelated))
        self.assertEqual(store.knowledge_index_stats()["reports"], 2)

        candidates = _knowledge_candidates(
            "城投公司的资金面和信用利差有什么变化？",
            filters=_parse_knowledge_filters({"period": "all"}),
        )
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["id"], relevant["id"])
        # Unchanged content uses the persisted vector rather than rebuilding.
        self.assertFalse(_index_knowledge_report(relevant))

    @patch("internal_knowledge_base.routes._llm_api_key", return_value="test-key")
    @patch("internal_knowledge_base.routes._answer_knowledge_question")
    def test_knowledge_api_defaults_to_free_qa_and_persists_conversation(self, answer, _key):
        answer.return_value = {"answer": "自由回答", "sources": []}
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_user_id"] = "member"
            session["internal_knowledge_base_csrf"] = "test-csrf"
        response = self.client.post("/internal-knowledge-base/api/knowledge-search", json={
            "question": "继续展开第二点",
            "conversationId": "conversation-001",
            "thinking": True,
            "context": [
                {"role": "user", "content": "先比较两类报告"},
                {"role": "assistant", "content": "主要有两点差异"},
            ],
        }, headers={"X-CSRF-Token": "test-csrf"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["questionType"], KNOWLEDGE_INTENT_GENERAL)
        self.assertEqual(data["conversationId"], "conversation-001")
        self.assertTrue(data["thinking"])
        kwargs = answer.call_args.kwargs
        self.assertEqual(kwargs["intent"], KNOWLEDGE_INTENT_GENERAL)
        self.assertTrue(kwargs["thinking"])
        self.assertEqual(len(kwargs["context"]), 2)
        history = store.qa_history_for_user("member")
        self.assertEqual(history[0]["conversationId"], "conversation-001")
        self.assertEqual(history[0]["questionType"], KNOWLEDGE_INTENT_GENERAL)
        self.assertTrue(history[0]["thinking"])

    def test_provider_payload_can_enable_or_disable_model_thinking(self):
        messages = [{"role": "user", "content": "问题"}]
        self_provider = {"model": "glm", "chat_template_kwargs": {"enable_thinking": False}}
        self.assertFalse(_provider_payload(
            self_provider, messages, 100, False, thinking=False,
        )["chat_template_kwargs"]["enable_thinking"])
        self.assertTrue(_provider_payload(
            self_provider, messages, 100, False, thinking=True,
        )["chat_template_kwargs"]["enable_thinking"])
        mimo_provider = {"model": "mimo", "disable_thinking": True}
        self.assertEqual(
            _provider_payload(mimo_provider, messages, 100, False, thinking=True)["thinking"],
            {"type": "enabled"},
        )

    def test_knowledge_api_rejects_unknown_manual_type(self):
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_user_id"] = "member"
            session["internal_knowledge_base_csrf"] = "test-csrf"
        with patch("internal_knowledge_base.routes._llm_api_key", return_value="test-key"):
            response = self.client.post("/internal-knowledge-base/api/knowledge-search", json={
                "question": "查找城投报告", "questionType": "auto",
            }, headers={"X-CSRF-Token": "test-csrf"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("问题类型无效", response.get_json()["error"])


class ReportAiSummaryTests(unittest.TestCase):
    """单篇报告 AI 摘要（三版本缓存 + 指纹失效）与就本文提问。"""

    REPORT_ID = "r-ai-001"

    def setUp(self):
        app.config.update(TESTING=True)
        assert_isolated_test_store()
        with store.transaction() as conn:
            for table in ("audit_log", "pdf_cache", "report_summaries", "engagement", "ratings",
                          "reports", "roadshow_schedule", "qa_usage", "qa_history", "users"):
                conn.execute(f"DELETE FROM {table}")
        store.add_user({
            "id": "member", "name": "测试成员", "org": "固收中心", "role": "member",
            "password_hash": generate_password_hash("member-password"),
        })
        # 报告挂一个真实存在的占位文件，保证 report_file_path 可用；正文提取用 mock 控制。
        upload_dir = Path(routes.UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / "ai-summary-fixture.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        store.add_report({
            "id": self.REPORT_ID, "title": "信用利差专题", "author": "研究员甲", "org": "固收中心",
            "reportType": "internal", "category": "credit", "theme": "credit",
            "reportDate": "2026-08-28", "summary": "人工简介", "tags": ["信用"],
            "fileUrl": "uploads/ai-summary-fixture.pdf", "fileStored": True,
            "fileSha256": "sha-001", "uploadedAt": "2026-08-28T10:00:00",
        })
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_user_id"] = "member"
            session["internal_knowledge_base_csrf"] = "test-csrf-token"
        self.headers = {"X-CSRF-Token": "test-csrf-token"}
        self.base = f"/internal-knowledge-base/api/reports/{self.REPORT_ID}/ai-summary"

    def test_summary_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get(f"{self.base}?style=standard")
        self.assertEqual(response.status_code, 401)

    def test_get_summary_without_cache(self):
        response = self.client.get(f"{self.base}?style=standard")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["available"])
        self.assertEqual(data["style"], "standard")

    def test_invalid_style_rejected(self):
        self.assertEqual(self.client.get(f"{self.base}?style=huge").status_code, 400)
        response = self.client.post(self.base, json={"style": "huge"}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    @patch("internal_knowledge_base.routes._llm_api_key", return_value="test-key")
    @patch("internal_knowledge_base.routes._extract_text", return_value="本周信用利差整体收窄，短端表现更为明显。" * 5)
    @patch("internal_knowledge_base.routes._stream_llm")
    def test_generate_summary_streams_and_caches(self, stream_llm, _extract, _key):
        def fake_stream(prompt, system=None, max_tokens=None, provider_sink=None):
            self.assertIn("信用利差专题", prompt)  # 报告元数据进入提示词
            self.assertIn("核心结论", prompt)  # 摘要结构模板进入提示词
            if provider_sink is not None:
                provider_sink.append("self")
            yield "### 核心结论\n"
            yield "- 利差整体收窄"

        stream_llm.side_effect = fake_stream
        response = self.client.post(self.base, json={"style": "standard"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["Content-Type"])
        events = parse_sse(response)
        types = [event["type"] for event in events]
        self.assertIn("stage", types)
        self.assertTrue(any(event["type"] == "delta" for event in events))
        done = [event for event in events if event["type"] == "done"][0]
        self.assertEqual(done["summary"], "### 核心结论\n- 利差整体收窄")
        self.assertEqual(done["model"], "self")
        self.assertEqual(done["generatedByName"], "测试成员")
        # 生成后入库，GET 命中缓存
        cached = self.client.get(f"{self.base}?style=standard").get_json()
        self.assertTrue(cached["available"])
        self.assertEqual(cached["summary"], "### 核心结论\n- 利差整体收窄")
        self.assertEqual(cached["model"], "self")
        # 各版本独立缓存：其他版本仍无缓存
        other = self.client.get(f"{self.base}?style=deep").get_json()
        self.assertFalse(other["available"])

    @patch("internal_knowledge_base.routes._llm_api_key", return_value="test-key")
    @patch("internal_knowledge_base.routes._extract_text", return_value="   ")
    def test_generate_reports_error_when_text_missing(self, _extract, _key):
        response = self.client.post(self.base, json={"style": "concise"}, headers=self.headers)
        events = parse_sse(response)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("提取正文", events[-1]["message"])

    @patch("internal_knowledge_base.routes._llm_api_key", return_value="test-key")
    @patch("internal_knowledge_base.routes._extract_text", return_value="正文")
    @patch("internal_knowledge_base.routes._stream_llm")
    def test_cached_summary_replayed_without_llm_and_force_regenerates(self, stream_llm, _extract, _key):
        store.save_report_summary(self.REPORT_ID, "standard", "member", "缓存中的摘要", "self", "sha-001")
        response = self.client.post(self.base, json={"style": "standard"}, headers=self.headers)
        events = parse_sse(response)
        stream_llm.assert_not_called()
        done = [event for event in events if event["type"] == "done"][0]
        self.assertEqual(done["summary"], "缓存中的摘要")

        stream_llm.side_effect = lambda *args, **kwargs: iter(["重新生成的摘要"])
        response = self.client.post(self.base, json={"style": "standard", "force": True}, headers=self.headers)
        events = parse_sse(response)
        stream_llm.assert_called_once()
        done = [event for event in events if event["type"] == "done"][0]
        self.assertEqual(done["summary"], "重新生成的摘要")
        cached = self.client.get(f"{self.base}?style=standard").get_json()
        self.assertEqual(cached["summary"], "重新生成的摘要")

    def test_summary_cache_invalidates_on_file_change(self):
        store.save_report_summary(self.REPORT_ID, "standard", "member", "旧摘要", "self", "sha-001")
        fresh = self.client.get(f"{self.base}?style=standard").get_json()
        self.assertTrue(fresh["available"])

        store.save_report_summary(self.REPORT_ID, "standard", "member", "旧摘要", "self", "sha-002")
        stale = self.client.get(f"{self.base}?style=standard").get_json()
        self.assertFalse(stale["available"])

    @patch("internal_knowledge_base.routes._llm_api_key", return_value="test-key")
    @patch("internal_knowledge_base.routes._extract_text", return_value="报告正文内容")
    @patch("internal_knowledge_base.routes._stream_llm")
    def test_report_ask_streams_without_persistence(self, stream_llm, _extract, _key):
        stream_llm.side_effect = lambda prompt, system=None, max_tokens=None, provider_sink=None: iter(["回答第一段", "，回答第二段"])
        response = self.client.post(
            f"/internal-knowledge-base/api/reports/{self.REPORT_ID}/ask",
            json={"question": "这篇报告的核心结论是什么？"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        events = parse_sse(response)
        done = [event for event in events if event["type"] == "done"][0]
        self.assertEqual(done["answer"], "回答第一段，回答第二段")
        # 问答不写任何业务表（不占知识搜索额度、不留历史、不产生摘要缓存）
        with store.connect() as conn:
            for table in ("qa_usage", "qa_history", "report_summaries"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_report_ask_validates_question(self):
        short = self.client.post(
            f"/internal-knowledge-base/api/reports/{self.REPORT_ID}/ask",
            json={"question": "好"}, headers=self.headers,
        )
        self.assertEqual(short.status_code, 400)
        long = self.client.post(
            f"/internal-knowledge-base/api/reports/{self.REPORT_ID}/ask",
            json={"question": "长" * 501}, headers=self.headers,
        )
        self.assertEqual(long.status_code, 400)


if __name__ == "__main__":
    unittest.main()
