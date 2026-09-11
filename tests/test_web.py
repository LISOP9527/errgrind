"""Thin WebUI integration checks.

These tests deliberately inject a real ErrGrindApplication backed by a
temporary SQLite database.  The only fake is the provider boundary, so the
workflow and privacy contracts are still exercised end to end.
"""

import io
import json
import re
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch
from html.parser import HTMLParser


class _Forms(HTMLParser):
    def __init__(self):
        super().__init__(); self.forms = []; self.current = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form': self.current = {'action': attrs.get('action', ''), 'values': {}}
        elif self.current is not None and tag == 'input':
            if attrs.get('name'):
                self.current['values'][attrs['name']] = attrs.get('value', '')
            if attrs.get('data-url'):
                self.current.setdefault('ocr', {})[attrs['data-url']] = attrs.get('data-token', '')
    def handle_endtag(self, tag):
        if tag == 'form' and self.current is not None: self.forms.append(self.current); self.current = None


def _form(html, action=None):
    parser = _Forms(); parser.feed(html.decode() if isinstance(html, bytes) else html)
    for form in parser.forms:
        if action is None or action in form['action']:
            return form
    raise AssertionError(f'form not found: {action}')

def _credentials(html, action=None):
    values = _form(html, action)['values']
    return values['csrf'], values['submit_token']

try:
    import flask  # noqa: F401
    import mdit_py_plugins  # noqa: F401
except ImportError:  # The optional web extra is not installed in core CI.
    flask = None
if flask is not None:
    from errgrind.web.app import create_app
else:
    create_app = None

from errgrind.application import ErrGrindApplication
from errgrind.db.ops import Database
from errgrind.llm.prompts import PromptManager
from errgrind.application import OutputContractError, WorkflowPersistenceError


def _decision(action="finish_supported", *, evidence=None):
    return {
        "new_hypotheses": [] if action != "reasoning_question" else [
            {"id": "H1", "claim": "忽略了题目中的限制条件"},
            {"id": "H2", "claim": "套用公式前没有核对适用条件"},
        ],
        "hypothesis_status_updates": [] if action == "reasoning_question" else [{"id": "H1", "status": "supported"}, {"id": "H2", "status": "weakened"}],
        "new_evidence": evidence if evidence is not None else [{
            "source_ref": "initial_user_thoughts",
            "quote": "我直接套了公式",
            "interpretation": "录题时思路回忆",
            "supports": ["H1"], "contradicts": [], "probe_id": "",
        }],
        "next_action": action,
        "probe": {
            "question": "当时为什么直接套用这个方法？" if action == "reasoning_question" else "",
            "target_hypothesis_ids": ["H1", "H2"] if action == "reasoning_question" else [],
            "discrimination_goal": "区分条件核对" if action == "reasoning_question" else "",
            "predictions": ([
                {"hypothesis_id": "H1", "expected_observation": "说不出条件"},
                {"hypothesis_id": "H2", "expected_observation": "知道条件但没核对"},
            ] if action == "reasoning_question" else []),
            "answer_key": "", "preserved_mechanism": "", "surface_change": "",
        },
        "best_hypothesis_id": "" if action == "reasoning_question" else "H1",
        "remaining_uncertainty": "还需要更多区分证据。" if action == "reasoning_question" else "",
        "what_would_change_judgment": "新的独立证据",
        "summary": "" if action == "reasoning_question" else "当前最受 Evidence 支持的解释是忽略适用条件后直接套用方法。",
    }


def _spec():
    return {
        "source_error_number": 1,
        "target_pattern": {
            "mechanism": "忽略适用条件", "trigger": "熟悉形式",
            "failure_behavior": "直接套用", "desired_behavior": "核对条件",
            "success_signal": "先列出条件",
        },
        "new_problem": {
            "domain": "代数", "task_type": "solve", "setting": "一元方程",
            "task_goal": "求未知数", "essential_trigger": "条件不自动成立",
            "solution_strategy": "先核对条件再求解", "avoid": ["复杂计算"],
        },
        "difficulty": {"level": 2, "reasoning_depth": 2, "calculation_load": 1},
    }


class _FakeLLM:
    def __init__(self):
        self.chat_json_calls = 0
        self.chat_calls = 0
        self.transcribed = []
        self.fail = False
        self.block = None
        self.judge_correct = False

    def chat_json(self, messages, **_kwargs):
        if self.fail:
            raise RuntimeError("provider body must stay private")
        if self.block is not None:
            entered, release = self.block
            entered.set(); release.wait(5)
        self.chat_json_calls += 1
        schema = _kwargs.get("output_schema", {})
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        if "new_hypotheses" in required:
            if self.chat_json_calls == 1:
                return _decision("reasoning_question")
            return _decision("finish_supported", evidence=[{
                "source_ref": "message:3", "quote": "当时没有核对条件",
                "interpretation": "回答支持忽略适用条件。", "supports": ["H1"],
                "contradicts": [], "probe_id": "P1",
            }])
        if "source_error_number" in required:
            return _spec()
        if required == {"question", "user_thoughts", "reference_answer"}:
            return {
                "question": "整理后的题目",
                "user_thoughts": "整理后的思路",
                "reference_answer": "整理后的答案",
            }
        if "is_correct" in required:
            return {"is_correct": self.judge_correct, "feedback": "正确。" if self.judge_correct else "请先核对适用条件。"}
        if required == {"question", "reference_answer"}:
            return {"question": "解方程 $x+1=2$。", "reference_answer": "x=1"}
        raise AssertionError(f"unexpected schema: {schema}")

    def chat(self, _messages):
        self.chat_calls += 1
        return "先解释你当时为什么这样判断。"

    def transcribe_image(self, path, _prompt):
        self.transcribed.append(path)
        return "OCR 识别的题目"


def make_fake_web_app(db_path):
    """Build the same isolated fake-provider app for a manual browser smoke."""
    return create_app(
        db_path=str(db_path),
        llm=_FakeLLM(),
        cfg={"provider": "test", "model": "fake", "drill_context_n": 10},
        secret_key="manual-smoke",
    )


@unittest.skipUnless(create_app is not None, "install the web extra")
class WebApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp.name) / "web.db"))
        self.llm = _FakeLLM()
        self.web = create_app(
            db_path=str(Path(self.temp.name) / "web.db"),
            llm=self.llm,
            cfg={"provider": "test", "model": "fake", "drill_context_n": 10},
            secret_key="test-secret",
        )
        self.web.testing = True
        self.client = self.web.test_client()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_record_list_detail_and_public_diagnostic_redaction(self):
        record_html = self.client.get("/record").data.decode()
        csrf, token = _credentials(record_html, '/record')
        response = self.client.post("/record", data={
            "csrf": csrf, "submit_token": token,
            "question": "求 $x$",
            "user_thoughts": "我直接套了公式",
            "reference_answer": "x=2",
        })
        self.assertIn(response.status_code, (200, 201, 302, 303))
        record = self.db.list_all_errors()[0]
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("待澄清".encode(), page.data)
        self.assertIn("What’s your error?".encode(), page.data)
        self.assertIn(b'data-draft-submit', page.data)
        self.assertNotIn(b"pending-grill", page.data)
        detail = self.client.get(f"/errors/{record.id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("求".encode(), detail.data)
        self.assertIn(b'class="message-stream"', detail.data)
        self.assertIn(b"Problem", detail.data)
        self.assertIn("查看题目".encode(), detail.data)
        self.assertIn("信息".encode(), detail.data)
        self.assertNotIn(b"Original Error", detail.data)
        self.assertNotIn(b'data-workflow="Grill Teach"', detail.data)
        self.assertNotIn(b"pending-grill", detail.data)

        private = "HIDDEN_LEDGER_9A2"
        self.db.save_grilling_progress(record.id, "[]", json.dumps({"private": private}))
        detail = self.client.get(f"/errors/{record.id}")
        self.assertNotIn(private.encode(), detail.data)

    def test_record_ocr_is_editable_draft_and_temp_file_removed(self):
        html = self.client.get("/record").data.decode()
        csrf = _form(html, '/record')['values']['csrf']
        ocr_form = _form(html, '/record')
        ocr_token = ocr_form['ocr']['/record/ocr/question']
        result = self.client.post("/record/ocr/question", data={
                "field": "question",
                "csrf": csrf, "submit_token": ocr_token,
                "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "question.png"),
            }, content_type="multipart/form-data", headers={"X-CSRFToken": csrf})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['text'], "OCR 识别的题目")
        self.assertEqual(self.db.list_all_errors(), [])
        self.assertEqual(len(self.llm.transcribed), 1)
        self.assertFalse(Path(self.llm.transcribed[0]).exists())

    def test_raw_record_draft_is_reviewed_before_persistence(self):
        page = self.client.get('/record').data.decode()
        self.assertIn('data-show-preview', page)
        self.assertIn('id="record-preview" data-preview hidden', page)
        save_csrf, save_token = _credentials(page, '/record')
        draft_token = re.search(
            r'data-draft-submit data-draft-url="[^"]+" data-submit-token="([^"]+)"',
            page,
        ).group(1)
        draft = self.client.post(
            '/api/record/draft',
            data={
                'csrf': save_csrf,
                'submit_token': draft_token,
                'raw_input': '题目：求 x。我的思路：直接套公式。',
            },
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(draft.get_json()['draft']['question'], '整理后的题目')
        self.assertEqual(self.db.list_all_errors(), [])

        saved = self.client.post(
            '/record',
            data={
                'csrf': save_csrf,
                'submit_token': save_token,
                'raw_input': '题目：求 x。我的思路：直接套公式。',
                'question': '整理后的题目',
                'user_thoughts': '整理后的思路',
                'reference_answer': '整理后的答案',
                'ocr_used': '0',
            },
        )
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(len(self.db.list_all_errors()), 1)

    def test_image_assisted_record_draft_stays_unpersisted_and_is_ocr_origin(self):
        page = self.client.get('/record').data.decode()
        csrf, _ = _credentials(page, '/record')
        draft_token = re.search(
            r'data-draft-submit data-draft-url="[^"]+" data-submit-token="([^"]+)"',
            page,
        ).group(1)
        response = self.client.post(
            '/api/record/draft',
            data={
                'csrf': csrf,
                'submit_token': draft_token,
                'raw_input': '补充：这是我记得的思路。',
                'images': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage'), 'paper.png'),
            },
            content_type='multipart/form-data',
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['draft']['origin'], 'ocr')
        self.assertEqual(len(self.llm.transcribed), 1)
        self.assertFalse(Path(self.llm.transcribed[0]).exists())
        self.assertEqual(self.db.list_all_errors(), [])

    def test_grill_submit_pause_resume_and_complete(self):
        error_id = self.db.create_error("求 x", "我直接套了公式", "x=2")
        start = self.client.get(f"/errors/{error_id}")
        self.assertEqual(start.status_code, 200)
        self.assertIn(b'class="message-stream"', start.data)
        self.assertNotIn(b">Grill<", start.data)
        self.assertNotIn(b">Teach<", start.data)
        page = start.data.decode(); csrf, token = _credentials(page, f'/errors/{error_id}/grill/start')
        started = self.client.post(f"/errors/{error_id}/grill/start", data={"csrf": csrf, "submit_token": token}, headers={"Accept":"application/json"})
        self.assertEqual(started.status_code, 200)
        page = self.client.get(f"/errors/{error_id}").data.decode()
        csrf, token = _credentials(page, f'/errors/{error_id}/grill/pause')
        paused = self.client.post(f"/errors/{error_id}/grill/pause", data={"csrf": csrf, "submit_token": token}, headers={"Accept":"application/json"})
        self.assertEqual(paused.status_code, 200)
        page = self.client.get(f"/errors/{error_id}").data.decode()
        csrf, token = _credentials(page, f'/errors/{error_id}/grill/answer')
        answered = self.client.post(f"/errors/{error_id}/grill/answer", data={"csrf": csrf, "submit_token": token, "answer": "当时没有核对条件"}, headers={"Accept": "application/json"})
        self.assertIn(answered.status_code, (200, 302))
        self.assertEqual(self.db.get_error(error_id).status, "pending-teach")
        self.assertNotIn(b"H1", answered.data)
        self.assertNotIn(b"grilling_diagnostic_state", answered.data)
        readonly = self.client.post(f"/errors/{error_id}/grill/answer", data={"csrf": csrf, "submit_token": token, "answer": "重复提交"}, headers={"Accept": "application/json"})
        self.assertIn(readonly.status_code, (400, 409))

    def test_teach_and_drill_wrong_answer_create_pending_error_without_reference_leak(self):
        error_id = self.db.create_error("求 x", "我直接套了公式", "原始参考答案隐藏")
        grill = self.client.get(f"/errors/{error_id}")
        csrf, token = _credentials(grill.data, f'/errors/{error_id}/grill/start')
        started = self.client.post(f"/errors/{error_id}/grill/start", data={"csrf": csrf, "submit_token": token}, headers={"Accept":"application/json"})
        grill = self.client.get(f"/errors/{error_id}")
        csrf, token = _credentials(grill.data, f'/errors/{error_id}/grill/answer')
        self.client.post(f"/errors/{error_id}/grill/answer", data={"csrf": csrf, "submit_token": token, "answer": "当时没有核对条件"}, headers={"Accept": "application/json"})
        teach = self.client.get(f"/errors/{error_id}")
        self.assertEqual(teach.status_code, 200)
        self.assertNotIn(b"HIDDEN", teach.data)
        teach_html = teach.data.decode(); csrf, token = _credentials(teach_html, f'/errors/{error_id}/teach/start')
        started = self.client.post(f"/errors/{error_id}/teach/start", data={"csrf": csrf, "submit_token": token}, headers={"Accept":"application/json"})
        teach_html = self.client.get(f"/errors/{error_id}").data.decode(); csrf, token = _credentials(teach_html, f'/errors/{error_id}/teach/answer')
        self.client.post(f"/errors/{error_id}/teach/answer", data={"csrf": csrf, "submit_token": token, "answer": "还有疑问"}, headers={"Accept": "application/json"})
        teach_html = self.client.get(f"/errors/{error_id}").data.decode()
        csrf, token = _credentials(teach_html, f'/errors/{error_id}/teach/finish')
        self.client.post(f"/errors/{error_id}/teach/finish", data={"csrf": csrf, "submit_token": token}, headers={"Accept": "application/json"})
        self.assertEqual(self.db.get_error(error_id).status, "done")

        home = self.client.get("/drill", follow_redirects=True).data.decode()
        key = home.split('data-drill-key="', 1)[1].split('"', 1)[0]
        csrf, token = _credentials(home, f'/api/drill/{key}/prepare')
        prepared = self.client.post(f"/api/drill/{key}/prepare", data={"csrf": csrf, "submit_token": token}, headers={"Accept": "application/json"})
        self.assertEqual(prepared.status_code, 200)
        payload = prepared.get_json()
        prepared_page = self.client.get(payload['redirect']).data
        self.assertIn("解方程".encode(), prepared_page)
        self.assertNotIn("x=1".encode(), prepared_page)
        judge_page = self.client.get(f"/drill/{key}").data.decode()
        csrf, token = _credentials(judge_page, f'/api/drill/{key}/judge')
        judged = self.client.post(f"/api/drill/{key}/judge", data={"csrf": csrf, "submit_token": token, "answer": "错误答案"}, headers={"X-CSRFToken": csrf, "Accept": "application/json"})
        self.assertEqual(judged.status_code, 200)
        result_page = self.client.get(judged.get_json()['redirect']).data
        self.assertIn("错误".encode(), result_page)
        self.assertIn("继续查看这道 Error".encode(), result_page)
        self.assertNotIn("请先核对".encode(), result_page)
        self.assertNotIn("REFERENCE_ONLY".encode(), result_page)
        records = self.db.list_all_errors()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].status, "pending-grill")

    def test_drill_answer_image_is_an_editable_draft_before_judge(self):
        source = self.db.create_error("求 x", "我直接套了公式")
        self.db.update_grilling(source, "[]", "当前最受 Evidence 支持的解释")
        page = self.client.get('/drill', follow_redirects=True).data.decode()
        key = page.split('data-drill-key="', 1)[1].split('"', 1)[0]
        csrf, token = _credentials(page, f'/api/drill/{key}/prepare')
        prepared = self.client.post(
            f'/api/drill/{key}/prepare',
            data={'csrf': csrf, 'submit_token': token},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(prepared.status_code, 200)
        page = self.client.get(f'/drill/{key}').data.decode()
        csrf, _ = _credentials(page, f'/api/drill/{key}/judge')
        ocr_token = re.search(r'data-drill-ocr[^>]+data-token="([^"]+)"', page).group(1)
        response = self.client.post(
            f'/api/drill/{key}/ocr',
            data={
                'csrf': csrf,
                'submit_token': ocr_token,
                'answer': '已有答案',
                'image': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage'), 'answer.png'),
            },
            content_type='multipart/form-data',
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.drill_stats()['total'], 0)
        page = self.client.get(f'/drill/{key}').data
        self.assertIn('已有答案'.encode(), page)
        self.assertIn('OCR 识别的题目'.encode(), page)

    def test_error_mapping_and_raw_model_html_is_escaped(self):
        missing = self.client.get("/errors/999999")
        self.assertEqual(missing.status_code, 404)
        error_id = self.db.create_error("<script>alert(1)</script>", "我直接套了公式")
        detail = self.client.get(f"/errors/{error_id}")
        self.assertNotIn(b"<script>alert(1)</script>", detail.data)
        self.assertIn(b"&lt;script&gt;", detail.data)
        grill = self.client.get(f"/errors/{error_id}")
        csrf, token = _credentials(grill.data, f'/errors/{error_id}/grill/start')
        self.client.post(f"/errors/{error_id}/grill/start", data={"csrf": csrf, "submit_token": token}, headers={"Accept":"application/json"})
        grill = self.client.get(f"/errors/{error_id}"); csrf, token = _credentials(grill.data, f'/errors/{error_id}/grill/answer')
        invalid = self.client.post(f"/errors/{error_id}/grill/answer", data={"csrf": csrf, "submit_token": token, "answer": ""}, headers={"Accept": "application/json"})
        self.assertIn(invalid.status_code, (400, 409))

    def test_provider_failure_maps_to_502_and_does_not_expose_body(self):
        error_id = self.db.create_error("求 x", "我直接套了公式")
        self.llm.fail = True
        page = self.client.get(f"/errors/{error_id}").data.decode()
        csrf, token = _credentials(page, f'/errors/{error_id}/grill/start')
        response = self.client.post(f"/errors/{error_id}/grill/start", data={"csrf": csrf, "submit_token": token}, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("provider body".encode(), response.data)
        self.assertEqual(self.db.get_error(error_id).status, "pending-grill")

    def test_domain_error_categories_are_safe(self):
        error_id = self.db.create_error("求 x", "我直接套了公式")
        for exc, status, category in ((OutputContractError("secret"), 422, "output_contract"),
                                      (WorkflowPersistenceError("secret"), 503, "persistence")):
            with self.subTest(category=category), patch.object(
                __import__('errgrind.web.app', fromlist=['ErrGrindApplication']).ErrGrindApplication,
                'start_or_resume_grill', side_effect=exc,
            ):
                page = self.client.get(f"/errors/{error_id}").data
                csrf, token = _credentials(page, f'/errors/{error_id}/grill/start')
                response = self.client.post(f"/errors/{error_id}/grill/start",
                    data={"csrf": csrf, "submit_token": token}, headers={"Accept": "application/json"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()['category'], category)
                self.assertNotIn(b"secret", response.data)
        self.assertEqual(self.client.get('/errors/99999').status_code, 404)
        self.assertEqual(self.client.post('/errors/1/grill/unknown', data={}).status_code, 403)

    def test_busy_guard_rejects_second_model_request(self):
        error_id = self.db.create_error("求 x", "我直接套了公式")
        other_id = error_id
        entered, release = threading.Event(), threading.Event()
        self.llm.block = (entered, release)
        clients = [self.web.test_client(), self.web.test_client()]
        forms = []
        for client, ident in zip(clients, (error_id, other_id)):
            page = client.get(f"/errors/{ident}").data
            forms.append((client, ident, _credentials(page, f'/errors/{ident}/grill/start')))
        result = []
        def first():
            c, ident, (csrf, token) = forms[0]
            result.append(c.post(f'/errors/{ident}/grill/start', data={'csrf': csrf, 'submit_token': token}, headers={'Accept':'application/json'}))
        thread = threading.Thread(target=first); thread.start(); self.assertTrue(entered.wait(2))
        c, ident, (csrf, token) = forms[1]
        second = c.post(f'/errors/{ident}/grill/start', data={'csrf': csrf, 'submit_token': token}, headers={'Accept':'application/json'})
        self.assertEqual(second.status_code, 409)
        release.set(); thread.join(5)
        self.assertEqual(len(result), 1)
        self.assertEqual(self.llm.chat_json_calls, 1)

    def test_correct_judge_records_once_and_replay_token_is_rejected(self):
        source = self.db.create_error("求 x", "我直接套了公式")
        self.db.update_grilling(source, "[]", "当前最受 Evidence 支持的解释是忽略适用条件")
        self.llm.judge_correct = True
        page = self.client.get('/drill', follow_redirects=True).data.decode()
        key = page.split('data-drill-key="', 1)[1].split('"', 1)[0]
        csrf, token = _credentials(page, f'/api/drill/{key}/prepare')
        prep = self.client.post(f'/api/drill/{key}/prepare', data={'csrf': csrf, 'submit_token': token}, headers={'Accept':'application/json'})
        page = self.client.get(f'/drill/{key}').data
        csrf, judge_token = _credentials(page, f'/api/drill/{key}/judge')
        stale_page = self.client.get(f'/drill/{key}').data
        _, stale_token = _credentials(stale_page, f'/api/drill/{key}/judge')
        judged = self.client.post(f'/api/drill/{key}/judge', data={'csrf': csrf, 'submit_token': judge_token, 'answer':'正确'}, headers={'Accept':'application/json'})
        self.assertEqual(judged.status_code, 200)
        self.assertEqual(self.db.drill_stats()['total'], 1)
        self.assertEqual(len(self.db.list_all_errors()), 1)
        replay = self.client.post(f'/api/drill/{key}/judge', data={'csrf': csrf, 'submit_token': judge_token, 'answer':'正确'}, headers={'Accept':'application/json'})
        self.assertEqual(replay.status_code, 409)
        cached = self.client.post(f'/api/drill/{key}/judge', data={'csrf': csrf, 'submit_token': stale_token, 'answer':'正确'}, headers={'Accept':'application/json'})
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(self.db.drill_stats()['total'], 1)


if __name__ == "__main__":
    unittest.main()
