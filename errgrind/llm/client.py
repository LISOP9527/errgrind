import os
import json
import time
from collections.abc import Callable
from typing import Optional
from urllib.parse import urlsplit

from .ocr import TEXT_OUTPUT_SCHEMA, OCR_OUTPUT_SCHEMA, load_image, parse_ocr_result, parse_text_result
from .usage import model_attempt, usage_scope


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
        # 只有兼容 OpenAI 的提供商需要此 SDK，避免拖慢其他提供商的启动。
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.max_retries = max_retries
        self.provider = self._provider_for_base_url(base_url)

    @staticmethod
    def _provider_for_base_url(base_url: str) -> str:
        hostname = urlsplit(base_url).hostname
        if hostname == "api.deepseek.com":
            return "deepseek"
        if hostname == "opencode.ai":
            return "opencode"
        return "openai_compatible"

    @staticmethod
    def _usage_dict(value):
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        for method in ("model_dump", "dict"):
            converter = getattr(value, method, None)
            if callable(converter):
                try:
                    result = converter()
                except Exception:
                    continue
                if isinstance(result, dict):
                    return result
        try:
            return vars(value)
        except TypeError:
            return None

    def chat(self, messages: list[dict], **kwargs) -> str:
        for attempt in range(self.max_retries):
            try:
                with model_attempt(
                    self.provider,
                    self.model,
                    reasoning_effort=kwargs.get("reasoning_effort"),
                    sdk_max_retries=getattr(self.client, "max_retries", None),
                ) as usage:
                    resp = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        **kwargs,
                    )
                    usage.record_usage(self._usage_dict(getattr(resp, "usage", None)), "openai")
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
                with model_attempt(
                    self.provider,
                    self.model,
                    reasoning_effort=kwargs.get("reasoning_effort"),
                    sdk_max_retries=getattr(self.client, "max_retries", None),
                ) as usage:
                    stream_kwargs = dict(kwargs)
                    stream_options = dict(stream_kwargs.get("stream_options") or {})
                    stream_options["include_usage"] = True
                    stream_kwargs["stream_options"] = stream_options
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        stream=True,
                        **stream_kwargs,
                    )
                    for chunk in response:
                        chunk_usage = self._usage_dict(getattr(chunk, "usage", None))
                        if chunk_usage is not None:
                            usage.record_usage(chunk_usage, "openai")
                        choices = getattr(chunk, "choices", None) or []
                        if choices and getattr(choices[0], "delta", None) and getattr(choices[0].delta, "content", None):
                            token = choices[0].delta.content
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
        json_attempts = max(
            1, int(kwargs.pop("max_json_attempts", self.max_retries))
        )
        kwargs.setdefault("temperature", 0.2)
        retry_messages = list(messages)
        last_error = None
        last_text = ""
        for attempt in range(json_attempts):
            with usage_scope(json_attempt=attempt + 1):
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
        raise LLMError(f"JSON 解析失败: {last_error}\n原始响应: {last_text}")

    def _image_json(self, image_path: str, prompt: str, output_schema: dict) -> dict:
        payload = load_image(image_path)
        return self.chat_json(
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
            ],
            output_schema=output_schema,
        )

    def ocr_image(self, image_path: str, prompt: str) -> dict[str, str]:
        """Use the OpenAI-compatible multimodal message format for OCR."""
        result = self._image_json(image_path, prompt, OCR_OUTPUT_SCHEMA)
        return parse_ocr_result(result)

    def transcribe_image(self, image_path: str, prompt: str) -> str:
        """Transcribe one requested image field, including thought-only images."""
        return parse_text_result(
            self._image_json(image_path, prompt, TEXT_OUTPUT_SCHEMA)
        )
