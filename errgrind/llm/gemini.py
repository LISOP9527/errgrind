import os
import json
import time
from collections.abc import Callable
from typing import Any, Optional

import httpx

from ..config import DEFAULT_GEMINI_MODEL
from .ocr import (
    OCR_OUTPUT_SCHEMA,
    TEXT_OUTPUT_SCHEMA,
    OcrError,
    load_image,
    parse_ocr_result,
    parse_text_result,
)
from .usage import model_attempt, usage_scope


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiError(Exception):
    pass


class GeminiClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 2,
        timeout: int = 30,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY 未设置")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.max_retries = max_retries
        self.timeout = timeout

    @staticmethod
    def _request_body(messages: list[dict], kwargs: dict) -> dict:
        system_messages = []
        contents = []
        for m in messages:
            if m["role"] == "system":
                system_messages.append(m["content"])
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})

        body = {}
        if system_messages:
            body["system_instruction"] = {
                "parts": [{"text": "\n\n".join(system_messages)}]
            }
        body["contents"] = contents
        body.update(kwargs)
        return body

    @staticmethod
    def _response_text(data: dict) -> str:
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as e:
            raise GeminiError(f"Gemini API 响应中没有文本: {data}") from e

    @staticmethod
    def _stream_response_text(data: dict) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts)

    @staticmethod
    def _record_usage(attempt, data: dict) -> None:
        usage = data.get("usageMetadata") if isinstance(data, dict) else None
        if usage is not None:
            attempt.record_usage(usage, "gemini")

    def chat(self, messages: list[dict], **kwargs) -> str:
        body = self._request_body(messages, kwargs)

        url = f"{GEMINI_BASE}/{self.model}:generateContent"

        for attempt in range(self.max_retries):
            try:
                with model_attempt("gemini", self.model) as usage_attempt:
                    resp = httpx.post(
                        url,
                        params={"key": self.api_key},
                        json=body,
                        timeout=self.timeout,
                    )
                    usage_attempt.set_http_status(getattr(resp, "status_code", None))
                    resp.raise_for_status()
                    data = resp.json()
                    self._record_usage(usage_attempt, data)
                    return self._response_text(data)
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    detail = getattr(e, "response", None)
                    detail_text = detail.text if detail is not None else str(e)
                    raise GeminiError(f"Gemini API 调用失败: {detail_text[:500]}")

    def stream_chat(
        self,
        messages: list[dict],
        on_token: Callable[[str], None],
        **kwargs,
    ) -> str:
        body = self._request_body(messages, kwargs)
        url = f"{GEMINI_BASE}/{self.model}:streamGenerateContent"

        for attempt in range(self.max_retries):
            collected = []
            try:
                with model_attempt("gemini", self.model) as usage_attempt:
                    with httpx.stream(
                        "POST",
                        url,
                        params={"key": self.api_key, "alt": "sse"},
                        json=body,
                        timeout=self.timeout,
                    ) as resp:
                        usage_attempt.set_http_status(getattr(resp, "status_code", None))
                        resp.raise_for_status()
                        event_data = []
                        for line in resp.iter_lines():
                            if not line:
                                if event_data:
                                    data = json.loads("\n".join(event_data))
                                    self._record_usage(usage_attempt, data)
                                    token = self._stream_response_text(data)
                                    if token:
                                        collected.append(token)
                                        on_token(token)
                                    event_data = []
                                continue
                            if line.startswith("data:"):
                                event_data.append(line[5:].lstrip())
                        if event_data:
                            data = json.loads("\n".join(event_data))
                            self._record_usage(usage_attempt, data)
                            token = self._stream_response_text(data)
                            if token:
                                collected.append(token)
                                on_token(token)
                    if not collected:
                        raise GeminiError("Gemini 流式 API 响应中没有文本")
                    return "".join(collected)
            except Exception as e:
                if collected:
                    raise GeminiError(f"Gemini 流式 API 调用中断: {e}") from e
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise GeminiError(f"Gemini API 调用失败: {e}") from e

    def chat_json(self, messages: list[dict], **kwargs) -> dict:
        output_schema = kwargs.pop("output_schema", None)
        json_attempts = max(
            1, int(kwargs.pop("max_json_attempts", self.max_retries))
        )
        config = dict(kwargs.pop("generation_config", {}))
        config["response_mime_type"] = "application/json"
        if output_schema is not None:
            # Our contracts use JSON Schema (including additionalProperties),
            # not the narrower OpenAPI Schema accepted by responseSchema.
            config.pop("response_schema", None)
            config.pop("responseSchema", None)
            config["responseJsonSchema"] = output_schema
        config.setdefault("temperature", 0.2)
        retry_messages = list(messages)
        last_error = None
        last_text = ""
        for attempt in range(json_attempts):
            with usage_scope(json_attempt=attempt + 1):
                last_text = self.chat(retry_messages, generationConfig=config, **kwargs)
            try:
                if not isinstance(last_text, str):
                    raise TypeError("响应内容必须是文本")
                cleaned = last_text.strip().removeprefix("```json").removeprefix("```")
                cleaned = cleaned.removesuffix("```")
                result = json.loads(cleaned)
                if not isinstance(result, dict):
                    raise ValueError("顶层必须是 JSON 对象")
                return result
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                last_error = e
                if attempt < json_attempts - 1:
                    retry_messages = [
                        *messages,
                        {
                            "role": "assistant",
                            "content": (
                                last_text
                                if isinstance(last_text, str)
                                else repr(last_text)
                            ),
                        },
                        {
                            "role": "user",
                            "content": "上次响应不是合法 JSON 对象。只重新输出完整 JSON，不要使用 Markdown 代码块。",
                        },
                    ]
        raise GeminiError(f"JSON 解析失败: {last_error}\n原始响应: {last_text}")

    def _image_json(
        self, image_path: str, prompt: str, output_schema: dict, parser: Callable[[Any], Any]
    ) -> Any:
        payload = load_image(image_path)
        body = {
            "system_instruction": {"parts": [{"text": prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "请按照系统规则转录这张数学错题图片。"},
                        {
                            "inline_data": {
                                "mime_type": payload.mime_type,
                                "data": payload.base64_data,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": output_schema,
                "temperature": 0.0,
            },
        }
        url = f"{GEMINI_BASE}/{self.model}:generateContent"
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with model_attempt("gemini", self.model) as usage_attempt:
                    response = httpx.post(
                        url,
                        params={"key": self.api_key},
                        json=body,
                        timeout=self.timeout,
                    )
                    usage_attempt.set_http_status(getattr(response, "status_code", None))
                    response.raise_for_status()
                    data = response.json()
                    self._record_usage(usage_attempt, data)
                    return parser(self._response_text(data))
            except (OcrError, EOFError):
                raise
            except Exception as exc:
                last = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(2**attempt)
        raise GeminiError(f"Gemini OCR 调用失败: {last}") from last

    def ocr_image(self, image_path: str, prompt: str) -> dict[str, str]:
        """Transcribe an image with Gemini inline image data."""
        return self._image_json(image_path, prompt, OCR_OUTPUT_SCHEMA, parse_ocr_result)

    def transcribe_image(self, image_path: str, prompt: str) -> str:
        """Transcribe one requested image field, including thought-only images."""
        return self._image_json(image_path, prompt, TEXT_OUTPUT_SCHEMA, parse_text_result)
