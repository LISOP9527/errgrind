"""Small, deliberately constrained HTTP transport for Codex Responses SSE."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx


CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


class CodexTransportError(Exception):
    """A transport or protocol failure, without response contents or secrets."""

    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def _text_from_output(value: Any) -> str:
    """Extract assistant message output_text while excluding reasoning/analysis."""
    if not isinstance(value, dict):
        return ""
    item_type = value.get("type", "")
    if isinstance(item_type, str) and (
        item_type.endswith("_call") or item_type.endswith("_call_output")
    ):
        raise CodexTransportError("Codex 响应包含工具调用，已安全停止", retryable=False)
    if value.get("channel") not in {None, "final"}:
        return ""
    if item_type not in {None, "message"}:
        return ""
    if value.get("role", "assistant") != "assistant":
        return ""
    chunks: list[str] = []
    content = value.get("content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type", "")
            if kind in {"refusal", "output_refusal"}:
                raise CodexTransportError("Codex 响应被拒绝，已安全停止", retryable=False)
            if kind in {"output_text", "text"} and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks)


def _fallback_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    output = response.get("output", [])
    if not isinstance(output, list):
        return ""
    return "".join(_text_from_output(item) for item in output)


class CodexResponsesTransport:
    """Synchronous, fixed-endpoint Responses SSE transport.

    The caller owns retry policy and supplies the complete request body. Tokens
    and account ids are used only to construct an in-memory request header.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0))
        self._owns_client = client is None
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_client:
                self._client.close()

    def __enter__(self) -> "CodexResponsesTransport":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def stream(self, body: dict, access_token: str, account_id: str) -> Iterator[str]:
        if self._closed:
            raise CodexTransportError("Codex transport 已关闭", retryable=False)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "originator": "errgrind",
            "OpenAI-Beta": "responses=experimental",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        try:
            response_context = self._client.stream(
                "POST", CODEX_RESPONSES_URL, headers=headers, json=body, follow_redirects=False
            )
            with response_context as response:
                if response.status_code != 200:
                    retryable = response.status_code in {408, 429} or response.status_code >= 500
                    raise CodexTransportError(
                        f"Codex 请求失败（HTTP {response.status_code}）",
                        retryable=retryable,
                        status_code=response.status_code,
                    )
                yield from self._parse(response.iter_lines())
        except CodexTransportError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            raise CodexTransportError("Codex 网络连接失败", retryable=True) from None
        except httpx.HTTPError:
            raise CodexTransportError("Codex HTTP 请求失败", retryable=False) from None

    @staticmethod
    def _parse(lines: Iterator[str]) -> Iterator[str]:
        data: list[str] = []
        emitted = False
        terminal = False
        completed_text = ""
        item_channels: dict[str, str] = {}
        index_channels: dict[int, str] = {}

        def process(raw: str) -> Iterator[str]:
            nonlocal emitted, terminal, completed_text
            if not raw:
                return
            if raw == "[DONE]":
                return
            try:
                event = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                raise CodexTransportError("Codex SSE 数据格式无效", retryable=False) from None
            if not isinstance(event, dict):
                raise CodexTransportError("Codex SSE 事件格式无效", retryable=False)
            kind = event.get("type", "")
            if (
                kind in {"response.failed", "response.incomplete"}
                or (
                    isinstance(kind, str)
                    and (
                        kind == "error"
                        or kind.endswith(".error")
                        or kind.endswith(".refusal.delta")
                        or ("call" in kind and kind.endswith(".delta"))
                    )
                )
            ):
                raise CodexTransportError("Codex 响应未完成", retryable=False)
            if kind in {"response.output_item.added", "response.output_item.done"}:
                item = event.get("item")
                if isinstance(item, dict):
                    item_id = item.get("id")
                    channel = item.get("channel", event.get("channel", "final"))
                    if isinstance(item_id, str) and isinstance(channel, str):
                        item_channels[item_id] = channel
                    if isinstance(event.get("output_index"), int) and isinstance(channel, str):
                        index_channels[event["output_index"]] = channel
                if isinstance(item, dict) and isinstance(item.get("type"), str) and (
                    item["type"].endswith("_call") or item["type"].endswith("_call_output")
                ):
                    raise CodexTransportError("Codex 响应包含工具调用，已安全停止", retryable=False)
            if kind in {"response.output_text.delta", "output_text.delta"}:
                channel = event.get("channel")
                if channel is None and isinstance(event.get("item_id"), str):
                    channel = item_channels.get(event["item_id"])
                if channel is None and isinstance(event.get("output_index"), int):
                    channel = index_channels.get(event["output_index"])
                channel = channel or "final"
                if channel != "final":
                    return
                delta = event.get("delta", "")
                if isinstance(delta, str):
                    emitted = emitted or bool(delta)
                    if delta:
                        yield delta
                return
            if kind in {"response.completed", "response.done"}:
                response = event.get("response", event)
                if not isinstance(response, dict) or response.get("status") != "completed":
                    raise CodexTransportError("Codex 响应未完成", retryable=False)
                terminal = True
                completed_text = _fallback_text(response)

        for line in lines:
            if line.startswith(":"):
                continue
            if line == "":
                if data:
                    yield from process("\n".join(data))
                    data = []
                    if terminal:
                        break
                continue
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        if data:
            yield from process("\n".join(data))
        if not terminal:
            raise CodexTransportError("Codex SSE 流在完成事件前结束", retryable=False)
        if not emitted and completed_text:
            yield completed_text
        if not emitted and not completed_text:
            raise CodexTransportError("Codex 响应没有可见文本", retryable=False)
