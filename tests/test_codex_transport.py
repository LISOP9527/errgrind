import json
import unittest

import httpx

from errgrind.llm.codex_transport import (
    CODEX_CATALOG_CLIENT_VERSION,
    CODEX_MODELS_URL,
    CODEX_RESPONSES_URL,
    CodexResponsesTransport,
    CodexTransportError,
)


def sse(*events: dict, trailing: bool = True) -> str:
    text = ""
    for event in events:
        text += ": keepalive\r\ndata: " + json.dumps(event) + "\r\n\r\n"
    return text if trailing else text.rstrip("\r\n")


class CodexTransportTests(unittest.TestCase):
    def make(self, payload: str, status: int = 200):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["request"] = request
            return httpx.Response(status, text=payload, headers={"content-type": "text/event-stream"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return CodexResponsesTransport(client), seen

    def test_commentary_by_index_and_analysis_fallback_never_escape(self):
        final = {"type": "message", "channel": "final", "content": [{"type": "output_text", "text": "answer"}]}
        hidden = {"type": "message", "channel": "analysis", "content": [{"type": "output_text", "text": "secret"}]}
        transport, _ = self.make(sse(
            {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "channel": "commentary"}},
            {"type": "response.output_text.delta", "output_index": 0, "delta": "commentary"},
            {"type": "response.completed", "response": {"status": "completed", "output": [hidden, final]}},
        ))
        self.assertEqual(list(transport.stream({}, "t", "a")), ["answer"])

    def test_tool_refusal_and_error_events_never_succeed(self):
        events = [
            {"type": "response.output_item.added", "item": {"type": name}}
            for name in ("function_call", "custom_tool_call", "local_shell_call", "mcp_call")
        ] + [
            {"type": "response.refusal.delta", "delta": "secret"},
            {"type": "error", "message": "token-secret"},
        ]
        for event in events:
            transport, _ = self.make(sse(event))
            with self.assertRaises(CodexTransportError) as error:
                list(transport.stream({}, "token-secret", "account-secret"))
            self.assertNotIn("secret", str(error.exception))

    def test_closing_generator_closes_response_without_reading_more(self):
        class Stream(httpx.SyncByteStream):
            closed = False

            def __iter__(self):
                yield sse({"type": "response.output_text.delta", "delta": "first"}).encode()
                raise AssertionError("must not continue reading after caller cancels")

            def close(self):
                self.closed = True

        stream = Stream()
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)))
        self.addCleanup(client.close)
        transport = CodexResponsesTransport(client)
        iterator = transport.stream({}, "t", "a")
        self.assertEqual(next(iterator), "first")
        iterator.close()
        self.assertTrue(stream.closed)

    def test_headers_body_and_deltas_complete_without_duplicate(self):
        transport, seen = self.make(sse(
            {"type": "response.output_text.delta", "delta": "甲"},
            {"type": "response.output_text.delta", "delta": "乙"},
            {"type": "response.completed", "response": {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "甲乙"}]}]}},
        ))
        self.assertEqual(list(transport.stream({"model": "x", "stream": True}, "tok", "acct")), ["甲", "乙"])
        request = seen["request"]
        self.assertEqual(str(request.url), CODEX_RESPONSES_URL)
        self.assertEqual(request.headers["authorization"], "Bearer tok")
        self.assertEqual(request.headers["chatgpt-account-id"], "acct")
        self.assertEqual(request.headers["originator"], "errgrind")
        self.assertEqual(request.headers["openai-beta"], "responses=experimental")
        self.assertEqual(request.headers["accept"], "text/event-stream")
        self.assertEqual(json.loads(request.content), {"model": "x", "stream": True})

    def test_completed_fallback_and_multiline_data(self):
        event = {"type": "response.completed", "response": {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "答\n案"}]}]}}
        encoded = json.dumps(event, indent=1)
        payload = "".join("data: " + line + "\n" for line in encoded.splitlines()) + "\n"
        transport, _ = self.make(payload)
        self.assertEqual(list(transport.stream({}, "t", "a")), ["答\n案"])

    def test_truncated_failed_and_incomplete(self):
        for payload in [sse({"type": "response.output_text.delta", "delta": "x"}, trailing=False), sse({"type": "response.failed"}), sse({"type": "response.incomplete"})]:
            transport, _ = self.make(payload)
            with self.assertRaises(CodexTransportError) as ctx:
                list(transport.stream({}, "token-secret", "account-secret"))
            self.assertFalse(ctx.exception.retryable)

    def test_http_statuses_and_network(self):
        for status, retryable in [(400, False), (401, False), (408, True), (429, True), (500, True)]:
            transport, _ = self.make("secret body", status)
            with self.assertRaises(CodexTransportError) as ctx:
                list(transport.stream({}, "token-secret", "account-secret"))
            self.assertEqual(ctx.exception.status_code, status)
            self.assertEqual(ctx.exception.retryable, retryable)
            self.assertNotIn("secret", str(ctx.exception))

        def broken(_request):
            raise httpx.ReadTimeout("network secret")
        transport = CodexResponsesTransport(httpx.Client(transport=httpx.MockTransport(broken)))
        with self.assertRaises(CodexTransportError) as ctx:
            list(transport.stream({}, "token-secret", "account-secret"))
        self.assertTrue(ctx.exception.retryable)
        self.assertNotIn("network secret", str(ctx.exception))

    def test_analysis_and_tools_fail_safely(self):
        analysis_delta = {"type": "response.output_text.delta", "channel": "analysis", "delta": "internal"}
        analysis = {"type": "response.completed", "response": {"status": "completed", "output": [{"type": "reasoning", "content": [{"type": "output_text", "text": "internal"}]}]}}
        transport, _ = self.make(sse(analysis_delta, analysis))
        with self.assertRaises(CodexTransportError):
            list(transport.stream({}, "t", "a"))

    def test_item_level_analysis_is_filtered_and_empty_completion_fails(self):
        payload = sse(
            {"type": "response.output_item.added", "item": {"id": "r1", "type": "reasoning", "channel": "analysis"}},
            {"type": "response.output_text.delta", "item_id": "r1", "delta": "hidden"},
            {"type": "response.completed", "response": {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "visible"}]}]}},
        )
        transport, _ = self.make(payload)
        self.assertEqual(list(transport.stream({}, "t", "a")), ["visible"])
        transport, _ = self.make(sse({"type": "response.completed", "response": {"status": "completed", "output": []}}))
        with self.assertRaises(CodexTransportError):
            list(transport.stream({}, "t", "a"))

    def test_redirect_is_not_followed_and_response_closes(self):
        transport, seen = self.make("", 302)
        with self.assertRaises(CodexTransportError) as ctx:
            list(transport.stream({}, "t", "a"))
        self.assertEqual(ctx.exception.status_code, 302)
        self.assertEqual(str(seen["request"].url), CODEX_RESPONSES_URL)

        response_box = {}
        def handler(_request):
            response = httpx.Response(200, text=sse({"type": "response.completed", "response": {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}]}}))
            response_box["response"] = response
            return response
        client = httpx.Client(transport=httpx.MockTransport(handler))
        transport = CodexResponsesTransport(client)
        self.assertEqual(list(transport.stream({}, "t", "a")), ["ok"])
        self.assertTrue(response_box["response"].is_closed)
        tool = {"type": "response.output_item.added", "item": {"type": "function_call", "name": "rm"}}
        transport, _ = self.make(sse(tool))
        with self.assertRaises(CodexTransportError):
            list(transport.stream({}, "t", "a"))

    def test_models_wire_and_validates_payload(self):
        seen = {}
        def handler(request):
            seen["request"] = request
            return httpx.Response(200, json={"models": [{"id": "gpt-5"}], "extra": True})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        transport = CodexResponsesTransport(client)
        self.assertEqual(transport.models("token", "acct"), {"models": [{"id": "gpt-5"}], "extra": True})
        request = seen["request"]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url), CODEX_MODELS_URL + "?client_version=" + CODEX_CATALOG_CLIENT_VERSION)
        self.assertEqual(request.headers["authorization"], "Bearer token")
        self.assertEqual(request.headers["chatgpt-account-id"], "acct")
        self.assertEqual(request.headers["originator"], "errgrind")
        self.assertEqual(request.headers["accept"], "application/json")

    def test_models_status_redirect_json_and_network_fail_safely(self):
        for status, retryable in [(400, False), (401, False), (408, True), (429, True), (500, True), (302, False)]:
            transport, _ = self.make("secret-body", status)
            with self.assertRaises(CodexTransportError) as ctx:
                transport.models("token-secret", "account-secret")
            self.assertEqual(ctx.exception.status_code, status)
            self.assertEqual(ctx.exception.retryable, retryable)
            self.assertNotIn("secret", str(ctx.exception))
        for payload in [{"model": []}, [], "bad"]:
            transport, _ = self.make(json.dumps(payload), 200)
            with self.assertRaises(CodexTransportError) as ctx:
                transport.models("t", "a")
            self.assertFalse(ctx.exception.retryable)
        def broken(_request):
            raise httpx.ReadTimeout("network-secret")
        transport = CodexResponsesTransport(httpx.Client(transport=httpx.MockTransport(broken)))
        with self.assertRaises(CodexTransportError) as ctx:
            transport.models("token-secret", "account-secret")
        self.assertTrue(ctx.exception.retryable)
        self.assertNotIn("network-secret", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
