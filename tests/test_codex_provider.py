import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from errgrind.cli import app
from errgrind.config import DEFAULT_CODEX_MODEL, load
from errgrind.llm.codex import CodexClient, CodexError
from errgrind.llm.codex_transport import CodexTransportError
from errgrind.llm.ocr import OCR_OUTPUT_SCHEMA, TEXT_OUTPUT_SCHEMA, OcrError


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = iter(responses or [["hello"]])
        self.calls = []
        self.closed_streams = 0
        self.closed = False

    def stream(self, body, token, account):
        self.calls.append((body, token, account))
        try:
            for item in next(self.responses):
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            self.closed_streams += 1

    def close(self):
        self.closed = True


class CodexProviderTests(unittest.TestCase):
    def make(self, responses=None, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        auth_file = Path(temp.name) / "auth.json"
        auth_file.write_text(json.dumps({"tokens": {
            "access_token": "test-access", "account_id": "test-account",
            "refresh_token": "refresh-never-forwarded",
        }}))
        transport = FakeTransport(responses)
        client = CodexClient(transport=transport, auth_file=auth_file, **kwargs)
        self.addCleanup(client.close)
        return client, transport

    def test_native_roles_and_only_caller_context_without_sdk(self):
        client, transport = self.make()
        messages = [
            {"role": "system", "content": "诊断规则"},
            {"role": "user", "content": "过去的回答"},
            {"role": "assistant", "content": "之前的问题"},
            {"role": "user", "content": "新证据"},
        ]
        with patch("errgrind.llm.codex._load_sdk") as sdk:
            self.assertEqual(client.chat(messages), "hello")
        sdk.assert_not_called()
        body, token, account = transport.calls[0]
        self.assertEqual(body["instructions"], "诊断规则")
        self.assertEqual([m["role"] for m in body["input"]], ["user", "assistant", "user"])
        self.assertEqual(body["input"][1]["content"], [{"type": "output_text", "text": "之前的问题"}])
        self.assertEqual(set(body), {"model", "instructions", "input", "store", "stream"})
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertNotIn("refresh-never-forwarded", json.dumps(transport.calls))
        self.assertEqual((token, account), ("test-access", "test-account"))
        self.assertIsNone(client._workspace)

    def test_empty_system_does_not_fall_back_to_codex_prompt(self):
        client, transport = self.make()
        client.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(transport.calls[0][0]["instructions"], "")

    def test_effort_reaches_chat_stream_and_json(self):
        for effort in (None, "high", "ultra", "future-effort"):
            with self.subTest(effort=effort):
                client, transport = self.make([["hello"], ["hello"], ['{"ok":true}']], reasoning_effort=effort)
                client.chat([])
                client.stream_chat([], lambda _: None)
                client.chat_json([])
                for body, _, _ in transport.calls:
                    if effort is None:
                        self.assertNotIn("reasoning", body)
                    else:
                        self.assertEqual(body["reasoning"], {"effort": effort})

    def test_per_call_effort_overrides_configured_effort(self):
        client, transport = self.make([["a"], ["b"]], reasoning_effort="high")
        client.chat([], effort="low")
        client.stream_chat([], lambda _: None, effort=None)
        self.assertEqual(transport.calls[0][0]["reasoning"], {"effort": "low"})
        self.assertNotIn("reasoning", transport.calls[1][0])

    def test_untrusted_kwargs_cannot_enable_tools_or_server_history(self):
        client, transport = self.make()
        for extra in ({"tools": []}, {"previous_response_id": "secret"}, {"instructions": "override"}, {"store": True}):
            with self.assertRaises(CodexError):
                client.chat([], **extra)
        self.assertEqual(transport.calls, [])

    def test_json_repairs_invalid_response_with_native_messages_and_schema(self):
        client, transport = self.make([["bad"], ['{"ok":true}']], max_retries=2)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        self.assertEqual(client.chat_json([], output_schema=schema), {"ok": True})
        self.assertEqual(transport.calls[0][0]["text"]["format"]["schema"], schema)
        self.assertEqual(transport.calls[1][0]["input"][0]["role"], "assistant")
        self.assertEqual(transport.calls[1][0]["input"][0]["content"][0]["text"], "bad")

    def test_generic_json_does_not_send_wildcard_schema(self):
        client, transport = self.make([['{"ok":true}']])
        self.assertEqual(client.chat_json([]), {"ok": True})
        self.assertNotIn("text", transport.calls[0][0])

    def test_retry_only_transient_before_output(self):
        transient = CodexTransportError("busy", retryable=True, status_code=429)
        client, transport = self.make([[transient], ["ok"]])
        with patch("errgrind.llm.codex.time.sleep"):
            self.assertEqual(client.chat([]), "ok")
        self.assertEqual(len(transport.calls), 2)
        client, transport = self.make([[CodexTransportError("bad", retryable=False, status_code=400)]])
        with self.assertRaises(CodexError):
            client.chat([])
        self.assertEqual(len(transport.calls), 1)

    def test_stream_does_not_replay_after_output(self):
        client, transport = self.make([["partial", CodexTransportError("broken", retryable=True)]])
        tokens = []
        with self.assertRaises(CodexError):
            client.stream_chat([], tokens.append)
        self.assertEqual(tokens, ["partial"])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.closed_streams, 1)

    def test_interrupt_and_callback_failure_close_stream_without_retry(self):
        for error in (KeyboardInterrupt(), EOFError(), RuntimeError("callback")):
            client, transport = self.make([["first", "second"]])
            def callback(_):
                raise error
            with self.assertRaises(type(error)):
                client.stream_chat([], callback)
            self.assertEqual(transport.closed_streams, 1)
            self.assertEqual(len(transport.calls), 1)

    def test_401_refreshes_once_using_sdk_and_rereads_file(self):
        unauthorized = CodexTransportError("unauthorized", retryable=False, status_code=401)
        client, transport = self.make([[unauthorized], ["ok"]])
        sdk = Mock()
        def refresh(**kwargs):
            self.assertEqual(kwargs, {"refresh_token": True})
            client._auth_file.write_text(json.dumps({"tokens": {"access_token": "renewed", "account_id": "account"}}))
        sdk.account.side_effect = refresh
        client._sdk = sdk
        self.assertEqual(client.chat([]), "ok")
        self.assertEqual(transport.calls[1][1], "renewed")
        sdk.account.assert_called_once_with(refresh_token=True)
        sdk.thread_start.assert_not_called()
        client, transport = self.make([[unauthorized], [unauthorized]])
        client._sdk = Mock()
        with self.assertRaises(CodexError):
            client.chat([])
        self.assertEqual(len(transport.calls), 2)
        client._sdk.account.assert_called_once()

    def test_expired_token_refreshes_before_request(self):
        client, transport = self.make()
        claim = base64.urlsafe_b64encode(json.dumps({"exp": time.time() - 10}).encode()).decode().rstrip("=")
        client._auth_file.write_text(json.dumps({"tokens": {"access_token": "x." + claim + ".x", "account_id": "account"}}))
        sdk = Mock()
        sdk.account.side_effect = lambda **_: client._auth_file.write_text(json.dumps({"tokens": {"access_token": "fresh", "account_id": "account"}}))
        client._sdk = sdk
        client.chat([])
        self.assertEqual(transport.calls[0][1], "fresh")
        sdk.account.assert_called_once_with(refresh_token=True)

    def test_missing_malformed_credentials_and_refresh_errors_are_safe(self):
        for contents in ("secret-invalid-json", "[]", '{"tokens":null}', '{"tokens":[]}'):
            client, transport = self.make()
            client._auth_file.write_text(contents)
            with self.assertRaises(CodexError) as error:
                client.chat([])
            self.assertNotIn("secret", str(error.exception))
            self.assertFalse(transport.calls)
        client, transport = self.make([[CodexTransportError("expired", retryable=False, status_code=401)]])
        client._sdk = Mock()
        client._sdk.account.side_effect = RuntimeError("refresh-token-secret")
        with self.assertRaises(CodexError) as error:
            client.chat([])
        self.assertNotIn("secret", str(error.exception))

    def test_ocr_and_transcribe_send_image_bytes_not_paths(self):
        for result, schema, method in [
            ({"question": "求x", "user_thoughts": "移项", "reference_answer": "2"}, OCR_OUTPUT_SCHEMA, "ocr_image"),
            ({"text": "先通分"}, TEXT_OUTPUT_SCHEMA, "transcribe_image"),
        ]:
            client, transport = self.make([[json.dumps(result)]], reasoning_effort="high")
            image_path = client._auth_file.parent / "problem.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            parsed = getattr(client, method)(str(image_path), "OCR prompt")
            self.assertEqual(parsed, result if method == "ocr_image" else result["text"])
            body = transport.calls[0][0]
            self.assertEqual(body["instructions"], "OCR prompt")
            self.assertTrue(body["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,"))
            self.assertNotIn(str(image_path), json.dumps(body))
            self.assertEqual(body["text"]["format"]["schema"], schema)
            self.assertEqual(body["reasoning"], {"effort": "high"})

    def test_transcribe_empty_result_is_validation_error_without_retry(self):
        client, transport = self.make([['{"text":"  "}']])
        image_path = client._auth_file.parent / "problem.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        with self.assertRaises(OcrError):
            client.transcribe_image(str(image_path), "OCR")
        self.assertEqual(len(transport.calls), 1)

    def test_auth_helpers_still_delegate_without_generation(self):
        sdk = Mock()
        client, transport = self.make(sdk=sdk)
        self.assertEqual(client.account(), sdk.account.return_value)
        self.assertEqual(client.login_chatgpt(), sdk.login_chatgpt.return_value)
        self.assertEqual(client.login_chatgpt_device_code(), sdk.login_chatgpt_device_code.return_value)
        self.assertEqual(transport.calls, [])
        client.close()
        client.close()
        sdk.close.assert_called_once()
        self.assertTrue(transport.closed)

    def test_models_are_live_filtered_sorted_and_never_supply_prompts(self):
        client, transport = self.make()
        transport.models = Mock(return_value={"models": [
            {"slug": "older", "visibility": "list", "priority": 5},
            {"slug": "hidden", "visibility": "hide", "priority": -1},
            {"slug": "gpt-6-astra", "visibility": "list", "priority": 0,
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "ultra"}, {"effort": "low"}],
             "default_reasoning_level": "low", "base_instructions": "SECRET_CODEX_PROMPT",
             "model_messages": {"instructions": "SECRET_CODEX_PROMPT"}},
            {"slug": "older", "visibility": "list", "priority": 6},
            {"slug": "", "visibility": "list"}, None,
        ]})
        with patch("errgrind.llm.codex._load_sdk") as sdk:
            entries = client.models()
            hidden = client.models(include_hidden=True)
            client.chat([{"role": "system", "content": "ErrGrind only"}])
        sdk.assert_not_called()
        self.assertEqual([m["model"] for m in entries], ["gpt-6-astra", "older"])
        self.assertEqual([m["model"] for m in hidden], ["hidden", "gpt-6-astra", "older"])
        self.assertEqual([m["model"] for m in hidden if m["is_default"]], ["gpt-6-astra"])
        self.assertEqual(entries[0]["supported_reasoning_efforts"], ["low", "ultra"])
        self.assertEqual(entries[0]["default_reasoning_effort"], "low")
        self.assertNotIn("SECRET_CODEX_PROMPT", json.dumps(entries + hidden + transport.calls))
        self.assertEqual(transport.calls[0][0]["instructions"], "ErrGrind only")
        self.assertEqual(transport.models.call_count, 2)

    def test_model_catalog_refresh_retry_and_permanent_failure(self):
        unauthorized = CodexTransportError("expired", status_code=401, retryable=False)
        transient = CodexTransportError("busy", status_code=503, retryable=True)
        client, transport = self.make()
        transport.models = Mock(side_effect=[unauthorized, transient, {"models": []}])
        client._sdk = Mock()
        def refresh(**_):
            client._auth_file.write_text(json.dumps({"tokens": {"access_token": "new", "account_id": "account"}}))
        client._sdk.account.side_effect = refresh
        with patch("errgrind.llm.codex.time.sleep") as sleep:
            self.assertEqual(client.models(), [])
        self.assertEqual(transport.models.call_count, 3)
        self.assertEqual(transport.models.call_args.args, ("new", "account"))
        client._sdk.account.assert_called_once_with(refresh_token=True)
        client._sdk.models.assert_not_called()
        sleep.assert_called_once()

        for errors in ([unauthorized, unauthorized], [CodexTransportError("bad", status_code=400, retryable=False)]):
            client, transport = self.make(sdk=Mock())
            transport.models = Mock(side_effect=errors)
            with self.assertRaises(CodexError):
                client.models()
            self.assertEqual(transport.models.call_count, len(errors))

    def test_direct_catalog_reaches_cli_and_effort_picker(self):
        client, transport = self.make()
        transport.models = Mock(return_value={"models": [
            {"slug": "gpt-6-astra", "visibility": "list", "priority": 0,
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
             "default_reasoning_level": "low"},
        ]})
        with patch.object(app, "_CODEX_MODEL_METADATA", {}), patch.object(app, "CodexClient", return_value=client):
            self.assertEqual(app._fetch_codex_models(), (["gpt-6-astra"], "gpt-6-astra"))
            self.assertEqual(app._fetch_codex_model_metadata("gpt-6-astra"), {
                "supported_reasoning_efforts": ["low", "high"], "default_reasoning_effort": "low",
            })

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
