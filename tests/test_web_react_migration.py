import io
import json
import tempfile
import unittest
from pathlib import Path

from errgrind.application import ErrGrindApplication
from errgrind.db.ops import Database
from errgrind.llm.prompts import PromptManager
from errgrind.web.app import create_app
from tests.test_web import _FakeLLM


class ReactMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "web.db"))
        self.llm = _FakeLLM()
        self.application = ErrGrindApplication(self.db, self.llm, PromptManager())
        self.web = create_app(application=self.application, secret_key="react-migration")
        self.web.testing = True
        self.client = self.web.test_client()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def bootstrap(self, error_id=None):
        path = "/assistant-ui/bootstrap" if error_id is None else f"/assistant-ui/bootstrap?error_id={error_id}"
        payload = self.client.get(path).get_json()
        return payload["csrf"], payload["tokens"]

    def post_json_token(self, path, csrf, token, **values):
        return self.client.post(path, json=values, headers={
            "X-CSRFToken": csrf, "X-Submission-Token": token,
        })

    def test_single_port_shell_and_existing_error_bootstrap_are_public(self):
        error_id = self.db.create_error("求 $x$", "我直接套了公式", "x=2")
        shell = self.client.get(f"/errors/{error_id}")
        self.assertEqual(shell.status_code, 200)
        self.assertIn(b"<div id=\"root\">", shell.data)
        self.assertIn(b"/static/assistant-ui/assets/", shell.data)

        payload = self.client.get(f"/assistant-ui/bootstrap?error_id={error_id}").get_json()
        self.assertEqual(payload["workspace"]["error"]["id"], error_id)
        self.assertEqual(payload["workspace"]["messages"][0]["role"], "user")
        self.assertEqual(payload["workspace"]["next_step"]["label"], "Grill")
        self.assertNotIn("grilling_diagnostic_state", json.dumps(payload))
        self.assertIn("teach_start", payload["tokens"])

    def test_image_only_record_transport(self):
        csrf, tokens = self.bootstrap()
        record = self.client.post(
            "/api/assistant/record/draft",
            data={
                "raw_input": "", "question": "", "user_thoughts": "", "reference_answer": "",
                "images": (io.BytesIO(b"\x89PNG\r\n\x1a\nrecord"), "record.png"),
            }, content_type="multipart/form-data",
            headers={"X-CSRFToken": csrf, "X-Submission-Token": tokens["record_draft"]},
        )
        self.assertEqual(record.status_code, 200)

    def test_image_only_teach_answer_transport(self):
        error_id = self.db.create_error("求 $x$。", "我直接套了公式", "x=2")
        csrf, tokens = self.bootstrap()
        started = self.post_json_token("/api/assistant/grill/start", csrf, tokens["grill_start"], error_id=error_id)
        answered = self.client.post(
            "/api/assistant/grill/answer",
            data={"error_id": str(error_id), "answer": "当时没有核对条件"},
            content_type="multipart/form-data",
            headers={"X-CSRFToken": csrf, "X-Submission-Token": started.get_json()["submit_token"]},
        )
        self.assertEqual(answered.status_code, 200)
        csrf, tokens = self.bootstrap()
        teach = self.post_json_token("/api/assistant/teach/start", csrf, tokens["teach_start"], error_id=error_id)
        self.assertEqual(teach.status_code, 200)
        answer = self.client.post(
            "/api/assistant/teach/answer",
            data={"error_id": str(error_id), "answer": "", "images": (io.BytesIO(b"\x89PNG\r\n\x1a\nteach"), "teach.png")},
            content_type="multipart/form-data",
            headers={"X-CSRFToken": csrf, "X-Submission-Token": teach.get_json()["submit_token"]},
        )
        self.assertEqual(answer.status_code, 200)
        conversation = json.loads(self.db.get_error(error_id).teach_conversation)
        self.assertTrue(any(item.get("attachments") for item in conversation))

    def test_config_visible_route_uses_react_shell_and_public_payload_hides_key(self):
        configured = create_app(
            application=self.application,
            cfg={
                "provider": "gemini", "model": "gemini-test",
                "api_key": "TOP-SECRET-KEY", "reasoning_effort": None,
                "drill_context_n": 10, "grill_max_turns": 30,
            },
            secret_key="react-settings",
        )
        configured.testing = True
        client = configured.test_client()
        page = client.get("/config")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'<div id="root">', page.data)
        self.assertIn("调整模型服务、提供商及运行参数。".encode(), page.data)
        self.assertNotIn(b"TOP-SECRET-KEY", page.data)

        payload = client.get("/api/assistant/config").get_json()
        self.assertEqual(payload["settings"]["provider"], "gemini")
        self.assertTrue(payload["api_key_set"])
        self.assertNotIn("api_key", payload["settings"])
        self.assertNotIn("TOP-SECRET-KEY", json.dumps(payload))
        bootstrap = client.get("/assistant-ui/bootstrap").get_json()
        self.assertIn("config_save", bootstrap["tokens"])

    def test_drill_visible_route_uses_react_shell_and_entry_is_model_free(self):
        before_json = self.llm.chat_json_calls
        before_chat = self.llm.chat_calls
        entered = self.client.get("/drill")
        self.assertEqual(entered.status_code, 303)
        self.assertEqual(self.llm.chat_json_calls, before_json)
        self.assertEqual(self.llm.chat_calls, before_chat)

        location = entered.headers["Location"]
        page = self.client.get(location)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'<div id="root">', page.data)
        self.assertIn("根据历史 Error 生成一题新的练习。".encode(), page.data)
        self.assertEqual(self.llm.chat_json_calls, before_json)
        self.assertEqual(self.llm.chat_calls, before_chat)

        key = location.rsplit("/", 1)[-1]
        payload = self.client.get(f"/api/assistant/drill/{key}").get_json()
        self.assertEqual(payload["state"], "idle")
        self.assertIsNone(payload["question"])
        self.assertIn("prepare", payload["tokens"])
        self.assertIn("judge", payload["tokens"])
        self.assertEqual(self.llm.chat_json_calls, before_json)
        self.assertEqual(self.llm.chat_calls, before_chat)

    def test_hashed_react_assets_are_immutable_and_gzipped_but_html_is_not_cached(self):
        shell = self.client.get('/')
        self.assertEqual(shell.headers.get('Cache-Control'), 'no-store')
        assets = sorted(Path('errgrind/web/static/assistant-ui/assets').glob('index-*.js'))
        self.assertTrue(assets)
        path = '/static/assistant-ui/assets/' + assets[0].name
        response = self.client.get(path, headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('Cache-Control'), 'public, max-age=31536000, immutable')
        self.assertEqual(response.headers.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', response.headers.get('Vary', ''))
        response.close()

    def test_react_sources_cover_centered_record_markdown_context_and_fixed_shell(self):
        app_source = Path("frontend/src/App.tsx").read_text()
        styles = Path("frontend/src/styles.css").read_text()
        self.assertIn('className="record-landing"', app_source)
        self.assertIn('StaticMarkdown text={workspace.error.question}', app_source)
        self.assertIn('background: #eaf3ff', styles)
        self.assertIn('background: #fff; border-right', styles)
        self.assertIn('height: 100dvh; overflow: hidden', styles)
        self.assertIn('className="thread-viewport"', app_source)
        self.assertIn('.thread-viewport { height: 100%; overflow-y: auto; overflow-x: hidden; }', styles)
        self.assertIn('drill.pending_attachment_count === 0', app_source)

    def test_active_teach_keeps_composer_and_drill_next_step_together(self):
        error_id = self.db.create_error("解方程", "我漏了一个分支", "完整答案")
        self.db.update_grilling(error_id, "[]", "当前诊断")
        self.db.save_teach_conversation(error_id, json.dumps([
            {"role": "assistant", "content": "先说明为什么会漏掉这个分支。"}
        ], ensure_ascii=False))
        payload = self.client.get(f"/api/assistant/workspace/{error_id}").get_json()
        self.assertEqual(payload["composer"], "teach")
        self.assertEqual(payload["next_step"]["code"], "finish_teach_and_drill")
        self.assertEqual(payload["next_step"]["label"], "Drill")
        self.assertEqual(payload["next_step"]["description"], "继续追问，或进入一道根据历史 Error 生成的练习。")

    def test_attachment_endpoint_checks_error_ownership(self):
        first = self.db.create_error("一", "思路", "答案", attachments=[("image/png", b"png")])
        second = self.db.create_error("二", "思路", "答案")
        attachment_id = self.db.list_error_attachments(first, conversation_kind="initial")[0].id
        self.assertEqual(self.client.get(f"/api/assistant/errors/{first}/attachments/{attachment_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/assistant/errors/{second}/attachments/{attachment_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
