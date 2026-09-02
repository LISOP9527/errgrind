import os
import json
import time
from collections.abc import Callable
from typing import Optional

from openai import OpenAI

from .ocr import load_image, parse_ocr_result


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
GO_BASE_URL = "https://opencode.ai/zen/go/v1"


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = "deepseek-chat",
        max_retries: int = 3,
    ):
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMError("DEEPSEEK_API_KEY 未设置")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.max_retries = max_retries

    def chat(self, messages: list[dict], **kwargs) -> str:
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **kwargs,
                )
                return resp.choices[0].message.content
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)
                else:
                    raise LLMError(f"API 调用失败: {e}")

    def stream_chat(
        self,
        messages: list[dict],
        on_token: Callable[[str], None],
        **kwargs,
    ) -> str:
        """Stream one response without retrying after text has reached the UI."""
        for attempt in range(self.max_retries):
            collected = []
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    stream=True,
                    **kwargs,
                )
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        collected.append(token)
                        on_token(token)
                return "".join(collected)
            except Exception as e:
                if collected:
                    raise LLMError(f"流式 API 调用中断: {e}") from e
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)
                else:
                    raise LLMError(f"API 调用失败: {e}") from e

    def chat_json(self, messages: list[dict], **kwargs) -> dict:
        # Some OpenAI-compatible providers only support json_object, not the
        # newer json_schema mode.  The shared command layer still validates
        # the returned fields locally.
        kwargs.pop("output_schema", None)
        kwargs.setdefault("temperature", 0.2)
        retry_messages = list(messages)
        last_error = None
        last_text = ""
        for attempt in range(self.max_retries):
            last_text = self.chat(
                retry_messages,
                response_format={"type": "json_object"},
                **kwargs,
            )
            try:
                result = json.loads(last_text)
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
        raise LLMError(f"JSON 解析失败: {last_error}\n原始响应: {last_text}")

    def ocr_image(self, image_path: str, prompt: str) -> dict[str, str]:
        """Use the OpenAI-compatible multimodal message format for OCR."""
        payload = load_image(image_path)
        result = self.chat_json(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请按照系统规则转录这张数学错题图片。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": payload.data_url},
                        },
                    ],
                },
            ]
        )
        return parse_ocr_result(result)
