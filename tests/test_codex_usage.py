import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from errgrind.llm.codex import CodexClient
from errgrind.llm.codex_transport import CodexResponsesTransport, CodexTransportError


def _sse(*events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


class _Transport:
    def __init__(self, responses):
        self.responses = iter(responses)

    def stream(self, body, token, account):
        response = next(self.responses)
        for item in response:
            if isinstance(item, BaseException):
                raise item
            yield item

    def close(self):
        pass


class CodexUsageIntegrationTests(unittest.TestCase):
    def test_sse_usage_and_http_status_are_forwarded_without_content(self):
        usage = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}

        def handler(_request):
            return httpx.Response(200, text=_sse(
                {"type": "response.output_text.delta", "delta": "visible"},
                {"type": "response.completed", "response": {
                    "status": "completed", "usage": usage,
                    "output": [], "secret": "do-not-log",
                }},
            ))

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        transport = CodexResponsesTransport(client)
        with patch("errgrind.llm.codex_transport.record_usage") as record, patch(
            "errgrind.llm.codex_transport.record_http_status"
        ) as status:
            self.assertEqual(list(transport.stream({}, "token", "account")), ["visible"])
        status.assert_called_once_with(200)
        record.assert_called_once_with(usage, "codex")
        self.assertNotIn("visible", repr(record.call_args))
        self.assertNotIn("secret", repr(record.call_args))

    def test_incomplete_usage_is_recorded_before_failure(self):
        usage = {"input_tokens": 4, "output_tokens": 0, "total_tokens": 4}
        transport = CodexResponsesTransport(httpx.Client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, text=_sse(
                {"type": "response.incomplete", "response": {"usage": usage}},
            ))
        )))
        self.addCleanup(transport.close)
        with patch("errgrind.llm.codex_transport.record_usage") as record:
            with self.assertRaises(CodexTransportError):
                list(transport.stream({}, "token", "account"))
        record.assert_called_once_with(usage, "codex")

    def test_each_retry_gets_one_model_attempt_and_json_scope_is_one_based(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        auth = Path(temp.name) / "auth.json"
        auth.write_text(json.dumps({"tokens": {"access_token": "token", "account_id": "account"}}))
        error = CodexTransportError("busy", retryable=True, status_code=429)
        transport = _Transport([[error], ['{"ok": true}']])
        client = CodexClient(
            transport=transport, auth_file=auth, max_retries=2, reasoning_effort="high"
        )
        self.addCleanup(client.close)
        attempts = []
        scopes = []

        @contextmanager
        def attempt(**kwargs):
            attempts.append(kwargs)
            yield

        @contextmanager
        def scope(**kwargs):
            scopes.append(kwargs)
            yield

        with patch("errgrind.llm.codex.model_attempt", attempt), patch(
            "errgrind.llm.codex.usage_scope", scope
        ), patch("errgrind.llm.codex.time.sleep"):
            self.assertEqual(client.chat_json([], max_json_attempts=1), {"ok": True})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            attempts[0],
            {"provider": "codex", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
        )
        self.assertEqual(scopes, [{"json_attempt": 1}])


if __name__ == "__main__":
    unittest.main()
