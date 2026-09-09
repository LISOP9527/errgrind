import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from errgrind.llm.client import LLMClient
from errgrind.llm.gemini import GeminiClient, GeminiError


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class ProviderUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log = Path(self.temp_dir.name) / "usage.jsonl"
        self.log_path = patch("errgrind.llm.usage.log_path", return_value=self.log)
        self.log_path.start()

    def tearDown(self):
        self.log_path.stop()
        self.temp_dir.cleanup()

    def test_openai_success_normalizes_usage_and_provider(self):
        client = object.__new__(LLMClient)
        client.model, client.max_retries = "model-x", 1
        client.provider = "openai_compatible"
        client.client = SimpleNamespace(
            max_retries=0,
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=Mock(return_value=SimpleNamespace(
                        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                    ))
                )
            ),
        )

        self.assertEqual(client.chat([]), "ok")
        row = _rows(self.log)[0]
        self.assertEqual(row["provider"], "openai_compatible")
        self.assertEqual(row["input_tokens"], 3)
        self.assertEqual(row["output_tokens"], 2)
        self.assertEqual(row["total_tokens"], 5)

    def test_openai_stream_captures_terminal_usage_without_injecting_options(self):
        client = object.__new__(LLMClient)
        client.model, client.max_retries, client.provider = "model-x", 1, "deepseek"
        create = Mock(return_value=iter([
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"))], usage=None),
            SimpleNamespace(choices=[], usage={"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}),
        ]))
        client.client = SimpleNamespace(max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        self.assertEqual(client.stream_chat([], lambda _: None), "hi")
        self.assertNotIn("stream_options", create.call_args.kwargs)
        self.assertEqual(_rows(self.log)[0]["total_tokens"], 5)

    def test_openai_stream_preserves_explicit_options_and_missing_usage_is_unknown(self):
        client = object.__new__(LLMClient)
        client.model, client.max_retries, client.provider = "model-x", 1, "openai_compatible"
        create = Mock(return_value=iter([
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))], usage=None),
        ]))
        client.client = SimpleNamespace(max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        options = {"include_usage": False}

        self.assertEqual(
            client.stream_chat([], lambda _: None, stream_options=options),
            "ok",
        )
        self.assertEqual(create.call_args.kwargs["stream_options"], options)
        row = _rows(self.log)[0]
        self.assertFalse(row["usage_reported"])
        self.assertIsNone(row["total_tokens"])

    def test_gemini_failure_logs_type_without_response_body(self):
        client = GeminiClient(api_key="secret", model="gemini-test", max_retries=1)
        response = Mock()
        response.status_code = 429
        response.raise_for_status.side_effect = RuntimeError("PRIVATE_RESPONSE_BODY")
        with patch("errgrind.llm.gemini.httpx.post", return_value=response):
            with self.assertRaises(GeminiError):
                client.chat([])
        row = _rows(self.log)[0]
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error_type"], "RuntimeError")
        self.assertNotIn("PRIVATE_RESPONSE_BODY", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
