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
from errgrind.llm.messages import MultimodalMessage


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
        self.grill_json_calls = 0
        self.chat_calls = 0
        self.messages = []
        self.transcribed = []
        self.record_outputs = []
        self.fail = False
        self.block = None
        self.judge_correct = False

    def chat_json(self, messages, **_kwargs):
        self.messages.append(messages)
        if self.fail:
            raise RuntimeError("provider body must stay private")
        if self.block is not None:
            entered, release = self.block
            entered.set(); release.wait(5)
        self.chat_json_calls += 1
        schema = _kwargs.get("output_schema", {})
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        if "new_hypotheses" in required:
            self.grill_json_calls += 1
            if self.grill_json_calls == 1:
                return _decision("reasoning_question")
            diagnostic = "\n".join(
                message.get("content", "")
                for message in messages
                if isinstance(message, dict) and message.get("role") == "system"
            )
            attachment_refs = re.findall(
                r"message:[0-9]+:attachment:[1-9][0-9]*", diagnostic
            )
            if attachment_refs:
                source_ref, quote = attachment_refs[-1], ""
            else:
                source_ref, quote = "message:3", "当时没有核对条件"
            return _decision("finish_supported", evidence=[{
                "source_ref": source_ref, "quote": quote,
                "interpretation": "回答支持忽略适用条件。", "supports": ["H1"],
                "contradicts": [], "probe_id": "P1",
            }])
        if "source_error_number" in required:
            return _spec()
        if required == {"question", "user_thoughts", "reference_answer"}:
            if self.record_outputs:
                return self.record_outputs.pop(0)
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

    def chat(self, messages):
        self.messages.append(messages)
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

    def test_home_navigation_and_record_compatibility_converge_on_landing(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        html = page.data.decode()
        self.assertIn('href="/">ErrGrind</a>', html)
        self.assertIn(
            '<a class="nav-link" href="/"><span class="nav-icon" aria-hidden="true">+</span><span>New error</span></a>',
            html,
        )
        self.assertNotIn('写下发生了什么。你可以先说一段自然的话，之后再校对整理结果。', html)
        self.assertNotIn('可以附上题目、草稿或截图', html)
        self.assertIn('placeholder="输入你的错题，当时思路，及参考答案（可选）"', html)

        compatibility = self.client.get('/record')
        self.assertEqual(compatibility.status_code, 302)
        self.assertEqual(compatibility.headers['Location'], '/')

    def test_entering_drill_is_model_free_until_explicit_prepare(self):
        source = self.db.create_error('求 x', '我直接套了公式')
        self.db.update_grilling(source, '[]', '当前最受 Evidence 支持的解释')

        entered = self.client.get('/drill')
        self.assertEqual(entered.status_code, 303)
        ready = self.client.get(entered.headers['Location'])
        self.assertEqual(ready.status_code, 200)
        html = ready.data.decode()
        self.assertIn('根据历史 Error 生成一题新的练习。', html)
        self.assertIn('开始后会从已完成诊断的 Error 中选择一个目标并生成题目。', html)
        self.assertIn('开始 Drill', html)
        self.assertNotIn('data-auto-prepare', html)
        self.assertEqual(self.llm.chat_json_calls, 0)
        self.assertEqual(self.llm.chat_calls, 0)

        key = entered.headers['Location'].rsplit('/', 1)[-1]
        csrf, token = _credentials(ready.data, f'/api/drill/{key}/prepare')
        prepared = self.client.post(
            f'/api/drill/{key}/prepare',
            data={'csrf': csrf, 'submit_token': token},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(self.llm.chat_json_calls, 2)
        question = self.client.get(f'/drill/{key}')
        self.assertIn('根据历史 Error 生成'.encode(), question.data)
        self.assertIn('解方程'.encode(), question.data)
        self.assertNotIn('source_error_number'.encode(), question.data)
        self.assertNotIn('target_pattern'.encode(), question.data)

    def test_record_list_detail_and_public_diagnostic_redaction(self):
        record_html = self.client.get("/").data.decode()
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
        self.assertIn(b"Settings", page.data)
        self.assertIn("What’s your error?".encode(), page.data)
        self.assertIn(b'data-draft-submit', page.data)
        self.assertNotIn(b"pending-grill", page.data)
        detail = self.client.get(f"/errors/{record.id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("求".encode(), detail.data)
        self.assertIn(b'class="message-stream"', detail.data)
        self.assertIn(b"title-context", detail.data)
        self.assertIn(b"Delete error", detail.data)
        self.assertNotIn(b"Problem", detail.data)
        self.assertNotIn(b"problem-control", detail.data)
        self.assertNotIn(b"Original Error", detail.data)
        self.assertNotIn(b'data-workflow="Grill Teach"', detail.data)
        self.assertNotIn(b"pending-grill", detail.data)

        private = "HIDDEN_LEDGER_9A2"
        self.db.save_grilling_progress(record.id, "[]", json.dumps({"private": private}))
        detail = self.client.get(f"/errors/{record.id}")
        self.assertNotIn(private.encode(), detail.data)

    def test_history_status_dots_match_workflow_status_and_selection(self):
        pending_grill = self.db.create_error("First title", "当时的思路")
        pending_teach = self.db.create_error("Second title", "当时的思路")
        done = self.db.create_error("Third title", "当时的思路")
        self.db.update_grilling(pending_teach, "[]", "诊断总结")
        self.db.set_status(done, "done")

        page = self.client.get(f"/errors/{pending_grill}")
        self.assertEqual(page.status_code, 200)
        html = page.data.decode()
        self.assertEqual(html.count('class="status-dot red"'), 1)
        self.assertEqual(html.count('class="status-dot amber"'), 1)
        self.assertNotIn("待澄清", html)
        self.assertNotIn("可以继续理解", html)
        self.assertNotIn("可以继续练习", html)
        self.assertIn(f'class="history-item is-current" href="/errors/{pending_grill}"', html)
        self.assertIn(f'class="history-item" href="/errors/{pending_teach}"', html)
        self.assertIn(
            f'<a class="history-item" href="/errors/{done}" aria-label="Third title"><span class="history-title">Third title</span></a>',
            html,
        )

    def test_history_uses_persisted_title_and_compact_legacy_fallback(self):
        stored = self.db.create_error(
            "这是一个很长的题目正文，不应该被侧栏重新截成一整段原文。",
            "我的思路",
            display_title="三角函数值域判断",
        )
        legacy = self.db.create_error(
            "某装置记录 t=0 开始，函数 H(t)=t^3-3t^2+4 在半开区间内是否具有最大值和最小值，"
            "请说明不能达到的边界。",
            "我的思路",
        )
        self.db.conn.execute(
            "UPDATE error_records SET display_title = NULL WHERE id = ?", (legacy,)
        )
        self.db.conn.commit()

        html = self.client.get("/").data.decode()
        self.assertIn('aria-label="三角函数值域判断"', html)
        self.assertIn('aria-label="H(t) 的最值判断"', html)
        self.assertNotIn("某装置记录的函数 H(t)", html)
        self.assertEqual(self.db.get_error(stored).display_title, "三角函数值域判断")
        self.assertEqual(self.db.get_error(legacy).display_title, "H(t) 的最值判断")

    def test_sidebar_controls_and_record_preview_are_simplified(self):
        page = self.client.get('/').data.decode()
        self.assertEqual(page.count('data-sidebar-toggle'), 2)
        self.assertIn('class="sidebar-reopen"', page)
        self.assertIn('id="drawer-toggle"', page)
        self.assertNotIn('data-show-preview', page)
        self.assertNotIn('或者直接填写结构化内容', page)
        self.assertIn('id="home-record-preview" data-preview hidden', page)

        javascript = Path('errgrind/web/static/app.js').read_text()
        self.assertIn('errgrind:sidebar-collapsed', javascript)
        self.assertIn("matchMedia('(max-width: 760px)')", javascript)
        self.assertIn('drawerToggle.checked = false', javascript)
        styles = Path('errgrind/web/static/app.css').read_text()
        self.assertIn('body.sidebar-collapsed .sidebar', styles)
        self.assertIn('body.sidebar-collapsed .page', styles)

    def test_timeline_has_no_visible_author_labels_or_drill_metadata(self):
        error_id = self.db.create_error('求 x', '我直接套了公式')
        self.db.update_grilling(error_id, json.dumps([
            {'role': 'user', 'content': '当时没有核对条件'},
            {'role': 'assistant', 'content': '请先检查适用条件。'},
        ], ensure_ascii=False), '忽略适用条件')
        attempt_id = self.db.record_drill_attempt(
            error_id, {}, '练习题', '参考答案', '用户答案', True, '反馈'
        ).attempt_id
        self.db.conn.execute(
            'UPDATE error_records SET source_drill_attempt_id = ? WHERE id = ?',
            (attempt_id, error_id),
        )
        self.db.conn.commit()

        html = self.client.get(f'/errors/{error_id}').data.decode()
        self.assertNotIn('message-author', html)
        self.assertNotIn('>你<', html)
        self.assertNotIn('关联 Drill', html)
        self.assertIn('aria-label="用户消息"', html)
        self.assertIn('aria-label="助手消息"', html)
        self.assertNotIn('border-top: 1px dashed', Path('errgrind/web/static/app.css').read_text())

    def test_duplicate_grill_summary_is_rendered_once_without_mutating_history(self):
        summary = '当前最受 Evidence 支持的解释是忽略适用条件。'
        error_id = self.db.create_error('求 x', '我直接套了公式')
        conversation = json.dumps([
            {'role': 'assistant', 'content': '当时为什么这样做？'},
            {'role': 'user', 'content': '没有核对条件。'},
            {'role': 'assistant', 'content': summary},
        ], ensure_ascii=False)
        self.db.update_grilling(error_id, conversation, summary)

        html = self.client.get(f'/errors/{error_id}').data.decode()
        self.assertEqual(html.count(summary), 1)
        self.assertEqual(json.loads(self.db.get_error(error_id).grilling_conversation), json.loads(conversation))

    def test_contextual_cta_states_and_teach_to_drill_mutation(self):
        fresh = self.db.create_error('新题', '还没有思路')
        fresh_html = self.client.get(f'/errors/{fresh}').data.decode()
        self.assertIn('class="action-cta"', fresh_html)
        self.assertIn('>Grill<', fresh_html)
        self.assertNotIn('class="current-composer"', fresh_html)

        waiting = self.db.create_error('等待题', '我直接套公式')
        self.db.save_grilling_progress(waiting, json.dumps([
            {'role': 'assistant', 'content': '你当时先看到了什么？'},
        ], ensure_ascii=False), None)
        waiting_html = self.client.get(f'/errors/{waiting}').data.decode()
        self.assertNotIn('class="next-step"', waiting_html)
        self.assertIn('id="grill-answer"', waiting_html)

        pending_teach = self.db.create_error('教学题', '我直接套公式')
        self.db.update_grilling(pending_teach, '[]', '诊断摘要')
        teach_html = self.client.get(f'/errors/{pending_teach}').data.decode()
        self.assertIn('>Teach<', teach_html)
        self.assertNotIn(f'/errors/{pending_teach}/teach/drill', teach_html)

        active_teach = self.db.create_error('练习前题', '我直接套公式')
        self.db.update_grilling(active_teach, '[]', '诊断摘要')
        self.db.save_teach_conversation(active_teach, json.dumps([
            {'role': 'assistant', 'content': '先解释你当时的判断。'},
        ], ensure_ascii=False))
        active_html = self.client.get(f'/errors/{active_teach}').data.decode()
        drill_path = f'/errors/{active_teach}/teach/drill'
        self.assertEqual(self.db.get_error(active_teach).status, 'pending-teach')
        self.assertIn(f'action="{drill_path}"', active_html)
        self.assertIn('>Drill<', active_html)
        self.assertIn('id="teach-answer"', active_html)
        self.assertNotIn('Suggested next', active_html)
        self.assertNotIn('Continue', active_html)
        self.assertNotIn('稍后继续', active_html)
        self.assertNotIn('保存并退出', active_html)

        csrf, token = _credentials(active_html, drill_path)
        finished = self.client.post(
            drill_path,
            data={'csrf': csrf, 'submit_token': token},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(finished.get_json()['redirect'], '/drill')
        self.assertEqual(self.db.get_error(active_teach).status, 'done')
        replay = self.client.post(
            drill_path,
            data={'csrf': csrf, 'submit_token': token},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(replay.status_code, 409)

        done_html = self.client.get(f'/errors/{active_teach}').data.decode()
        self.assertIn('href="/drill"', done_html)
        self.assertNotIn(f'action="{drill_path}"', done_html)

    def test_latex_heavy_historical_title_is_backfilled_and_never_rendered_raw(self):
        error_id = self.db.create_error(
            r'解方程 \frac{x+1}{2}=3，并说明 \sqrt{x} 的定义域。',
            '我直接套用了公式',
        )
        self.db.conn.execute(
            'UPDATE error_records SET display_title = ? WHERE id = ?',
            ('frac{x+1}{2}=3', error_id),
        )
        self.db.conn.commit()

        html = self.client.get(f'/errors/{error_id}').data.decode()
        title = re.search(r'<title>(.*?)</title>', html, re.DOTALL).group(1)
        history_title = re.search(r'<span class="history-title">(.*?)</span>', html, re.DOTALL).group(1)
        self.assertNotIn('frac{', title)
        self.assertNotIn('frac{', history_title)
        self.assertNotIn('\\frac', title)
        self.assertEqual(self.db.get_error(error_id).display_title, '方程求解')

    def test_grill_bootstrap_is_hidden_but_later_same_user_text_and_waiting_state_remain(self):
        error_id = self.db.create_error("求 x", "我直接套了公式")
        self.db.save_grilling_progress(error_id, json.dumps([
            {"role": "system", "content": "private protocol"},
            {"role": "user", "content": "开始吧"},
            {"role": "assistant", "content": "第一个追问"},
            {"role": "user", "content": "开始吧"},
            {"role": "assistant", "content": "请继续回答这个问题"},
        ], ensure_ascii=False), None)

        html = self.client.get(f"/errors/{error_id}").data.decode()
        self.assertEqual(html.count("开始吧"), 1)
        self.assertIn("请继续回答这个问题", html)
        self.assertNotIn('class="next-step"', html)
        self.assertIn('data-current-work', html)

    def test_delete_error_requires_confirmation_and_one_time_submission(self):
        error_id = self.db.create_error("待删除题目", "我的思路")
        page = self.client.get(f"/errors/{error_id}")
        self.assertIn(b'name="confirm_delete" value="1"', page.data)
        self.assertNotIn(b'type="checkbox" name="confirm_delete"', page.data)
        csrf, token = _credentials(page.data, f"/errors/{error_id}/delete")

        missing_confirmation = self.client.post(
            f"/errors/{error_id}/delete",
            data={"csrf": csrf, "submit_token": token},
        )
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertIsNotNone(self.db.get_error(error_id))

        replay = self.client.post(
            f"/errors/{error_id}/delete",
            data={"csrf": csrf, "submit_token": token, "confirm_delete": "1"},
        )
        self.assertEqual(replay.status_code, 409)
        self.assertIsNotNone(self.db.get_error(error_id))

        page = self.client.get(f"/errors/{error_id}")
        csrf, token = _credentials(page.data, f"/errors/{error_id}/delete")
        invalid_csrf = self.client.post(
            f"/errors/{error_id}/delete",
            data={"csrf": "wrong", "submit_token": token, "confirm_delete": "1"},
        )
        self.assertEqual(invalid_csrf.status_code, 403)
        self.assertIsNotNone(self.db.get_error(error_id))

        deleted = self.client.post(
            f"/errors/{error_id}/delete",
            data={"csrf": csrf, "submit_token": token, "confirm_delete": "1"},
        )
        self.assertEqual(deleted.status_code, 303)
        self.assertEqual(deleted.headers["Location"], "/")
        self.assertIsNone(self.db.get_error(error_id))

    def test_record_has_no_web_ocr_route_or_field_controls(self):
        html = self.client.get("/").data.decode()
        self.assertNotIn('/record/ocr/', html)
        self.assertNotIn('data-ocr-field', html)
        self.assertNotIn('识别图片', html)

    def test_raw_record_draft_is_reviewed_before_persistence(self):
        page = self.client.get('/').data.decode()
        self.assertNotIn('data-show-preview', page)
        self.assertNotIn('或者直接填写结构化内容', page)
        self.assertIn('id="home-record-preview" data-preview hidden', page)
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
        self.assertTrue(draft.get_json()['ready'])
        self.assertEqual(draft.get_json()['status'], 'ready')
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

    def test_incomplete_record_draft_is_a_successful_intermediate_update(self):
        page = self.client.get('/').data
        csrf, _ = _credentials(page, '/record')
        draft_token = re.search(
            rb'data-draft-submit data-draft-url="[^"]+" data-submit-token="([^"]+)"',
            page,
        ).group(1).decode()
        self.llm.record_outputs.append({
            'question': '', 'user_thoughts': '', 'reference_answer': '',
        })

        response = self.client.post(
            '/api/record/draft',
            data={'csrf': csrf, 'submit_token': draft_token, 'raw_input': '你好'},
            headers={'Accept': 'application/json'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['draft'], {
            'question': '', 'user_thoughts': '', 'reference_answer': '', 'origin': 'record',
        })
        self.assertFalse(payload['ready'])
        self.assertEqual(payload['status'], 'incomplete')
        self.assertEqual(payload['missing_fields'], ['question'])
        self.assertIn('题目', payload['message'])
        self.assertEqual(self.db.list_all_errors(), [])

    def test_record_draft_updates_the_same_editable_state_across_turns(self):
        page = self.client.get('/').data
        csrf, _ = _credentials(page, '/record')
        token = re.search(
            rb'data-draft-submit data-draft-url="[^"]+" data-submit-token="([^"]+)"',
            page,
        ).group(1).decode()
        self.llm.record_outputs.extend([
            {'question': '', 'user_thoughts': '', 'reference_answer': ''},
            {'question': '求 $x$', 'user_thoughts': '', 'reference_answer': ''},
        ])

        first = self.client.post(
            '/api/record/draft',
            data={'csrf': csrf, 'submit_token': token, 'raw_input': '你好'},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.get_json()
        second = self.client.post(
            '/api/record/draft',
            data={
                'csrf': csrf,
                'submit_token': first_payload['submit_token'],
                'raw_input': '题目是求 x。',
                **first_payload['draft'],
            },
            headers={'Accept': 'application/json'},
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()['draft']['question'], '求 $x$')
        self.assertTrue(second.get_json()['ready'])
        self.assertEqual(len(self.llm.messages), 2)
        self.assertIn('题目是求 x。', self.llm.messages[1][0]['content'])
        self.assertEqual(self.db.list_all_errors(), [])

    def test_manual_record_preview_edits_are_sent_as_current_draft(self):
        page = self.client.get('/').data
        csrf, _ = _credentials(page, '/record')
        token = re.search(
            rb'data-draft-submit data-draft-url="[^"]+" data-submit-token="([^"]+)"',
            page,
        ).group(1).decode()
        self.llm.record_outputs.extend([
            {'question': '模型题目', 'user_thoughts': '', 'reference_answer': ''},
            {'question': '人工修订题目', 'user_thoughts': '人工思路', 'reference_answer': '人工答案'},
        ])

        first = self.client.post(
            '/api/record/draft',
            data={'csrf': csrf, 'submit_token': token, 'raw_input': '初始材料'},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.get_json()
        second = self.client.post(
            '/api/record/draft',
            data={
                'csrf': csrf,
                'submit_token': first_payload['submit_token'],
                'raw_input': '请保留我的修订。',
                'question': '人工修订题目',
                'user_thoughts': '人工思路',
                'reference_answer': '人工答案',
            },
            headers={'Accept': 'application/json'},
        )

        self.assertEqual(second.status_code, 200)
        prompt = self.llm.messages[1][0]['content']
        self.assertIn('人工修订题目', prompt)
        self.assertIn('人工思路', prompt)
        self.assertIn('人工答案', prompt)
        self.assertEqual(self.db.list_all_errors(), [])

    def test_malformed_record_draft_output_stays_output_contract_422(self):
        page = self.client.get('/').data
        csrf, _ = _credentials(page, '/record')
        token = re.search(
            rb'data-draft-submit data-draft-url="[^"]+" data-submit-token="([^"]+)"',
            page,
        ).group(1).decode()
        self.llm.record_outputs.append({
            'question': [], 'user_thoughts': '', 'reference_answer': '',
        })

        response = self.client.post(
            '/api/record/draft',
            data={'csrf': csrf, 'submit_token': token, 'raw_input': '题目'},
            headers={'Accept': 'application/json'},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()['category'], 'output_contract')
        self.assertEqual(self.db.list_all_errors(), [])

    def test_image_assisted_record_draft_is_one_direct_multimodal_call(self):
        page = self.client.get('/').data.decode()
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
        self.assertEqual(response.get_json()['draft']['origin'], 'record')
        self.assertEqual(len(self.llm.transcribed), 0)
        self.assertEqual(self.llm.chat_json_calls, 1)
        multimodal = [
            message
            for batch in self.llm.messages
            for message in batch
            if isinstance(message, MultimodalMessage)
        ]
        self.assertEqual(len(multimodal), 1)
        self.assertEqual(multimodal[0].text.count('补充：这是我记得的思路。'), 1)
        self.assertEqual(multimodal[0].images[0].data, b'\x89PNG\r\n\x1a\nimage')
        self.assertEqual(self.db.list_all_errors(), [])

    def test_saved_record_image_is_publicly_only_a_compact_indicator(self):
        page = self.client.get('/')
        csrf, token = _credentials(page.data, '/record')
        response = self.client.post(
            '/record',
            data={
                'csrf': csrf,
                'submit_token': token,
                'question': '图片题目',
                'user_thoughts': '图片中的思路',
                'reference_answer': '',
                'images': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage'), '../../private-name.png'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 303)
        error_id = self.db.list_all_errors()[0].id
        detail = self.client.get(f'/errors/{error_id}')
        html = detail.data.decode()
        self.assertIn('＋ 图片', html)
        self.assertNotIn('data:image/', html)
        self.assertNotIn('private-name.png', html)

    def test_grill_submit_pause_resume_and_complete(self):
        error_id = self.db.create_error("求 x", "我直接套了公式", "x=2")
        start = self.client.get(f"/errors/{error_id}")
        self.assertEqual(start.status_code, 200)
        self.assertIn(b'class="message-stream"', start.data)
        self.assertIn(b">Grill<", start.data)
        self.assertNotIn(b"Suggested next", start.data)
        page = start.data.decode(); csrf, token = _credentials(page, f'/errors/{error_id}/grill/start')
        started = self.client.post(f"/errors/{error_id}/grill/start", data={"csrf": csrf, "submit_token": token}, headers={"Accept":"application/json"})
        self.assertEqual(started.status_code, 200)
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
        csrf, token = _credentials(teach_html, f'/errors/{error_id}/teach/drill')
        self.client.post(f"/errors/{error_id}/teach/drill", data={"csrf": csrf, "submit_token": token}, headers={"Accept": "application/json"})
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

    def test_drill_answer_image_goes_directly_to_judge_and_derived_error_keeps_it(self):
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
        csrf, judge_token = _credentials(page, f'/api/drill/{key}/judge')
        response = self.client.post(
            f'/api/drill/{key}/judge',
            data={
                'csrf': csrf,
                'submit_token': judge_token,
                'answer': '',
                'images': (io.BytesIO(b'\x89PNG\r\n\x1a\nimage'), 'answer.png'),
            },
            content_type='multipart/form-data',
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.drill_stats()['total'], 1)
        attempt = self.db.list_drill_attempts(limit=None)[0]
        self.assertFalse(attempt.is_correct)
        self.assertEqual(attempt.user_response, '')
        self.assertTrue(attempt.attachment_ids)
        derived = self.db.get_error(attempt.derived_error_id)
        self.assertIsNotNone(derived)
        self.assertNotIn('[image]', derived.user_thoughts or '')
        attachments = self.db.list_error_attachments(
            derived.id, conversation_kind='initial'
        )
        self.assertEqual([item.data for item in attachments], [b'\x89PNG\r\n\x1a\nimage'])
        self.assertTrue(
            any(
                isinstance(message, MultimodalMessage)
                and message.images[0].data == b'\x89PNG\r\n\x1a\nimage'
                for batch in self.llm.messages
                for message in batch
            )
        )

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
