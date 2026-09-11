"""Recovery and untrusted-content checks for the optional local adapter."""
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_web import create_app, _FakeLLM, _credentials, _form
from errgrind.db.ops import Database
from errgrind.application import ErrGrindApplication


@unittest.skipUnless(create_app is not None, 'install the web extra')
class WebRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'test.db')
        self.db = Database(self.path)
        self.llm = _FakeLLM()
        self.app = create_app(db_path=self.path, cfg={}, llm=self.llm, secret_key='test')
        self.client = self.app.test_client()
        self.error_id = self.db.create_error('求 $x$。', '我直接套了公式', 'REFERENCE_ONLY')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def post(self, kind, action, **values):
        path = f'/errors/{self.error_id}/{kind}/{action}'
        page = self.client.get(f'/errors/{self.error_id}')
        csrf, token = _credentials(page.data, path)
        return self.client.post(path, data=dict(csrf=csrf, submit_token=token, **values),
                                headers={'Accept': 'application/json'})

    def complete_grill(self):
        self.assertEqual(self.post('grill', 'start').status_code, 200)
        self.assertEqual(self.post('grill', 'answer', answer='当时没有核对条件').status_code, 200)

    def test_reads_do_not_start_models_and_system_messages_never_render(self):
        self.db.save_grilling_conversation(self.error_id, json.dumps([
            {'role': 'system', 'content': 'SYSTEM_SECRET_PRIVATE'},
            {'role': 'assistant', 'content': '公开问题'},
        ]))
        for _ in range(2):
            page = self.client.get(f'/errors/{self.error_id}')
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(b'SYSTEM_SECRET_PRIVATE', page.data)
        self.assertEqual(self.llm.chat_json_calls, 0)
        self.assertEqual(self.llm.chat_calls, 0)

    def test_grill_answer_survives_failure_then_explicit_resume(self):
        self.assertEqual(self.post('grill', 'start').status_code, 200)
        with patch.object(self.llm, 'chat_json', side_effect=RuntimeError('SECRET_PROVIDER_BODY 429')):
            response = self.post('grill', 'answer', answer='当时没有核对条件')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'SECRET_PROVIDER_BODY', response.data)
        saved = self.db.get_error(self.error_id)
        self.assertEqual(saved.status, 'pending-grill')
        history = json.loads(saved.grilling_conversation)
        self.assertEqual(history[-1], {'role': 'user', 'content': '当时没有核对条件'})
        self.client.get(f'/errors/{self.error_id}')
        self.assertEqual(self.llm.chat_json_calls, 1)
        self.assertEqual(self.post('grill', 'start').status_code, 200)
        self.assertEqual(self.db.get_error(self.error_id).status, 'pending-teach')
        self.assertEqual(sum(m['content'] == '当时没有核对条件' for m in json.loads(self.db.get_error(self.error_id).grilling_conversation)), 1)

    def test_teach_answer_survives_failure_then_finish_and_resume(self):
        self.complete_grill()
        self.assertEqual(self.post('teach', 'start').status_code, 200)
        with patch.object(self.llm, 'chat', side_effect=RuntimeError('OAUTH_SECRET')):
            response = self.post('teach', 'answer', answer='我还不理解这个条件')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'OAUTH_SECRET', response.data)
        history = json.loads(self.db.get_error(self.error_id).teach_conversation)
        self.assertEqual(history[-1]['content'], '我还不理解这个条件')
        self.assertEqual(self.post('teach', 'start').status_code, 200)
        self.assertEqual(self.post('teach', 'finish').status_code, 200)
        self.assertEqual(self.db.get_error(self.error_id).status, 'done')
        calls = self.llm.chat_calls
        self.assertEqual(self.post('teach', 'start').status_code, 200)
        self.assertEqual(self.llm.chat_calls, calls)

    def test_ocr_failure_removes_file_and_does_not_write_record(self):
        page = self.client.get('/record')
        form = _form(page.data)
        csrf = form['values']['csrf']
        path = '/record/ocr/user_thoughts'
        files = []
        def failing(filename, prompt):
            files.append(filename)
            self.assertTrue(Path(filename).is_file())
            raise RuntimeError('OCR_SECRET')
        with patch.object(self.llm, 'transcribe_image', side_effect=failing):
            response = self.client.post(path, data={
                'csrf': csrf, 'submit_token': form['ocr'][path],
                'image': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage'), '../../escape.png'),
            }, headers={'Accept': 'application/json'})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'OCR_SECRET', response.data)
        self.assertEqual(len(files), 1)
        self.assertFalse(Path(files[0]).exists())
        self.assertEqual(len(self.db.list_all_errors()), 1)

    def test_csrf_and_cross_origin_rejected_without_mutation(self):
        page = self.client.get('/record')
        csrf, token = _credentials(page.data, '/record')
        data = dict(csrf=csrf, submit_token=token, question='Q', user_thoughts='T')
        response = self.client.post('/record', data=data, headers={'Origin': 'https://attacker.example', 'Accept': 'application/json'})
        self.assertEqual(response.status_code, 403)
        data['csrf'] = 'wrong'
        self.assertEqual(self.client.post('/record', data=data).status_code, 403)
        self.assertEqual(len(self.db.list_all_errors()), 1)

    def test_math_html_and_code_render_without_executable_model_markup(self):
        from errgrind.web.rendering import render_markdown
        for tex in (r'$x^2$', r'$$x^2$$', r'\(x^2\)', r'\[x^2\]'):
            self.assertIn('data-tex="x^2"', render_markdown(tex))
        for payload in ('<script>alert(1)</script>', '$<img src=x onerror=alert(1)>$', r'\[<svg onload=alert(1)>\]'):
            rendered = render_markdown(payload)
            self.assertNotIn('<script>', rendered)
            self.assertNotIn('<img ', rendered)
            self.assertNotIn('<svg ', rendered)
        self.assertNotIn('data-tex', render_markdown('`$x$`\n\n```tex\n$$y$$\n```'))
        self.assertNotIn('href="javascript:', render_markdown('[click](javascript:alert(1))'))

    def test_prepare_reports_real_spec_and_draft_stages(self):
        self.db.update_grilling(self.error_id, "[]", "诊断总结")
        page = self.client.get('/drill', follow_redirects=True)
        form = _form(page.data)
        action = form['action']
        key = action.split('/')[3]
        entered_spec, entered_draft = threading.Event(), threading.Event()
        release_spec, release_draft = threading.Event(), threading.Event()
        original = self.llm.chat_json
        results = []
        def blocked(*args, **kwargs):
            required = kwargs['output_schema']['required']
            if 'source_error_number' in required:
                entered_spec.set()
                if not release_spec.wait(5): raise RuntimeError('test timeout')
            elif set(required) == {'question', 'reference_answer'}:
                entered_draft.set()
                if not release_draft.wait(5): raise RuntimeError('test timeout')
            return original(*args, **kwargs)
        client = self.app.test_client()
        client.set_cookie('session', self.client.get_cookie('session').value)
        def request_prepare():
            results.append(client.post(action, data=form['values'], headers={'Accept': 'application/json'}))
        with patch.object(self.llm, 'chat_json', side_effect=blocked):
            thread = threading.Thread(target=request_prepare)
            thread.start()
            try:
                self.assertTrue(entered_spec.wait(3))
                status = self.client.get(f'/api/drill/{key}').get_json()
                self.assertEqual(status, {'state': 'preparing'})
                release_spec.set()
                self.assertTrue(entered_draft.wait(3))
                status = self.client.get(f'/api/drill/{key}').get_json()
                self.assertEqual(status, {'state': 'preparing'})
            finally:
                release_spec.set(); release_draft.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].status_code, 200)
        self.assertNotIn(b'REFERENCE_ONLY', results[0].data)

    def test_missing_config_still_allows_text_recording(self):
        app = create_app(db_path=self.path, cfg={}, secret_key='no-model')
        client = app.test_client()
        page = client.get('/record')
        self.assertIn('尚未配置可用模型'.encode(), page.data)
        form = _form(page.data)
        response = client.post('/record', data=dict(form['values'], question='题目', user_thoughts='没有思路'))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.db.list_all_errors()), 2)
