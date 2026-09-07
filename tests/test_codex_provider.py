import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from errgrind.cli import app
from errgrind.config import DEFAULT_CODEX_MODEL, load
from errgrind.llm.codex import CodexClient
from errgrind.llm.ocr import OCR_OUTPUT_SCHEMA, TEXT_OUTPUT_SCHEMA, OcrError


class FakeTextInput:
    def __init__(self, text):
        self.text = text


class FakeLocalImageInput:
    def __init__(self, path):
        self.path = path


class FakeTurn:
    def __init__(self, events, text="hello"):
        self.events = events
        self.text = text
        self.interrupted = False

    def stream(self):
        yield from self.events

    def interrupt(self):
        self.interrupted = True

    def run(self):
        return SimpleNamespace(final_response=self.text)


class FakeThread:
    def __init__(self, text="hello", events=None):
        self.text = text
        self.events = events or []
        self.run_calls = []

    def run(self, prompt, **kwargs):
        self.run_calls.append((prompt, kwargs))
        return SimpleNamespace(final_response=self.text)

    def turn(self, prompt, **kwargs):
        self.run_calls.append((prompt, kwargs))
        return FakeTurn(self.events, self.text)


class FakeSDK:
    def __init__(self, thread):
        self.thread = thread
        self.calls = []

    def thread_start(self, **kwargs):
        self.calls.append(kwargs)
        return self.thread

    def account(self, **kwargs):
        return {"logged_in": True}

    def login_chatgpt(self):
        return "browser-handle"

    def login_chatgpt_device_code(self):
        return "device-handle"

    def models(self, **kwargs):
        return ["gpt-5"]

    def close(self):
        self.closed = True


class CodexProviderTests(unittest.TestCase):
    SDK_TYPES = (
        None,
        None,
        SimpleNamespace(deny_all="deny"),
        SimpleNamespace(read_only="read"),
        FakeTextInput,
        FakeLocalImageInput,
    )

    def test_effort_reaches_chat_stream_and_json_turns(self):
        for effort in (None, "high", "ultra", "future-effort"):
            with self.subTest(effort=effort):
                sdk = FakeSDK(FakeThread(text='{"ok": true}'))
                with CodexClient(sdk=sdk, reasoning_effort=effort) as client:
                    with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
                        client.chat([])
                        client.stream_chat([], lambda token: None)
                        client.chat_json([])
                self.assertEqual(len(sdk.thread.run_calls), 3)
                for _, kwargs in sdk.thread.run_calls:
                    if effort is None:
                        self.assertNotIn("effort", kwargs)
                    else:
                        self.assertEqual(kwargs["effort"], effort)

    def test_per_call_effort_overrides_configured_effort(self):
        sdk = FakeSDK(FakeThread())
        with CodexClient(sdk=sdk, reasoning_effort="high") as client:
            with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
                client.chat([], effort="low")
                client.stream_chat([], lambda token: None, effort=None)
        self.assertEqual(sdk.thread.run_calls[0][1]["effort"], "low")
        self.assertIsNone(sdk.thread.run_calls[1][1]["effort"])

    def test_chat_uses_read_only_and_denies_approvals(self):
        sdk = FakeSDK(FakeThread())
        client = CodexClient(sdk=sdk)
        self.addCleanup(client.close)
        with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "hello")
        self.assertEqual(sdk.calls[0]["sandbox"], "read")
        self.assertEqual(sdk.calls[0]["approval_mode"], "deny")

    def test_stream_extracts_only_agent_deltas(self):
        events = [SimpleNamespace(method="item/agentMessage/delta", payload=SimpleNamespace(delta="a")),
                  SimpleNamespace(method="turn/completed", payload=SimpleNamespace(delta="ignored"))]
        sdk = FakeSDK(FakeThread(events=events))
        tokens = []
        client = CodexClient(sdk=sdk)
        self.addCleanup(client.close)
        with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
            self.assertEqual(client.stream_chat([], tokens.append), "a")
        self.assertEqual(tokens, ["a"])

    def test_json_retries_invalid_response(self):
        class Thread(FakeThread):
            def __init__(self): super().__init__(); self.responses = iter(["bad", '{"ok": true}'])
            def turn(self, prompt, **kwargs):
                self.run_calls.append((prompt, kwargs))
                return FakeTurn([], next(self.responses))
        sdk = FakeSDK(Thread())
        client = CodexClient(sdk=sdk, max_retries=2)
        self.addCleanup(client.close)
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES), patch("errgrind.llm.codex.time.sleep"):
            self.assertEqual(client.chat_json([], output_schema=schema), {"ok": True})
        self.assertEqual(sdk.thread.run_calls[0][1]["output_schema"], schema)

    def test_generic_json_does_not_send_an_invalid_wildcard_schema(self):
        sdk = FakeSDK(FakeThread(text='{"ok": true}'))
        client = CodexClient(sdk=sdk)
        self.addCleanup(client.close)
        with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
            self.assertEqual(client.chat_json([]), {"ok": True})

        self.assertNotIn("output_schema", sdk.thread.run_calls[0][1])

    def test_keyboard_interrupt_requests_turn_interrupt(self):
        class InterruptingTurn(FakeTurn):
            def run(self):
                raise KeyboardInterrupt

        class Thread(FakeThread):
            def turn(self, prompt, **kwargs):
                self.run_calls.append((prompt, kwargs))
                self.turn_handle = InterruptingTurn([])
                return self.turn_handle

        sdk = FakeSDK(Thread())
        client = CodexClient(sdk=sdk)
        self.addCleanup(client.close)
        with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
            with self.assertRaises(KeyboardInterrupt):
                client.chat([{"role": "user", "content": "hi"}])
        self.assertTrue(sdk.thread.turn_handle.interrupted)

    def test_ocr_uses_local_image_and_structured_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "problem.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            thread = FakeThread(
                text=json.dumps(
                    {
                        "question": "求 $x$",
                        "user_thoughts": "移项",
                        "reference_answer": "$x=2$",
                    },
                    ensure_ascii=False,
                )
            )
            sdk = FakeSDK(thread)
            client = CodexClient(sdk=sdk, reasoning_effort="high")
            self.addCleanup(client.close)

            with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
                result = client.ocr_image(str(image_path), "OCR prompt")

        self.assertEqual(result["question"], "求 $x$")
        turn_input, turn_kwargs = thread.run_calls[0]
        self.assertIsInstance(turn_input[0], FakeTextInput)
        self.assertIsInstance(turn_input[1], FakeLocalImageInput)
        self.assertEqual(turn_input[1].path, str(image_path.resolve()))
        self.assertEqual(turn_kwargs["output_schema"], OCR_OUTPUT_SCHEMA)
        self.assertEqual(turn_kwargs["effort"], "high")
        self.assertEqual(sdk.calls[0]["sandbox"], "read")
        self.assertEqual(sdk.calls[0]["approval_mode"], "deny")

    def test_transcribe_uses_local_image_text_schema_and_effort(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "thought.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            thread = FakeThread(text=json.dumps({"text": "先通分"}, ensure_ascii=False))
            sdk = FakeSDK(thread)
            client = CodexClient(sdk=sdk, reasoning_effort="high")
            self.addCleanup(client.close)

            with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
                result = client.transcribe_image(str(image_path), "只转录思路")

        self.assertEqual(result, "先通分")
        turn_input, turn_kwargs = thread.run_calls[0]
        self.assertIsInstance(turn_input[0], FakeTextInput)
        self.assertIsInstance(turn_input[1], FakeLocalImageInput)
        self.assertEqual(turn_input[1].path, str(image_path.resolve()))
        self.assertEqual(turn_kwargs["output_schema"], TEXT_OUTPUT_SCHEMA)
        self.assertEqual(turn_kwargs["effort"], "high")

    def test_transcribe_empty_response_is_validation_error_without_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "thought.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            thread = FakeThread(text='{"text":"  "}')
            sdk = FakeSDK(thread)
            client = CodexClient(sdk=sdk, max_retries=3)
            self.addCleanup(client.close)

            with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
                with self.assertRaisesRegex(OcrError, "没有识别出当前字段"):
                    client.transcribe_image(str(image_path), "只转录思路")

        self.assertEqual(len(thread.run_calls), 1)

    def test_transcribe_eof_interrupts_active_turn(self):
        class EofTurn(FakeTurn):
            def run(self):
                raise EOFError

        class Thread(FakeThread):
            def turn(self, prompt, **kwargs):
                self.run_calls.append((prompt, kwargs))
                self.turn_handle = EofTurn([])
                return self.turn_handle

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "thought.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            thread = Thread()
            sdk = FakeSDK(thread)
            client = CodexClient(sdk=sdk)
            self.addCleanup(client.close)

            with patch("errgrind.llm.codex._load_sdk", return_value=self.SDK_TYPES):
                with self.assertRaises(EOFError):
                    client.transcribe_image(str(image_path), "只转录思路")

        self.assertTrue(thread.turn_handle.interrupted)

    def test_auth_helpers_delegate_to_sdk(self):
        sdk = FakeSDK(FakeThread())
        client = CodexClient(sdk=sdk)
        self.assertEqual(client.account(), {"logged_in": True})
        self.assertEqual(client.login_chatgpt_device_code(), "device-handle")
        client.close()
        client.close()
        self.assertTrue(sdk.closed)

    def test_app_wiring_does_not_require_api_key(self):
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def account(self, **_kwargs):
                return {"logged_in": True}

            def close(self):
                pass

        original = app.PROVIDERS["codex"]
        with patch.dict(app.PROVIDERS, {"codex": (original[0], FakeClient, original[2])}):
            client = app._make_llm({"provider": "codex", "model": "gpt-5"})
        self.assertEqual(
            client.kwargs,
            {"model": "gpt-5", "reasoning_effort": None},
        )

    def test_minimal_codex_config_gets_codex_model_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(json.dumps({"provider": "codex"}), encoding="utf-8")
            with patch("errgrind.config.CONFIG_PATH", str(path)):
                self.assertEqual(load()["model"], DEFAULT_CODEX_MODEL)

    def test_ensure_config_removes_empty_codex_api_fields(self):
        cfg = {
            "provider": "codex",
            "model": DEFAULT_CODEX_MODEL,
            "api_key": "",
            "base_url": "",
        }
        with patch("errgrind.cli.app.load_config", return_value=cfg), \
             patch("errgrind.cli.app.save_config") as save, \
             patch("errgrind.cli.app._configure_codex_login"):
            result = app._ensure_config()

        self.assertNotIn("api_key", result)
        self.assertNotIn("base_url", result)
        save.assert_called_once_with(result)


if __name__ == "__main__":
    unittest.main()
