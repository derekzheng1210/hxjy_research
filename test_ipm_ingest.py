# -*- coding: utf-8 -*-
"""ipm_tracker.ingest 远程推送接口测试：令牌校验、增量合并、全量替换、结构校验。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from ipm_tracker import ingest
from ipm_tracker import routes as ipm_routes

TOKEN = "unit-test-token"


class IpIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cache_file = root / "cache" / "indicators_data.json"
        self.cache_file.parent.mkdir()
        self.data_dir = root / "data"

        app = Flask(__name__)
        app.register_blueprint(ingest.bp)
        self.client = app.test_client()

        for patcher in (
            patch.object(ipm_routes, "CACHE_FILE", str(self.cache_file)),
            patch.object(ipm_routes, "CACHE_DIR", str(self.cache_file.parent)),
            patch.object(ipm_routes, "DATA_DIR", str(self.data_dir)),
            patch.dict(os.environ, {"IPM_INGEST_TOKEN": TOKEN}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    # ── 工具 ──────────────────────────────────────────────
    def write_cache(self, indicators):
        self.cache_file.write_text(
            json.dumps({'updated': '2026-08-30 10:00:00', 'indicators': indicators},
                       ensure_ascii=False),
            encoding='utf-8')
        # 模拟运行中的服务器：缓存已在启动时载入内存，推送合并发生在内存态
        ipm_routes.load_cache_file()

    def read_cache(self):
        return json.loads(self.cache_file.read_text(encoding='utf-8'))

    def post(self, payload, token=TOKEN):
        return self.client.post('/api/ingest/ipm', json=payload,
                                headers={'X-Ingest-Token': token})

    # ── 鉴权 ──────────────────────────────────────────────
    def test_disabled_without_server_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IPM_INGEST_TOKEN", None)
            self.assertEqual(self.post({'indicators': {}}).status_code, 503)
            self.assertEqual(
                self.client.get('/api/ingest/ipm/preflight',
                                headers={'X-Ingest-Token': TOKEN}).status_code, 503)

    def test_rejects_wrong_token(self):
        self.assertEqual(self.post({'indicators': {'S1': {'times': [], 'values': []}}},
                                   token='wrong').status_code, 401)
        self.assertEqual(
            self.client.get('/api/ingest/ipm/preflight',
                            headers={'X-Ingest-Token': 'wrong'}).status_code, 401)

    # ── preflight ─────────────────────────────────────────
    def test_preflight_reports_latest_date(self):
        self.write_cache({'S1': {'times': ['2026-08-28', '2026-08-31'], 'values': [1.0, 2.0]}})
        resp = self.client.get('/api/ingest/ipm/preflight',
                               headers={'X-Ingest-Token': TOKEN})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['data_latest_date'], '2026-08-31')
        self.assertEqual(body['indicators'], 1)

    # ── 增量推送 ──────────────────────────────────────────
    def test_push_increment_merges_and_archives(self):
        self.write_cache({'S1': {'times': ['2026-08-28'], 'values': [1.0]}})
        payload = {
            'date': '20260831',
            'start': '2026-08-29',
            'end': '2026-08-31',
            'indicators': {
                'S1': {'times': ['2026-08-31'], 'values': [2.0]},
                'S2': {'times': ['2026-08-31'], 'values': [5.0]},
            },
        }
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['mode'], 'increment')
        self.assertEqual(body['increment_date'], '20260831')
        self.assertEqual(body['data_latest_date'], '2026-08-31')

        merged = self.read_cache()['indicators']
        self.assertEqual(merged['S1']['times'], ['2026-08-28', '2026-08-31'])
        self.assertEqual(merged['S1']['values'], [1.0, 2.0])
        self.assertEqual(merged['S2']['values'], [5.0])

        archive = self.data_dir / '20260831.json'
        self.assertTrue(archive.is_file())
        self.assertEqual(json.loads(archive.read_text(encoding='utf-8'))['date'], '20260831')

    def test_push_increment_null_does_not_overwrite_history(self):
        self.write_cache({'S1': {'times': ['2026-08-28'], 'values': [1.0]}})
        payload = {
            'date': '20260831',
            'start': '2026-08-29',
            'end': '2026-08-31',
            'indicators': {'S1': {'times': ['2026-08-28', '2026-08-31'],
                                  'values': [None, 3.0]}},
        }
        self.assertEqual(self.post(payload).status_code, 200)
        merged = self.read_cache()['indicators']['S1']
        self.assertEqual(merged['values'], [1.0, 3.0])

    # ── 全量推送 ──────────────────────────────────────────
    def test_push_full_replaces_cache(self):
        self.write_cache({'OLD': {'times': ['2026-01-01'], 'values': [9.0]}})
        payload = {'indicators': {'NEW': {'times': ['2026-08-31'], 'values': [1.5]}}}
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['mode'], 'full')
        merged = self.read_cache()['indicators']
        self.assertNotIn('OLD', merged)
        self.assertEqual(merged['NEW']['values'], [1.5])

    # ── 结构校验 ──────────────────────────────────────────
    def test_rejects_invalid_payload(self):
        self.assertEqual(self.post({'foo': 1}).status_code, 400)
        self.assertEqual(
            self.post({'indicators': {'S1': {'times': 'bad', 'values': []}}}).status_code, 400)
        self.assertEqual(self.post({}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
