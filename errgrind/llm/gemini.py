import os
import json
import time
from collections.abc import Callable
from typing import Optional

import httpx

from ..config import DEFAULT_GEMINI_MODEL
from .ocr import OCR_OUTPUT_SCHEMA, load_image, parse_ocr_result


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
        system_instruction = None
        contents = []
        for m in messages:
            if m["role"] == "system":
                system_instruction = {"parts": [{"text": m["content"]}]}
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})

        body = {}
        if system_instruction:
            body["system_instruction"] = system_instruction
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

    def chat(self, messages: list[dict], **kwargs) -> str:
        body = self._request_body(messages, kwargs)

        url = f"{GEMINI_BASE}/{self.model}:generateContent"

        for attempt in range(self.max_retries):
            try:
                resp = httpx.post(
                    url,
                    params={"key": self.api_key},
                    json=body,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return self._response_text(resp.json())
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
                with httpx.stream(
                    "POST",
                    url,
                    params={"key": self.api_key, "alt": "sse"},
                    json=body,
                    timeout=self.timeout,
                ) as resp:
                    resp.raise_for_status()
                    event_data = []
                    for line in resp.iter_lines():
                        if not line:
                            if event_data:
                                token = self._stream_response_text(json.loads("\n".join(event_data)))
                                if token:
                                    collected.append(token)
                                    on_token(token)
                                event_data = []
                            continue
                        if line.startswith("data:"):
                            event_data.append(line[5:].lstrip())
                    if event_data:
                        token = self._stream_response_text(json.loads("\n".join(event_data)))
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
        config = dict(kwargs.pop("generation_config", {}))
        config["response_mime_type"] = "application/json"
        if output_schema is not None:
            config["response_schema"] = output_schema
        config.setdefault("temperature", 0.2)
        retry_messages = list(messages)
        last_error = None
        last_text = ""
        for attempt in range(self.max_retries):
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
                if attempt < self.max_retries - 1:
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

    def ocr_image(self, image_path: str, prompt: str) -> dict[str, str]:
        """Transcribe an image with Gemini inline image data."""
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
                "response_schema": OCR_OUTPUT_SCHEMA,
                "temperature": 0.0,
            },
        }
        url = f"{GEMINI_BASE}/{self.model}:generateContent"
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = httpx.post(
                    url,
                    params={"key": self.api_key},
                    json=body,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return parse_ocr_result(self._response_text(response.json()))
            except Exception as exc:
                last = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(2**attempt)
        raise GeminiError(f"Gemini OCR 调用失败: {last}") from last
