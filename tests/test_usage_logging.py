"""Telemetry must count reported usage without changing learning workflows."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

import httpx

from errgrind.application import ErrGrindApplication
from errgrind.db.ops import Database
from errgrind.llm.codex import CodexClient, CodexError
from errgrind.llm.codex_transport import CodexResponsesTransport
from errgrind.llm.prompts import PromptManager
from errgrind.llm.client import LLMClient, LLMError
from errgrind.llm.gemini import GeminiClient, GeminiError
from errgrind.llm import usage
from tests.test_grill_diagnosis import _first_decision


class UsageLoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state' / 'usage.jsonl'
        self.patcher = patch.object(usage, 'log_path', return_value=self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_normalize_provider_fields_and_reject_untrusted_values(self):
        codex = usage.normalize_usage({
            'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 80},
            'output_tokens': 20, 'output_tokens_details': {'reasoning_tokens': 15},
            'total_tokens': 120, 'secret': 'do-not-log',
        }, 'codex')
        self.assertEqual(list(codex.values()), [100, 80, 20, 15, 120])
        deepseek = usage.normalize_usage({'prompt_tokens': 30, 'prompt_cache_hit_tokens': 12}, 'openai')
        self.assertEqual(deepseek['cached_input_tokens'], 12)
        gemini = usage.normalize_usage({
            'promptTokenCount': 10, 'candidatesTokenCount': 3, 'thoughtsTokenCount': 7,
            'totalTokenCount': 20,
        }, 'gemini')
        self.assertEqual(gemini['output_tokens'], 3)
        self.assertEqual(gemini['reasoning_tokens'], 7)
        bad = usage.normalize_usage({'input_tokens': True, 'output_tokens': -1, 'total_tokens': '900'}, 'codex')
        self.assertTrue(all(value is None for value in bad.values()))

    def test_nested_scopes_restore_and_snapshots_are_not_added(self):
        with usage.usage_scope(action='drill', stage='spec'):
            with usage.usage_scope(repair_attempt=2, json_attempt=3):
                with usage.model_attempt('codex', 'model') as attempt:
                    attempt.record_usage({'input_tokens': 10, 'output_tokens': 3}, 'codex')
                    attempt.record_usage({'input_tokens': 10, 'output_tokens': 5}, 'codex')
            with usage.model_attempt('codex', 'model'):
                pass
        with usage.model_attempt('codex', 'model'):
            pass
        a, b, c = self.rows()
        self.assertEqual(a['output_tokens'], 5)
        self.assertEqual(a['operation_id'], b['operation_id'])
        self.assertEqual([a['attempt_index'], b['attempt_index']], [1, 2])
        self.assertEqual((a['repair_attempt'], a['json_attempt']), (2, 3))
        self.assertEqual((b['repair_attempt'], b['json_attempt']), (1, 1))
        self.assertEqual(c['action'], 'unscoped')
        self.assertNotEqual(b['operation_id'], c['operation_id'])
        self.assertIsNone(c['input_tokens'])
        self.assertFalse(c['usage_reported'])

    def test_exception_and_interrupt_privacy(self):
        for error in (ValueError('SECRET-answer-and-key'), KeyboardInterrupt('SECRET')):
            with self.assertRaises(type(error)):
                with usage.model_attempt('codex', 'model') as attempt:
                    attempt.set_http_status(429)
                    raise error
        rows = self.rows()
        self.assertEqual([r['status'] for r in rows], ['error', 'interrupted'])
        self.assertEqual(rows[0]['http_status'], 429)
        self.assertNotIn('SECRET', self.path.read_text())

    def test_disk_failure_does_not_mask_success_or_interrupt(self):
        with patch.object(usage, 'log_path', side_effect=OSError('private-path')), patch.object(usage, '_WARNED', False):
            with self.assertWarnsRegex(RuntimeWarning, '无法写入模型用量日志'):
                with usage.model_attempt('codex', 'model'):
                    pass
            with self.assertRaises(KeyboardInterrupt):
                with usage.model_attempt('codex', 'model'):
                    raise KeyboardInterrupt

    def test_rotation_permissions_and_summary_unknowns(self):
        with patch.object(usage, 'MAX_LOG_BYTES', 1):
            with usage.usage_scope(action='teach', stage='reply'):
                with usage.model_attempt('codex', 'model') as attempt:
                    attempt.record_usage({'input_tokens': 9, 'output_tokens': 0, 'total_tokens': 9}, 'codex')
                with usage.model_attempt('codex', 'model'):
                    pass
        self.assertTrue(Path(str(self.path) + '.1').exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        with self.path.open('a') as stream:
            stream.write('{broken\n')
        report = usage.summarize(self.path)
        self.assertEqual(report['malformed_lines'], 1)
        group = report['groups'][0]
        self.assertEqual(group['attempts'], 2)
        self.assertEqual(group['missing_usage_attempts'], 1)
        self.assertEqual(group['reported_tokens']['input_tokens'], 9)
        self.assertEqual(group['reported_tokens']['output_tokens'], 0)
        self.assertIsNone(group['reported_tokens']['reasoning_tokens'])
        self.assertEqual(group['reporting_attempts']['input_tokens'], 1)

    def client(self, handler, max_retries=3):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = Path(self.temp.name) / 'auth.json'
        auth.write_text(json.dumps({'tokens': {'access_token': 'CREDENTIAL_SECRET', 'account_id': 'ACCOUNT_SECRET'}}))
        client = CodexClient(model='test-model', auth_file=auth, transport=CodexResponsesTransport(http), max_retries=max_retries)
        self.addCleanup(client.close)
        return client

    @staticmethod
    def response(text, tokens=100):
        event = {'type': 'response.completed', 'response': {
            'status': 'completed',
            'usage': {'input_tokens': tokens, 'output_tokens': 20, 'total_tokens': tokens + 20,
                      'input_tokens_details': {'cached_tokens': 50},
                      'output_tokens_details': {'reasoning_tokens': 5}},
            'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}],
        }}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\n')

    def test_actual_grill_repair_usage_is_correlated_and_resume_is_free(self):
        replies = iter(['{}', json.dumps(_first_decision(), ensure_ascii=False)])
        client = self.client(lambda request: self.response(next(replies)))
        db = Database(str(Path(self.temp.name) / 'test.db'))
        self.addCleanup(db.close)
        error_id = db.create_error('QUESTION_SECRET', '我直接套了公式')
        app = ErrGrindApplication(db, client, PromptManager())
        first = app.start_or_resume_grill(error_id)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['repair_attempt'] for r in rows], [1, 2])
        self.assertEqual(rows[0]['operation_id'], rows[1]['operation_id'])
        self.assertEqual(rows[0]['action'], 'grill')
        self.assertEqual(rows[0]['error_id'], error_id)
        self.assertEqual(rows[1]['cached_input_tokens'], 50)
        self.assertEqual(rows[1]['reasoning_tokens'], 5)
        app.pause_grill(error_id)
        resumed = app.start_or_resume_grill(error_id)
        self.assertIsNone(resumed.assistant_response)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(first.assistant_response, '请说明你当时的判断')
        for forbidden in ('QUESTION_SECRET', 'CREDENTIAL_SECRET', 'ACCOUNT_SECRET', '我直接套了公式', '候选机制'):
            self.assertNotIn(forbidden, self.path.read_text())

    def test_codex_http_retry_and_json_retry_remain_distinct(self):
        replies = iter([httpx.Response(429, text='SECRET'), self.response('not json'), self.response('{}')])
        client = self.client(lambda request: next(replies))
        with usage.usage_scope(action='drill', stage='spec'), patch('errgrind.llm.codex.time.sleep'):
            self.assertEqual(client.chat_json([]), {})
        rows = self.rows()
        self.assertEqual([r['status'] for r in rows], ['error', 'success', 'success'])
        self.assertEqual([r['json_attempt'] for r in rows], [1, 1, 2])
        self.assertEqual([r['attempt_index'] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]['http_status'], 429)
        self.assertIsNone(rows[0]['input_tokens'])
        self.assertEqual(sum(r['input_tokens'] or 0 for r in rows), 200)

    def test_codex_partial_stream_never_replays_and_usage_is_unknown(self):
        payload = 'data: ' + json.dumps({'type': 'response.output_text.delta', 'delta': 'SECRET'}) + '\n\n'
        client = self.client(lambda request: httpx.Response(200, text=payload))
        seen = []
        with self.assertRaises(CodexError):
            client.stream_chat([], seen.append)
        self.assertEqual(seen, ['SECRET'])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['status'], 'error')
        self.assertFalse(self.rows()[0]['usage_reported'])

    def test_teach_and_field_ocr_scopes_do_not_leak_across_actions(self):
        replies = iter(['可见讲解', '{"text":"已识别文字"}'])
        client = self.client(lambda request: self.response(next(replies)))
        db = Database(str(Path(self.temp.name) / 'test.db'))
        self.addCleanup(db.close)
        error_id = db.create_error('题目', '思路')
        db.update_grilling(error_id, json.dumps([{'role': 'assistant', 'content': '总结'}]), '总结')
        app = ErrGrindApplication(db, client, PromptManager())
        app.start_or_resume_teach(error_id)
        app.finish_teach(error_id)
        with patch('errgrind.llm.codex.load_image') as image:
            image.return_value.data_url = 'data:image/png;base64,IMAGE_SECRET'
            self.assertEqual(app.transcribe_record_field('PATH_SECRET', 'question'), '已识别文字')
        rows = self.rows()
        self.assertEqual([(r['action'], r['stage']) for r in rows], [('teach', 'reply'), ('record', 'ocr_question')])
        self.assertIsNone(rows[1]['error_id'])
        self.assertNotEqual(rows[0]['operation_id'], rows[1]['operation_id'])
        self.assertNotIn('SECRET', self.path.read_text())

    def test_drill_stages_and_repairs_are_separate_from_judgment(self):
        spec = {
            "source_error_number": 1,
            "target_pattern": {"mechanism": "检查条件", "trigger": "熟悉形式", "failure_behavior": "直接套用", "desired_behavior": "核对条件", "success_signal": "列出条件"},
            "new_problem": {"domain": "概率", "task_type": "calculate", "setting": "受限样本", "task_goal": "计算概率", "essential_trigger": "条件未自动成立", "solution_strategy": "先确定样本空间", "avoid": ["复杂计算"]},
            "difficulty": {"level": 3, "reasoning_depth": 3, "calculation_load": 2},
        }
        replies = iter([{}, spec, {}, {'question': '新题', 'reference_answer': '新答'}, {'is_correct': True, 'feedback': '已检查条件'}])
        client = self.client(lambda request: self.response(json.dumps(next(replies))))
        db = Database(str(Path(self.temp.name) / 'test.db'))
        self.addCleanup(db.close)
        error_id = db.create_error('原题')
        db.update_grilling(error_id, '[]', '摘要')
        app = ErrGrindApplication(db, client, PromptManager())
        prep = app.prepare_drill()
        result = app.judge_and_record_drill(prep, '回答')
        rows = self.rows()
        self.assertTrue(result.is_correct)
        self.assertEqual([r['stage'] for r in rows], ['spec', 'spec', 'draft', 'draft', 'judge'])
        self.assertEqual([r['repair_attempt'] for r in rows], [1, 2, 1, 2, 1])
        self.assertEqual(len({r['operation_id'] for r in rows[:4]}), 1)
        self.assertNotEqual(rows[0]['operation_id'], rows[4]['operation_id'])
        self.assertEqual(rows[4]['error_id'], error_id)
        self.assertEqual(db.drill_stats()['total'], 1)

    def test_gemini_retry_records_failure_before_backoff(self):
        client = GeminiClient(api_key='secret', max_retries=2)
        request = httpx.Request('POST', 'https://example.invalid')
        responses = [httpx.Response(429, request=request), httpx.Response(200, request=request, json={
            'candidates': [{'content': {'parts': [{'text': '{}'}]}}],
            'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 2, 'thoughtsTokenCount': 3, 'totalTokenCount': 15},
        })]
        def backoff(_):
            self.assertEqual(self.rows()[0]['status'], 'error')
        with patch('errgrind.llm.gemini.httpx.post', side_effect=responses), patch('errgrind.llm.gemini.time.sleep', side_effect=backoff):
            self.assertEqual(client.chat_json([]), {})
        a, b = self.rows()
        self.assertEqual([a['status'], b['status']], ['error', 'success'])
        self.assertEqual(a['http_status'], 429)
        self.assertEqual(b['total_tokens'], 15)
        self.assertEqual(b['output_tokens'], 2)
        self.assertEqual(b['reasoning_tokens'], 3)

    def test_gemini_stream_terminal_snapshot_and_missing_usage(self):
        client = GeminiClient(api_key='secret')
        for metadata in ({'totalTokenCount': 15}, None):
            events = [{'candidates': [{'content': {'parts': [{'text': '答'}]}}]}]
            if metadata:
                events.extend([{'usageMetadata': metadata}, {'usageMetadata': metadata}])
            lines = []
            for event in events:
                lines.extend(['data: ' + json.dumps(event), ''])
            response = Mock(status_code=200)
            response.iter_lines.return_value = iter(lines)
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            with patch('errgrind.llm.gemini.httpx.stream', return_value=context):
                self.assertEqual(client.stream_chat([], lambda text: None), '答')
        a, b = self.rows()
        self.assertEqual(a['total_tokens'], 15)
        self.assertIsNone(b['total_tokens'])
        self.assertFalse(b['usage_reported'])

    def test_gemini_ocr_failure_is_not_success(self):
        client = GeminiClient(api_key='secret', max_retries=1)
        with patch('errgrind.llm.gemini.load_image') as image, patch('errgrind.llm.gemini.httpx.post', side_effect=httpx.ConnectError('SECRET')):
            image.return_value.mime_type = 'image/png'
            image.return_value.base64_data = 'SECRET'
            with self.assertRaises(GeminiError):
                client.transcribe_image('SECRET', 'SECRET')
        self.assertEqual(self.rows()[0]['status'], 'error')
        self.assertNotIn('SECRET', self.path.read_text())

    def openai_client(self, create):
        client = object.__new__(LLMClient)
        client.model, client.provider, client.max_retries = 'model', 'opencode', 2
        client.client = SimpleNamespace(max_retries=2, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return client

    def test_openai_retry_json_and_sdk_ambiguity(self):
        error = RuntimeError('SECRET')
        error.status_code = 429
        def response(text):
            return SimpleNamespace(usage={'prompt_tokens': 20, 'completion_tokens': 2}, choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        client = self.openai_client(Mock(side_effect=[error, response('bad JSON'), response('{}')]))
        with usage.usage_scope(action='drill', stage='spec'), patch('errgrind.llm.client.time.sleep'):
            self.assertEqual(client.chat_json([]), {})
        rows = self.rows()
        self.assertEqual([r['status'] for r in rows], ['error', 'success', 'success'])
        self.assertEqual([r['json_attempt'] for r in rows], [1, 1, 2])
        self.assertEqual(rows[0]['http_status'], 429)
        self.assertEqual(rows[0]['sdk_max_retries'], 2)
        self.assertNotIn('SECRET', self.path.read_text())

    def test_openai_partial_stream_never_replays(self):
        def stream():
            yield SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content='答'))])
            raise RuntimeError('SECRET')
        create = Mock(return_value=stream())
        client = self.openai_client(create)
        with self.assertRaises(LLMError):
            client.stream_chat([], lambda text: None)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(self.rows()[0]['status'], 'error')
        self.assertFalse(self.rows()[0]['usage_reported'])


if __name__ == '__main__':
    unittest.main()
