import io
import tempfile
import unittest
import json
from types import SimpleNamespace

try:
    from errgrind.application import GrillState, RecordDraft
    from errgrind.web.app import create_app
    from errgrind.application import ErrGrindApplication
    from errgrind.db.ops import Database
    from errgrind.llm.prompts import PromptManager
    from errgrind.llm.messages import MultimodalMessage
    from tests.test_web import _FakeLLM
except ImportError:
    create_app = None


class FakeAssistantApplication:
    def __init__(self):
        self.calls = []

    def prepare_record_draft(self, raw_input, image_paths, *, current_draft, pending_key=None):
        self.calls.append(('draft', raw_input, list(image_paths), current_draft, pending_key))
        return RecordDraft('', '', '', 'record')

    def record_error(self, question, user_thoughts, reference_answer, *, origin, image_paths, pending_key=None):
        self.calls.append(('finalize', question, user_thoughts, reference_answer,
                           origin, list(image_paths), pending_key))
        return SimpleNamespace(
            id=7, status='pending-grill', origin='record', question=question,
            user_thoughts=user_thoughts, reference_answer=reference_answer,
        )

    def append_pending_image_attachments(self, pending_key, image_paths):
        self.calls.append(('pending_append', pending_key, list(image_paths)))
        return 1

    def get_pending_attachment_count(self, pending_key):
        self.calls.append(('pending_count', pending_key))
        return 1 if any(call[0] == 'pending_append' for call in self.calls) else 0

    def delete_pending_attachments(self, pending_key):
        self.calls.append(('pending_delete', pending_key))

    def get_error_attachment_count(self, error_id):
        self.calls.append(('attachment_count', error_id))
        return 1

    def start_or_resume_grill(self, error_id):
        self.calls.append(('start', error_id))
        return self._grill_result(error_id, '请说明你当时的判断。')

    def submit_grill_answer(self, error_id, answer, *, image_paths):
        self.calls.append(('answer', error_id, answer, list(image_paths)))
        return self._grill_result(error_id, '已记录这次回答。')

    @staticmethod
    def _grill_result(error_id, response):
        return SimpleNamespace(
            error=SimpleNamespace(
                id=error_id, status='pending-grill', origin='record',
                question='求 x', user_thoughts='我直接套公式', reference_answer='x=2',
            ),
            messages=[
                {'role': 'user', 'content': '开始吧', 'attachments': [11]},
                {'role': 'assistant', 'content': response},
            ],
            assistant_response=response,
            state=GrillState.ACTIVE,
            summary=None,
        )


@unittest.skipUnless(create_app is not None, 'install the web extra')
class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.application = FakeAssistantApplication()
        self.web = create_app(
            application=self.application,
            secret_key='assistant-api-test',
        )
        self.web.testing = True
        self.client = self.web.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def bootstrap(self):
        response = self.client.get('/assistant-ui/bootstrap')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        return payload['csrf'], payload['tokens']

    def test_bootstrap_tokens_are_path_scoped_and_csrf_is_required(self):
        csrf, tokens = self.bootstrap()
        self.assertEqual(set(tokens), {
            'record_draft', 'record_finalize', 'record_reset', 'drill_new',
            'grill_start', 'grill_answer',
        })
        rejected = self.client.post(
            '/api/assistant/grill/start',
            json={'error_id': 7},
            headers={'X-Submission-Token': tokens['grill_start']},
        )
        self.assertEqual(rejected.status_code, 403)
        accepted = self.client.post(
            '/api/assistant/grill/start',
            json={'error_id': 7},
            headers={
                'X-CSRFToken': csrf,
                'X-Submission-Token': tokens['grill_start'],
            },
        )
        self.assertEqual(accepted.status_code, 200)

    def test_incomplete_draft_is_normal_json_response(self):
        csrf, tokens = self.bootstrap()
        response = self.client.post(
            '/api/assistant/record/draft',
            data={'csrf': csrf, 'submit_token': tokens['record_draft'], 'raw_input': '还没整理'},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['ready'])
        self.assertEqual(payload['status'], 'incomplete')
        self.assertEqual(payload['missing_fields'], ['question'])
        self.assertTrue(payload['submit_token'])

    def test_image_only_record_is_not_treated_as_empty(self):
        csrf, tokens = self.bootstrap()
        response = self.client.post(
            '/api/assistant/record/draft',
            data={
                'csrf': csrf,
                'submit_token': tokens['record_draft'],
                'raw_input': '',
                'question': '', 'user_thoughts': '', 'reference_answer': '',
                'images': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage-only'), 'question.png'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ready'])
        self.assertEqual(response.get_json()['pending_attachment_count'], 1)
        draft_calls = [call for call in self.application.calls if call[0] == 'draft']
        self.assertEqual(len(draft_calls), 1)
        self.assertEqual(len(draft_calls[0][2]), 1)
        self.assertTrue(draft_calls[0][4])
        self.assertTrue(any(call[0] == 'pending_append' for call in self.application.calls))

    def test_image_only_grill_answer_is_not_treated_as_empty(self):
        csrf, tokens = self.bootstrap()
        started = self.client.post(
            '/api/assistant/grill/start', json={'error_id': 12},
            headers={'X-CSRFToken': csrf, 'X-Submission-Token': tokens['grill_start']},
        )
        response = self.client.post(
            '/api/assistant/grill/answer',
            data={
                'error_id': '12', 'answer': '',
                'images': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage-only'), 'answer.png'),
            },
            content_type='multipart/form-data',
            headers={
                'X-CSRFToken': csrf,
                'X-Submission-Token': started.get_json()['submit_token'],
            },
        )
        self.assertEqual(response.status_code, 200)
        answer_calls = [call for call in self.application.calls if call[0] == 'answer']
        self.assertEqual(len(answer_calls), 1)
        self.assertEqual(answer_calls[0][2], '')
        self.assertEqual(len(answer_calls[0][3]), 1)

    def test_finalize_uses_application_and_preserves_original_attachment_contract(self):
        csrf, tokens = self.bootstrap()
        response = self.client.post(
            '/api/assistant/record/finalize',
            data={
                'csrf': csrf,
                'submit_token': tokens['record_finalize'],
                'question': '求 x',
                'user_thoughts': '',
                'reference_answer': '',
                'images': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage'), 'question.png'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['error']['id'], 7)
        self.assertEqual(payload['initial_attachment_count'], 1)
        self.assertEqual(payload['content_contract']['version'], 1)
        self.assertTrue(payload['submit_token'])
        self.assertEqual(
            payload['content_contract']['persistence'],
            'legacy-text-plus-initial-original-attachments',
        )
        finalize_call = next(call for call in self.application.calls if call[0] == 'finalize')
        self.assertEqual(finalize_call[4], 'record')
        started = self.client.post(
            '/api/assistant/grill/start',
            json={'error_id': payload['error']['id']},
            headers={
                'X-CSRFToken': csrf,
                'X-Submission-Token': payload['submit_token'],
            },
        )
        self.assertEqual(started.status_code, 200)

    def test_grill_start_and_answer_use_same_error_and_public_messages(self):
        csrf, tokens = self.bootstrap()
        started = self.client.post(
            '/api/assistant/grill/start',
            json={'error_id': 12},
            headers={'X-CSRFToken': csrf, 'X-Submission-Token': tokens['grill_start']},
        )
        self.assertEqual(started.status_code, 200)
        start_payload = started.get_json()
        answered = self.client.post(
            '/api/assistant/grill/answer',
            data={
                'csrf': csrf,
                'submit_token': start_payload['submit_token'],
                'error_id': '12',
                'answer': '我当时直接套用了公式',
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(answered.status_code, 200)
        payload = answered.get_json()
        self.assertEqual(payload['error']['id'], 12)
        self.assertEqual(payload['messages'][0]['attachment_ids'], [11])
        self.assertEqual(payload['messages'][0]['attachment_count'], 1)
        self.assertNotIn('grilling_diagnostic_state', payload['error'])
        self.assertEqual(
            [call[0:2] for call in self.application.calls if call[0] in {'start', 'answer'}],
            [('start', 12), ('answer', 12)],
        )

    def test_real_application_keeps_record_image_through_finalize_and_grill(self):
        db = Database(str(self.temp.name + '/real.db'))
        llm = _FakeLLM()
        application = ErrGrindApplication(db, llm, PromptManager())
        web = create_app(application=application, secret_key='assistant-real-boundary')
        client = web.test_client()
        csrf, tokens = self._bootstrap_for(client)
        png = b'\x89PNG\r\n\x1a\noriginal-record-image'

        draft = client.post(
            '/api/assistant/record/draft',
            data={
                'raw_input': '请整理这道题',
                'question': '', 'user_thoughts': '', 'reference_answer': '',
                'images': (io.BytesIO(png), 'work.png'),
            }, content_type='multipart/form-data',
            headers={'X-CSRFToken': csrf, 'X-Submission-Token': tokens['record_draft']},
        )
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(db.list_all_errors(), [])
        draft_payload = draft.get_json()
        self.assertEqual(draft_payload['pending_attachment_count'], 1)
        refreshed = client.get('/assistant-ui/bootstrap').get_json()
        self.assertEqual(refreshed['record_pending_attachment_count'], 1)

        # A later text-only Record turn must not lose the already uploaded image.
        revised = client.post(
            '/api/assistant/record/draft',
            data={
                'raw_input': '补充：这是我当时的草图',
                'question': draft_payload['draft']['question'],
                'user_thoughts': draft_payload['draft']['user_thoughts'],
                'reference_answer': draft_payload['draft']['reference_answer'],
            }, content_type='multipart/form-data',
            headers={'X-CSRFToken': csrf, 'X-Submission-Token': draft_payload['submit_token']},
        )
        self.assertEqual(revised.status_code, 200)
        self.assertEqual(revised.get_json()['pending_attachment_count'], 1)
        revised_message = llm.messages[-1][0]
        self.assertIsInstance(revised_message, MultimodalMessage)
        self.assertEqual([image.data for image in revised_message.images], [png])

        # Finalization no longer depends on re-uploading a browser File object.
        finalized = client.post(
            '/api/assistant/record/finalize',
            data={
                'question': '求 x', 'user_thoughts': '我直接套了公式', 'reference_answer': 'x=2',
            }, content_type='multipart/form-data',
            headers={'X-CSRFToken': csrf, 'X-Submission-Token': tokens['record_finalize']},
        )
        self.assertEqual(finalized.status_code, 200)
        error_id = finalized.get_json()['error']['id']
        stored = db.list_error_attachments(error_id, conversation_kind='initial')
        self.assertEqual([item.data for item in stored], [png])

        # The fake provider's first Grill call should be the initial probe;
        # the preceding Record draft call is a separate workflow.
        llm.chat_json_calls = 0
        started = client.post(
            '/api/assistant/grill/start',
            json={'error_id': error_id},
            headers={
                'X-CSRFToken': csrf,
                'X-Submission-Token': finalized.get_json()['submit_token'],
            },
        )
        self.assertEqual(started.status_code, 200)
        conversation = json.loads(db.get_error(error_id).grilling_conversation)
        attached_messages = [item for item in conversation if item.get('attachments')]
        self.assertEqual(attached_messages[0]['attachments'], [stored[0].id])
        answered = client.post(
            '/api/assistant/grill/answer',
            data={'error_id': str(error_id), 'answer': '当时没有核对条件'},
            content_type='multipart/form-data',
            headers={
                'X-CSRFToken': csrf,
                'X-Submission-Token': started.get_json()['submit_token'],
            },
        )
        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.get_json()['error']['id'], error_id)
        self.assertTrue(answered.get_json()['assistant_response'])
        self.assertTrue(draft_payload['ready'])
        db.close()

    def test_failed_record_draft_retry_reuses_durable_pending_image(self):
        db = Database(str(self.temp.name + '/retry.db'))
        self.addCleanup(db.close)
        llm = _FakeLLM()
        llm.fail = True
        application = ErrGrindApplication(db, llm, PromptManager())
        web = create_app(application=application, secret_key='assistant-retry-boundary')
        client = web.test_client()
        csrf, tokens = self._bootstrap_for(client)
        png = b'\x89PNG\r\n\x1a\npending-retry-image'

        failed = client.post(
            '/api/assistant/record/draft',
            data={
                'raw_input': '',
                'question': '', 'user_thoughts': '', 'reference_answer': '',
                'images': (io.BytesIO(png), 'retry.png'),
            },
            content_type='multipart/form-data',
            headers={
                'X-CSRFToken': csrf,
                'X-Submission-Token': tokens['record_draft'],
            },
        )
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(
            client.get('/assistant-ui/bootstrap').get_json()['record_pending_attachment_count'],
            1,
        )

        llm.fail = False
        retried = client.post(
            '/api/assistant/record/draft',
            data={
                'raw_input': '补充说明',
                'question': '', 'user_thoughts': '', 'reference_answer': '',
            },
            content_type='multipart/form-data',
            headers={
                'X-CSRFToken': csrf,
                'X-Submission-Token': failed.get_json()['submit_token'],
            },
        )
        self.assertEqual(retried.status_code, 200)
        message = llm.messages[-1][0]
        self.assertIsInstance(message, MultimodalMessage)
        self.assertEqual([image.data for image in message.images], [png])

    @staticmethod
    def _bootstrap_for(client):
        response = client.get('/assistant-ui/bootstrap')
        payload = response.get_json()
        return payload['csrf'], payload['tokens']


if __name__ == '__main__':
    unittest.main()
