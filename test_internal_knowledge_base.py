import base64
import io
import json
import os
import re
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from werkzeug.security import generate_password_hash


TEST_DATA_ROOT = Path(__file__).resolve().parent / ".test_runtime" / uuid.uuid4().hex
os.environ["PORTAL_DATA_ROOT"] = str(TEST_DATA_ROOT)
os.environ["SECRET_KEY"] = "internal-kb-test-secret"
os.environ["SITE_PASSWORD"] = "portal-test-password"
# 测试用小密钥加速首次生成（生产默认 4096，见 auth_crypto._key_bits）
os.environ["KB_AUTH_KEY_BITS"] = "2048"

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


def seal_credential(client, payload):
    """模拟前端 KbCrypto.seal：取挑战 → RSA-OAEP 加密 payload+nonce。

    登录/改密接口只接受 {credential} 信封（渗透测试"传输过程信息泄漏"整改），
    明文密码不再出现在请求体中。
    """
    challenge = client.get("/internal-knowledge-base/api/auth-challenge")
    assert challenge.status_code == 200, challenge.get_data(as_text=True)
    challenge = challenge.get_json()
    pub = challenge["publicKey"]
    public_key = rsa.RSAPublicNumbers(int(pub["e"], 16), int(pub["n"], 16)).public_key()
    message = json.dumps(dict(payload, nonce=challenge["nonce"]), ensure_ascii=False).encode("utf-8")
    ciphertext = public_key.encrypt(
        message,
        asym_padding.OAEP(mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                          algorithm=hashes.SHA256(), label=None))
    return {"credential": base64.b64encode(ciphertext).decode("ascii")}


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

    def test_portal_gate_removed_and_namespaced_login(self):
        response = self.client.get("/internal-knowledge-base/")
        self.assertEqual(response.status_code, 200)
        token = self.csrf()
        missing_csrf = self.client.post("/internal-knowledge-base/api/login",
                                        json=seal_credential(self.client, {
                                            "username": "member", "password": "member-password",
                                        }))
        self.assertEqual(missing_csrf.status_code, 403)
        response = self.client.post("/internal-knowledge-base/api/login",
                                    json=seal_credential(self.client, {
                                        "username": "member", "password": "member-password",
                                    }), headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["internal_knowledge_base_user_id"], "member")

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
                                    json=seal_credential(self.client, {"password": "admin-password"}),
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        changed = self.client.post("/internal-knowledge-base/api/admin/change-password",
                                   json=seal_credential(self.client, {
                                       "oldPassword": "admin-password", "newPassword": "new-admin-password",
                                       "confirmPassword": "new-admin-password",
                                   }), headers={"X-CSRF-Token": token})
        self.assertEqual(changed.status_code, 200)
        self.client.post("/internal-knowledge-base/api/admin/logout",
                         headers={"X-CSRF-Token": token})
        old_login = self.client.post("/internal-knowledge-base/api/admin/login",
                                     json=seal_credential(self.client, {"password": "admin-password"}),
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(old_login.status_code, 401)
        new_login = self.client.post("/internal-knowledge-base/api/admin/login",
                                     json=seal_credential(self.client, {"password": "new-admin-password"}),
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(new_login.status_code, 200)

    def test_superadmin_can_list_and_delete_permanent_pdf_cache(self):
        self.portal_session()
        token = self.csrf("/internal-knowledge-base/admin")
        self.client.post("/internal-knowledge-base/api/admin/login",
                         json=seal_credential(self.client, {"password": "admin-password"}),
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


class ReportFileEndpointTests(unittest.TestCase):
    """文件接口：默认附件下载，?inline=1 供 iframe 在线查看流式加载。"""

    REPORT_ID = "r-file-001"

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
        upload_dir = Path(routes.UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / "file-fixture.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        store.add_report({
            "id": self.REPORT_ID, "title": "文件接口测试", "author": "研究员甲", "org": "固收中心",
            "reportType": "internal", "category": "credit", "theme": "credit",
            "reportDate": "2026-09-14", "summary": "", "tags": [],
            "fileName": "文件接口测试_中文.pdf",
            "fileUrl": "uploads/file-fixture.pdf", "fileStored": True,
            "fileSha256": "sha-file-001", "uploadedAt": "2026-09-14T10:00:00",
        })
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_user_id"] = "member"

    def test_file_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get(f"/internal-knowledge-base/api/reports/{self.REPORT_ID}/file")
        self.assertEqual(response.status_code, 401)

    def test_file_download_is_attachment_by_default(self):
        response = self.client.get(f"/internal-knowledge-base/api/reports/{self.REPORT_ID}/file")
        self.assertEqual(response.status_code, 200)
        disposition = response.headers.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("application/pdf", response.headers.get("Content-Type", ""))

    def test_file_inline_for_preview(self):
        response = self.client.get(
            f"/internal-knowledge-base/api/reports/{self.REPORT_ID}/file?inline=1")
        self.assertEqual(response.status_code, 200)
        disposition = response.headers.get("Content-Disposition", "")
        self.assertNotIn("attachment", disposition)
        self.assertIn("inline", disposition)

    @unittest.skipIf(routes.SOFFICE is None, "LibreOffice 不可用")
    def test_preview_get_branch_uses_report_file_url(self):
        # 前端非 PDF 预览改走 GET /api/preview?file=，此处覆盖定位逻辑（PDF 直接透传）
        response = self.client.get(
            "/internal-knowledge-base/api/preview?file=uploads/file-fixture.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/pdf", response.headers.get("Content-Type", ""))


class UserDeletionRetentionTests(unittest.TestCase):
    """删除用户改为软删除：历史上传报告、评分、点赞浏览等全部保留且姓名照常显示。"""

    REPORT_AUTHOR = "r-del-author"   # 离职作者上传的深度报告
    REPORT_MEMBER = "r-del-member"   # 在职成员上传、被离职作者评过分的深度报告

    def setUp(self):
        app.config.update(TESTING=True)
        assert_isolated_test_store()
        with store.transaction() as conn:
            for table in ("audit_log", "pdf_cache", "report_summaries", "engagement", "ratings",
                          "reports", "roadshow_schedule", "qa_usage", "qa_history", "users"):
                conn.execute(f"DELETE FROM {table}")
        for uid, name, role in (("leader", "测试领导", "leader"),
                                ("author", "离职作者", "member"),
                                ("member", "在职成员", "member")):
            store.add_user({
                "id": uid, "name": name, "org": "固收中心", "role": role,
                "password_hash": generate_password_hash(f"{uid}-password"),
            })
        store.set_admin_password_hash(generate_password_hash("admin-password"))
        for rid, author_id, author_name in ((self.REPORT_AUTHOR, "author", "离职作者"),
                                            (self.REPORT_MEMBER, "member", "在职成员")):
            store.add_report({
                "id": rid, "title": f"深度报告-{rid}", "author": author_name,
                "authorId": author_id, "org": "固收中心", "reportType": "internal",
                "category": "deep", "theme": "credit", "reportDate": "2026-10-20",
                "summary": "", "tags": [], "fileUrl": "", "fileStored": False,
                "uploadedAt": "2026-10-20T10:00:00",
            })
        store.add_rating({
            "id": "rating-del-1", "reportId": self.REPORT_MEMBER, "userId": "author",
            "inspiration": 8, "depth": 7, "utility": 9,
            "comment": "离职前留下的点评", "updatedAt": "2026-10-21T09:00:00",
        })
        store.add_view(self.REPORT_MEMBER, "author")
        self.client = app.test_client()

    def _session(self, user_id=None, superadmin=False):
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_csrf"] = "test-csrf-token"
            if user_id:
                session["internal_knowledge_base_user_id"] = user_id
            if superadmin:
                session["internal_knowledge_base_superadmin"] = True

    def _delete_author(self):
        self._session(superadmin=True)
        response = self.client.delete("/internal-knowledge-base/api/admin/users/author",
                                      headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(response.status_code, 200)

    def test_delete_user_keeps_reports_ratings_engagement_and_names(self):
        self._delete_author()

        # 数据库层面：评分与浏览记录仍在，用户行保留（软删除）
        self.assertEqual(len(store.ratings()), 1)
        self.assertIsNotNone(store.get_user("author", include_deleted=True))
        self.assertIsNone(store.get_user("author"))
        engagement = store.report_engagement(self.REPORT_MEMBER)
        self.assertEqual(engagement["viewCount"], 1)

        # 领导端评分列表：历史评分保留且显示原姓名，而非"已删除用户"
        self._session(user_id="leader")
        data = self.client.get("/internal-knowledge-base/api/ratings").get_json()
        self.assertEqual(len(data["ratings"]), 1)
        self.assertEqual(data["ratings"][0]["userName"], "离职作者")
        self.assertEqual(data["ratings"][0]["comment"], "离职前留下的点评")
        # 报告列表：离职作者的历史报告仍在，署名保留
        reports = self.client.get("/internal-knowledge-base/api/reports").get_json()["reports"]
        by_id = {r["id"]: r for r in reports}
        self.assertIn(self.REPORT_AUTHOR, by_id)
        self.assertEqual(by_id[self.REPORT_AUTHOR]["author"], "离职作者")
        self.assertEqual(by_id[self.REPORT_AUTHOR]["authorId"], "author")
        # 汇总统计：评分数与参与人数不因删号缩水
        self.assertEqual(data["summary"]["totalRatings"], 1)
        self.assertEqual(data["summary"]["participants"], 1)

        # 管理后台：活跃列表不含该账号，已删除列表含删除时间
        self._session(superadmin=True)
        users = self.client.get("/internal-knowledge-base/api/admin/users").get_json()
        self.assertNotIn("author", {u["id"] for u in users["users"]})
        deleted = {u["id"]: u for u in users["deletedUsers"]}
        self.assertIn("author", deleted)
        self.assertTrue(deleted["author"]["deletedAt"])
        self.assertEqual(deleted["author"]["name"], "离职作者")
        stats = self.client.get("/internal-knowledge-base/api/admin/stats").get_json()
        self.assertEqual(stats["ratings"], 1)
        self.assertEqual(stats["participants"], 1)

    def test_deleted_user_cannot_login_and_existing_session_dies(self):
        self._delete_author()

        client = app.test_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_csrf"] = "test-csrf-token"
        response = client.post("/internal-knowledge-base/api/login",
                               json=seal_credential(client, {"username": "author", "password": "author-password"}),
                               headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(response.status_code, 401)

        self._session(user_id="author")
        self.assertEqual(self.client.get("/internal-knowledge-base/api/me").status_code, 401)

    def test_deleted_user_leaves_scorer_pool(self):
        self._delete_author()

        # 删除后不再是应评分人员：评分进度分母剔除该账号，已交评分仍计入总分
        self._session(user_id="leader")
        status = self.client.get(
            f"/internal-knowledge-base/api/reports/{self.REPORT_MEMBER}/scoring-status"
        ).get_json()
        self.assertNotIn("author", {s["id"] for s in status["scorers"]})
        self.assertEqual(status["total"], 2)  # 仅剩领导与在职成员
        from internal_knowledge_base.routes import eligible_scorers
        self.assertNotIn("author", {u["id"] for u in eligible_scorers(
            store.get_report(self.REPORT_MEMBER))})

    def test_report_authors_excludes_deleted_user(self):
        store.add_user({
            "id": "clerk", "name": "行政账号", "org": "行政", "role": "admin",
            "password_hash": generate_password_hash("clerk-password"),
        })
        self._delete_author()
        self._session(user_id="clerk")
        authors = self.client.get("/internal-knowledge-base/api/report-authors").get_json()
        self.assertNotIn("author", {u["id"] for u in authors["authors"]})

    def test_restore_user_reactivates_login_with_random_password(self):
        self._delete_author()
        self._session(superadmin=True)
        response = self.client.post("/internal-knowledge-base/api/admin/users/author/restore",
                                    headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["user"]["name"], "离职作者")
        # 恢复即生成随机初始密码（不再是统一默认密码）并要求首登改密
        initial_password = body.get("initialPassword") or ""
        self.assertGreaterEqual(len(initial_password), 8)
        self.assertNotEqual(initial_password, "123456")

        users = self.client.get("/internal-knowledge-base/api/admin/users").get_json()
        self.assertIn("author", {u["id"] for u in users["users"]})
        self.assertEqual(users["deletedUsers"], [])

        # 恢复后旧密码失效；随机初始密码登录进入强制改密，改密成功才建立会话
        client = app.test_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_csrf"] = "test-csrf-token"
        old_login = client.post("/internal-knowledge-base/api/login",
                                json=seal_credential(client, {"username": "author", "password": "author-password"}),
                                headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(old_login.status_code, 401)
        gate = client.post("/internal-knowledge-base/api/login",
                           json=seal_credential(client, {"username": "author", "password": initial_password}),
                           headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(gate.status_code, 200)
        self.assertTrue(gate.get_json().get("mustChangePassword"))
        with client.session_transaction() as session:
            self.assertNotIn("internal_knowledge_base_user_id", session)
            self.assertEqual(session.get("internal_knowledge_base_pending_user_id"), "author")
        completed = client.post("/internal-knowledge-base/api/complete-login-password",
                                json=seal_credential(client, {"oldPassword": initial_password,
                                                              "newPassword": "author-new-pass-2026"}),
                                headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(completed.status_code, 200)
        with client.session_transaction() as session:
            self.assertEqual(session.get("internal_knowledge_base_user_id"), "author")
        self.assertEqual(len(store.ratings()), 1)

    def test_create_user_rejects_id_of_deleted_account(self):
        self._delete_author()
        self._session(superadmin=True)
        headers = {"X-CSRF-Token": "test-csrf-token"}
        payload = {"id": "author", "name": "新人", "org": "固收中心", "role": "member"}
        response = self.client.post("/internal-knowledge-base/api/admin/users",
                                    json=seal_credential(self.client, payload),
                                    headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("已被删除", response.get_json()["error"])
        # 恢复后同一账号回到"账号已存在"分支
        self.client.post("/internal-knowledge-base/api/admin/users/author/restore", headers=headers)
        response = self.client.post("/internal-knowledge-base/api/admin/users",
                                    json=seal_credential(self.client, payload),
                                    headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "账号已存在")

    def test_summary_generator_name_survives_deletion(self):
        from internal_knowledge_base.routes import _summary_payload
        store.save_report_summary(self.REPORT_AUTHOR, "standard", "author",
                                  "摘要内容", "test-model", "sha-001")
        self._delete_author()
        row = store.get_report_summary(self.REPORT_AUTHOR, "standard")
        payload = _summary_payload(row, "standard", "author")
        self.assertEqual(payload["generatedByName"], "离职作者")


def _make_malicious_pdf(path):
    """构造带内嵌脚本的 PDF：文档级 JS 名称树 + OpenAction JS + 页面 /AA 启动动作。"""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    js_action = doc.get_new_xref()
    doc.update_object(js_action, "<< /S /JavaScript /JS (app.alert({cMsg:'pdf-xss'})) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js_action} 0 R")
    launch_action = doc.get_new_xref()
    doc.update_object(launch_action, "<< /S /Launch /F (calc.exe) >>")
    doc.xref_set_key(page.xref, "AA", f"<< /O {launch_action} 0 R >>")
    doc.save(str(path))
    doc.close()


def _make_clean_pdf(path):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "clean report")
    doc.save(str(path))
    doc.close()


def _pdf_has_active_content(path):
    import pymupdf
    doc = pymupdf.open(str(path))
    try:
        pattern = re.compile(r"/(JavaScript|JS|Launch)(?![0-9A-Za-z])")
        for xref in range(1, doc.xref_length()):
            source = doc.xref_object(xref, compressed=False) or ""
            if pattern.search(source):
                return True
        catalog = doc.xref_object(doc.pdf_catalog(), compressed=False) or ""
        return bool(pattern.search(catalog))
    finally:
        doc.close()


class SecurityHardeningTests(unittest.TestCase):
    """渗透测试整改回归：信封登录、强制改密、随机初始密码、PDF 活跃内容清洗。"""

    LEGACY_USER = "legacy"
    NORMAL_USER = "normal"

    def setUp(self):
        app.config.update(TESTING=True)
        assert_isolated_test_store()
        with store.transaction() as conn:
            for table in ("audit_log", "pdf_cache", "report_summaries", "engagement", "ratings",
                          "reports", "roadshow_schedule", "qa_usage", "qa_history", "users"):
                conn.execute(f"DELETE FROM {table}")
        store.add_user({
            "id": self.LEGACY_USER, "name": "默认密码用户", "org": "固收中心", "role": "member",
            "password_hash": generate_password_hash("123456"),
        })
        store.add_user({
            "id": self.NORMAL_USER, "name": "正常用户", "org": "固收中心", "role": "member",
            "password_hash": generate_password_hash("normal-pass-2026"),
        })
        store.set_admin_password_hash(generate_password_hash("admin-password"))
        self.client = app.test_client()

    def _session(self, user_id=None, superadmin=False):
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["internal_knowledge_base_csrf"] = "test-csrf-token"
            if user_id:
                session["internal_knowledge_base_user_id"] = user_id
            if superadmin:
                session["internal_knowledge_base_superadmin"] = True

    def _login(self, client, username, password):
        return client.post("/internal-knowledge-base/api/login",
                           json=seal_credential(client, {"username": username, "password": password}),
                           headers={"X-CSRF-Token": "test-csrf-token"})

    def _admin_login(self, password):
        return self.client.post("/internal-knowledge-base/api/admin/login",
                                json=seal_credential(self.client, {"password": password}),
                                headers={"X-CSRF-Token": "test-csrf-token"})

    # ---- 传输加密：明文关闭、信封成功、篡改与重放拒绝 ----

    def test_plaintext_password_login_rejected(self):
        self._session()
        response = self.client.post("/internal-knowledge-base/api/login",
                                    json={"username": self.NORMAL_USER, "password": "normal-pass-2026"},
                                    headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("加密凭据", response.get_json()["error"])

    def test_envelope_login_success_and_wrong_password(self):
        self._session()
        ok = self._login(self.client, self.NORMAL_USER, "normal-pass-2026")
        self.assertEqual(ok.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session.get("internal_knowledge_base_user_id"), self.NORMAL_USER)
        bad = self._login(self.client, self.NORMAL_USER, "wrong-password")
        self.assertEqual(bad.status_code, 401)

    def test_tampered_credential_and_nonce_replay_rejected(self):
        self._session()
        tampered = self.client.post("/internal-knowledge-base/api/login",
                                    json={"credential": "!!!not-base64!!!"},
                                    headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(tampered.status_code, 400)

        # 同一 nonce 的两个信封：第一个消费成功，第二个（重放）被拒
        challenge = self.client.get("/internal-knowledge-base/api/auth-challenge").get_json()
        pub = challenge["publicKey"]
        public_key = rsa.RSAPublicNumbers(int(pub["e"], 16), int(pub["n"], 16)).public_key()
        payload = {"username": self.NORMAL_USER, "password": "normal-pass-2026", "nonce": challenge["nonce"]}
        envelopes = []
        for _ in range(2):
            ciphertext = public_key.encrypt(
                json.dumps(payload).encode("utf-8"),
                asym_padding.OAEP(mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                                  algorithm=hashes.SHA256(), label=None))
            envelopes.append({"credential": base64.b64encode(ciphertext).decode("ascii")})
        first = self.client.post("/internal-knowledge-base/api/login", json=envelopes[0],
                                 headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(first.status_code, 200)
        replay = self.client.post("/internal-knowledge-base/api/login", json=envelopes[1],
                                  headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(replay.status_code, 400)

    # ---- 强制改密：默认密码再也不能直接换取正式会话 ----

    def test_legacy_default_password_forces_change_gate(self):
        self._session()
        gate = self._login(self.client, self.LEGACY_USER, "123456")
        self.assertEqual(gate.status_code, 200)
        body = gate.get_json()
        self.assertTrue(body.get("mustChangePassword"))
        with self.client.session_transaction() as session:
            self.assertIsNone(session.get("internal_knowledge_base_user_id"))
            self.assertEqual(session.get("internal_knowledge_base_pending_user_id"), self.LEGACY_USER)
        # 默认密码拿不到业务会话
        self.assertEqual(self.client.get("/internal-knowledge-base/api/me").status_code, 401)

    def test_complete_login_password_flow_and_policy(self):
        self._session()
        self._login(self.client, self.LEGACY_USER, "123456")

        def complete(new_password, old_password="123456"):
            return self.client.post(
                "/internal-knowledge-base/api/complete-login-password",
                json=seal_credential(self.client, {"oldPassword": old_password, "newPassword": new_password}),
                headers={"X-CSRF-Token": "test-csrf-token"})

        self.assertEqual(complete("123456").status_code, 400)          # 仍用默认密码
        self.assertEqual(complete("1234567").status_code, 400)         # 7 位
        self.assertEqual(complete("12345678").status_code, 400)        # 纯数字
        self.assertEqual(complete(self.LEGACY_USER).status_code, 400)  # 与账号相同
        self.assertEqual(complete("wrong-old", old_password="whatever").status_code, 401)
        done = complete("fresh-pass-2026")
        self.assertEqual(done.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session.get("internal_knowledge_base_user_id"), self.LEGACY_USER)
            self.assertIsNone(session.get("internal_knowledge_base_pending_user_id"))
        # 旧默认密码彻底失效，新密码正常登录
        self.assertEqual(self._login(self.client, self.LEGACY_USER, "123456").status_code, 401)
        again = self._login(self.client, self.LEGACY_USER, "fresh-pass-2026")
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.get_json().get("mustChangePassword"))

    def test_admin_legacy_default_password_forced_change(self):
        store.set_admin_password_hash(generate_password_hash("123456"))
        self._session()
        gate = self._admin_login("123456")
        self.assertEqual(gate.status_code, 200)
        self.assertTrue(gate.get_json().get("mustChangePassword"))
        with self.client.session_transaction() as session:
            self.assertIsNone(session.get("internal_knowledge_base_superadmin"))
            self.assertTrue(session.get("internal_knowledge_base_pending_superadmin"))
        self.assertEqual(
            self.client.get("/internal-knowledge-base/api/admin/users").status_code, 401)
        completed = self.client.post(
            "/internal-knowledge-base/api/admin/complete-login-password",
            json=seal_credential(self.client, {"oldPassword": "123456", "newPassword": "admin-pass-2026"}),
            headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(completed.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertTrue(session.get("internal_knowledge_base_superadmin"))
        # 后台新密码可登录
        self.assertEqual(self._admin_login("admin-pass-2026").status_code, 200)

    # ---- 随机初始密码与建号/重置策略 ----

    def _admin(self):
        self._session(superadmin=True)

    def test_create_user_without_password_gets_random_initial(self):
        self._admin()
        response = self.client.post(
            "/internal-knowledge-base/api/admin/users",
            json=seal_credential(self.client, {"id": "newbie", "name": "新同事",
                                               "org": "固收中心", "role": "member"}),
            headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        initial = body.get("initialPassword") or ""
        self.assertGreaterEqual(len(initial), 8)
        self.assertNotEqual(initial, "123456")
        self.assertNotIn(initial, ("12345678", "password"))
        self.assertTrue(body.get("mustChangePassword"))
        user = store.get_user("newbie")
        self.assertIsNotNone(user)
        self.assertEqual(user["must_change_password"], 1)
        # 新用户登录即进强制改密门槛
        self._session()
        gate = self._login(self.client, "newbie", initial)
        self.assertEqual(gate.status_code, 200)
        self.assertTrue(gate.get_json().get("mustChangePassword"))

    def test_create_user_with_weak_password_rejected(self):
        self._admin()
        response = self.client.post(
            "/internal-knowledge-base/api/admin/users",
            json=seal_credential(self.client, {"id": "newbie", "name": "新同事",
                                               "org": "固收中心", "role": "member",
                                               "password": "12345678"}),
            headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(response.status_code, 400)

    def test_reset_password_sets_must_change_flag(self):
        self._admin()
        response = self.client.post(
            f"/internal-knowledge-base/api/admin/users/{self.NORMAL_USER}/reset-password",
            json=seal_credential(self.client, {"password": "reset-pass-2026"}),
            headers={"X-CSRF-Token": "test-csrf-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.get_user(self.NORMAL_USER)["must_change_password"], 1)
        self._session()
        gate = self._login(self.client, self.NORMAL_USER, "reset-pass-2026")
        self.assertTrue(gate.get_json().get("mustChangePassword"))

    # ---- PDF 活跃内容清洗 ----

    def test_sanitize_pdf_strips_active_content(self):
        import tempfile
        from internal_knowledge_base import pdf_guard
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evil.pdf"
            _make_malicious_pdf(path)
            self.assertTrue(_pdf_has_active_content(path))
            result = pdf_guard.sanitize_pdf(str(path))
            self.assertTrue(result["modified"])
            self.assertTrue(result["removed"])
            self.assertFalse(_pdf_has_active_content(path))
            import pymupdf
            doc = pymupdf.open(str(path))  # 清洗后仍是合法 PDF
            self.assertEqual(doc.page_count, 1)
            doc.close()

    def test_sanitize_pdf_leaves_clean_file_untouched(self):
        import tempfile
        from internal_knowledge_base import pdf_guard
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clean.pdf"
            _make_clean_pdf(path)
            before = path.read_bytes()
            result = pdf_guard.sanitize_pdf(str(path))
            self.assertFalse(result["modified"])
            self.assertEqual(path.read_bytes(), before)

    def _upload(self, content, filename):
        meta = {"reportType": "internal", "category": "other", "theme": "credit",
                "org": "固收中心", "reportDate": "2026-11-20", "titles": {}}
        return self.client.post(
            "/internal-knowledge-base/api/reports",
            data={"file": (io.BytesIO(content), filename), "meta": json.dumps(meta)},
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": "test-csrf-token"})

    def test_upload_sanitizes_malicious_pdf_and_sets_headers(self):
        self._session(user_id=self.NORMAL_USER)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evil.pdf"
            _make_malicious_pdf(path)
            response = self._upload(path.read_bytes(), "恶意脚本报告.pdf")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["count"], 1)
        self.assertTrue(body["reports"][0].get("sanitized"))
        report = store.reports()[0]
        stored = Path(routes.UPLOAD_DIR) / Path(report["fileUrl"]).name
        self.assertTrue(stored.exists())
        self.assertFalse(_pdf_has_active_content(stored))
        # 在线预览出口带纵深防御响应头
        inline = self.client.get(f"/internal-knowledge-base/api/reports/{report['id']}/file?inline=1")
        self.assertEqual(inline.status_code, 200)
        self.assertIn("sandbox", inline.headers.get("Content-Security-Policy", ""))
        self.assertEqual(inline.headers.get("X-Content-Type-Options"), "nosniff")

    def test_upload_rejects_unparseable_pdf(self):
        self._session(user_id=self.NORMAL_USER)
        response = self._upload(b"this is not a pdf at all", "假报告.pdf")
        self.assertEqual(response.status_code, 400)
        self.assertIn("无法安全处理", response.get_json()["error"])

    def test_admin_batch_sanitize_job(self):
        upload_dir = Path(routes.UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        evil = upload_dir / f"batch-evil-{uuid.uuid4().hex[:6]}.pdf"
        clean = upload_dir / f"batch-clean-{uuid.uuid4().hex[:6]}.pdf"
        _make_malicious_pdf(evil)
        _make_clean_pdf(clean)
        clean_before = clean.read_bytes()
        try:
            self._admin()
            started = self.client.post("/internal-knowledge-base/api/admin/pdf-sanitize",
                                       headers={"X-CSRF-Token": "test-csrf-token"})
            self.assertEqual(started.status_code, 200)
            deadline = time.time() + 60
            status = {}
            while time.time() < deadline:
                status = self.client.get("/internal-knowledge-base/api/admin/pdf-sanitize").get_json()
                if not status.get("running"):
                    break
                time.sleep(0.2)
            self.assertFalse(status.get("running"))
            # 目录里可能有其他用例遗留的 PDF，只断言本用例的两个文件被正确处理
            self.assertGreaterEqual(status.get("total"), 2)
            self.assertGreaterEqual(status.get("modified"), 1)
            self.assertFalse(_pdf_has_active_content(evil))
            self.assertEqual(clean.read_bytes(), clean_before)
        finally:
            for path in (evil, clean):
                try:
                    path.unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
