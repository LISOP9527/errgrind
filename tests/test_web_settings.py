"""Settings writes share the CLI config without leaking credentials."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from errgrind.application import ErrGrindApplication
from tests.test_web import _FakeLLM, _credentials, create_app


@unittest.skipUnless(create_app is not None, 'install the web extra')
class WebSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / 'config.json'
        self.path_patch = patch('errgrind.config.CONFIG_PATH', str(self.config_path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.seen_configs = []

        def application_factory(db, llm, prompts, cfg):
            self.seen_configs.append(dict(cfg))
            return ErrGrindApplication(db, llm, prompts, cfg)

        self.web = create_app(
            db_path=str(Path(self.temp.name) / 'web.db'),
            cfg={'provider': 'gemini', 'model': 'gemini-3.6-flash',
                 'reasoning_effort': None, 'api_key': 'existing-secret',
                 'drill_context_n': 10, 'grill_max_turns': 30},
            llm=_FakeLLM(), application_factory=application_factory,
            secret_key='settings-test',
        )
        self.web.testing = True
        self.client = self.web.test_client()

    def form(self, **changes):
        page = self.client.get('/config')
        csrf, token = _credentials(page.data, '/config')
        data = dict(csrf=csrf, submit_token=token, provider='gemini',
                    model='gemini-3.6-flash', reasoning_effort='', api_key='',
                    base_url='', drill_context_n='10', grill_max_turns='30')
        data.update(changes)
        return page, data

    def test_all_settings_save_and_read_back_without_exposing_key(self):
        page, data = self.form(model='gemini-custom', api_key='new-secret',
                               drill_context_n='17', grill_max_turns='42')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'existing-secret', page.data)
        response = self.client.post('/config', data=data)
        self.assertEqual(response.status_code, 303)
        saved = json.loads(self.config_path.read_text())
        self.assertEqual(saved['provider'], 'gemini')
        self.assertEqual(saved['model'], 'gemini-custom')
        self.assertEqual(saved['api_key'], 'new-secret')
        self.assertEqual(saved['drill_context_n'], 17)
        self.assertEqual(saved['grill_max_turns'], 42)
        self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o600)
        current = self.client.get('/config')
        self.assertIn(b'gemini-custom', current.data)
        self.assertNotIn(b'new-secret', current.data)
        self.assertEqual(self.seen_configs[-1]['model'], 'gemini-custom')
        self.assertEqual(self.seen_configs[-1]['api_key'], 'new-secret')

    def test_blank_key_preserves_same_provider_and_clear_removes_it(self):
        _, data = self.form(model='another-model')
        self.assertEqual(self.client.post('/config', data=data).status_code, 303)
        self.assertEqual(json.loads(self.config_path.read_text())['api_key'], 'existing-secret')
        _, data = self.form(clear_api_key='1')
        self.assertEqual(self.client.post('/config', data=data).status_code, 303)
        self.assertEqual(json.loads(self.config_path.read_text())['api_key'], '')

    def test_provider_switch_requires_new_key_and_resets_model(self):
        _, data = self.form(provider='deepseek')
        rejected = self.client.post('/config', data=data)
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(self.config_path.exists())
        _, data = self.form(provider='deepseek', api_key='deepseek-secret',
                            reasoning_effort='ultra')
        self.assertEqual(self.client.post('/config', data=data).status_code, 303)
        saved = json.loads(self.config_path.read_text())
        self.assertEqual(saved['model'], 'deepseek-chat')
        self.assertEqual(saved['reasoning_effort'], None)
        self.assertEqual(saved['api_key'], 'deepseek-secret')
        self.assertNotIn('base_url', saved)

    def test_codex_effort_and_opencode_address(self):
        _, data = self.form(provider='codex', model='gpt-custom',
                            reasoning_effort='high', api_key='do-not-save')
        self.assertEqual(self.client.post('/config', data=data).status_code, 303)
        saved = json.loads(self.config_path.read_text())
        self.assertEqual(saved['model'], 'gpt-custom')
        self.assertEqual(saved['reasoning_effort'], 'high')
        self.assertNotIn('api_key', saved)
        self.assertNotIn('base_url', saved)
        _, data = self.form(provider='opencode', model='custom-go',
                            api_key='go-secret', base_url='https://example.com/v1')
        self.assertEqual(self.client.post('/config', data=data).status_code, 303)
        saved = json.loads(self.config_path.read_text())
        self.assertEqual(saved['base_url'], 'https://example.com/v1')
        self.assertIsNone(saved['reasoning_effort'])

    def test_invalid_values_do_not_write_config(self):
        for changes in (
            {'drill_context_n': '0'}, {'grill_max_turns': 'abc'},
            {'provider': 'unknown'}, {'provider': 'codex', 'reasoning_effort': 'invalid'},
            {'provider': 'opencode', 'api_key': 'go-key', 'base_url': 'file:///etc/passwd'},
        ):
            with self.subTest(changes=changes):
                _, data = self.form(**changes)
                self.assertEqual(self.client.post('/config', data=data).status_code, 400)
                self.assertFalse(self.config_path.exists())

    def test_csrf_origin_replay_and_error_response_never_echo_key(self):
        _, data = self.form(api_key='submitted-secret')
        bad = self.client.post('/config', data=dict(data, csrf='wrong'))
        self.assertEqual(bad.status_code, 403)
        self.assertNotIn(b'submitted-secret', bad.data)
        cross_origin = self.client.post('/config', data=data,
                                        headers={'Origin': 'https://evil.invalid'})
        self.assertEqual(cross_origin.status_code, 403)
        self.assertNotIn(b'submitted-secret', cross_origin.data)
        opaque_origin = self.client.post('/config', data=data,
                                         headers={'Origin': 'null', 'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(opaque_origin.status_code, 403)
        self.assertEqual(self.client.post('/config', data=data,
                                          headers={'Origin': 'null', 'Sec-Fetch-Site': 'same-origin'}).status_code, 303)
        self.assertEqual(self.client.post('/config', data=data).status_code, 409)
        self.assertNotIn(b'submitted-secret', self.client.get('/config').data)

    def test_failed_save_keeps_running_configuration(self):
        _, data = self.form(model='new-model', api_key='new-secret')
        with patch('errgrind.web.app.save_config', side_effect=OSError('private-path')):
            response = self.client.post('/config', data=data)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'new-secret', response.data)
        self.assertFalse(self.config_path.exists())
        current = self.client.get('/config')
        self.assertIn(b'gemini-3.6-flash', current.data)
        self.assertNotIn(b'new-model', current.data)


if __name__ == '__main__':
    unittest.main()
