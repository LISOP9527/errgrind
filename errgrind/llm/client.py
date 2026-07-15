import os
import json
import time
from typing import Optional

from openai import OpenAI


DEEPSEEK_BASE_URL = "https://api.deepseek.com"


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
                    time.sleep(2 ** attempt)
                else:
                    raise LLMError(f"API 调用失败: {e}")

    def chat_json(self, messages: list[dict], **kwargs) -> dict:
        text = self.chat(messages, response_format={"type": "json_object"}, **kwargs)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON 解析失败: {e}\n原始响应: {text}")
