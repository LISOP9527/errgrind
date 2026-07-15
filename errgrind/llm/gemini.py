import os
import json
import time
from typing import Optional

import httpx


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
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        self.max_retries = max_retries
        self.timeout = timeout

    def chat(self, messages: list[dict], **kwargs) -> str:
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
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    detail = getattr(e, "response", None)
                    detail_text = detail.text if detail is not None else str(e)
                    raise GeminiError(f"Gemini API 调用失败: {detail_text[:500]}")

    def chat_json(self, messages: list[dict], **kwargs) -> dict:
        config = kwargs.pop("generation_config", {})
        config["response_mime_type"] = "application/json"
        text = self.chat(messages, generationConfig=config, **kwargs)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise GeminiError(f"JSON 解析失败: {e}\n原始响应: {text}")
