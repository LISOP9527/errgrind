"""Codex OAuth generation over HTTP; the SDK only manages login and models.

Requests contain ErrGrind instructions and native conversation messages. No
Codex thread is created, so its agent prompt, AGENTS and tools cannot enter the
model request. The official runtime remains the sole writer of OAuth tokens.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from .codex_transport import CodexResponsesTransport, CodexTransportError
from .ocr import (
    OCR_OUTPUT_SCHEMA, TEXT_OUTPUT_SCHEMA, load_image,
    parse_ocr_result, parse_text_result,
)


class CodexError(Exception):
    """User-facing error raised by the Codex provider."""


def _load_sdk() -> tuple[Any, Any]:
    try:
        from openai_codex import Codex, CodexConfig
    except ImportError as exc:
        raise CodexError(
            "Codex 登录和模型目录需要官方 openai-codex SDK；"
            "请在项目目录重新运行 bash install.sh。"
        ) from exc
    return Codex, CodexConfig


class CodexClient:
    """Stateless model calls with SDK-owned OAuth login and refresh."""

    def __init__(
        self, model: str = "gpt-5.6-sol", max_retries: int = 3,
        sdk: Any | None = None, codex_bin: str | None = None,
        reasoning_effort: str | None = None,
        *, transport: Any | None = None, auth_file: str | Path | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_retries = max(1, max_retries)
        self._sdk = sdk
        self._codex_bin = codex_bin
        self._transport = transport
        self._workspace = None
        self._closed = False
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        self._auth_file = Path(auth_file) if auth_file is not None else codex_home / "auth.json"

    def _control(self) -> Any:
        if self._closed:
            raise CodexError("Codex 客户端已关闭")
        if self._sdk is None:
            Codex, CodexConfig = _load_sdk()
            self._workspace = tempfile.TemporaryDirectory(prefix="errgrind-codex-")
            kwargs: dict[str, Any] = {"cwd": self._workspace.name}
            if self._codex_bin:
                kwargs["codex_bin"] = self._codex_bin
            try:
                self._sdk = Codex(config=CodexConfig(**kwargs))
            except BaseException as exc:
                self._workspace.cleanup()
                self._workspace = None
                if isinstance(exc, Exception):
                    raise CodexError("Codex 登录服务启动失败，请检查官方 SDK 安装") from None
                raise
        return self._sdk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for resource in (self._transport, self._sdk):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if self._workspace is not None:
            self._workspace.cleanup()

    def __enter__(self) -> "CodexClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _credentials(self, *, refresh: bool = False) -> tuple[str, str]:
        if refresh:
            try:
                self.account(refresh_token=True)
            except Exception:
                raise CodexError("ChatGPT 登录刷新失败，请重新登录后重试") from None
        try:
            data = json.loads(self._auth_file.read_text(encoding="utf-8"))
            tokens = data.get("tokens") or {}
            access_token = tokens.get("access_token")
            account_id = tokens.get("account_id")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError
            if not isinstance(account_id, str) or not account_id:
                raise ValueError
        except (OSError, ValueError, TypeError, AttributeError):
            # Do not include JSON parser errors: credential file contents may
            # contain secrets. Keyring-only credentials are not exported here.
            raise CodexError(
                "无法读取 Codex 的文件登录凭据。直连需要官方登录生成的 auth.json；"
                "仅使用系统钥匙串的登录暂不支持。"
            ) from None
        # JWT expiry is only a refresh hint, never used to verify identity.
        try:
            part = access_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            expires = float(claims["exp"])
        except (IndexError, ValueError, TypeError, KeyError, AttributeError):
            expires = None
        if not refresh and expires is not None and expires <= time.time() + 60:
            return self._credentials(refresh=True)
        return access_token, account_id

    def _body(self, messages: list[dict], **kwargs: Any) -> dict:
        instructions: list[str] = []
        inputs: list[dict] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                if not isinstance(content, str):
                    raise CodexError("系统指令必须是文本")
                instructions.append(content)
                continue
            if role not in ("user", "assistant", "developer"):
                raise CodexError("Codex 直连只接受 system、developer、user 和 assistant 消息")
            if isinstance(content, str):
                content = [{"type": "output_text" if role == "assistant" else "input_text", "text": content}]
            elif not isinstance(content, list):
                raise CodexError("对话内容必须是文本或图片消息")
            inputs.append({"role": role, "content": content})
        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "instructions": "\n\n".join(instructions),
            "input": inputs, "store": False, "stream": True,
        }
        effort = kwargs.pop("effort", self.reasoning_effort)
        if effort is not None:
            body["reasoning"] = {"effort": effort}
        schema = kwargs.pop("output_schema", None)
        if schema is not None:
            body["text"] = {"format": {
                "type": "json_schema", "name": "errgrind_response",
                "strict": True, "schema": schema,
            }}
        if kwargs:
            # Never pass arbitrary kwargs through to instructions, tools,
            # persistence, previous_response_id or other transport fields.
            raise CodexError("Codex 直连收到不支持的生成参数")
        return body

    def _generate(self, messages: list[dict], on_token: Callable[[str], None] | None = None, **kwargs: Any) -> str:
        if self._closed:
            raise CodexError("Codex 客户端已关闭")
        body = self._body(messages, **kwargs)
        if self._transport is None:
            try:
                self._transport = CodexResponsesTransport()
            except Exception:
                raise CodexError("无法创建 Codex HTTP 客户端，请检查代理配置和依赖") from None
        credentials = self._credentials()
        refreshed = False
        attempt = 0
        while True:
            collected: list[str] = []
            try:
                with closing(self._transport.stream(body, *credentials)) as stream:
                    for token in stream:
                        collected.append(token)
                        if on_token is not None:
                            on_token(token)
                return "".join(collected)
            except CodexTransportError as exc:
                # Once generated text exists, replay could change the answer
                # or repeat visible output, even if no callback was supplied.
                if collected:
                    raise CodexError("Codex 响应中断，已有内容不会自动重放") from None
                if exc.status_code == 401 and not refreshed:
                    credentials = self._credentials(refresh=True)
                    refreshed = True
                    continue
                attempt += 1
                if not exc.retryable or attempt >= self.max_retries:
                    raise CodexError(str(exc)) from None
                time.sleep(2 ** (attempt - 1))

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        return self._generate(messages, **kwargs)

    def stream_chat(self, messages: list[dict], on_token: Callable[[str], None], **kwargs: Any) -> str:
        return self._generate(messages, on_token, **kwargs)

    def _image_json(self, image_path: str, prompt: str, output_schema: dict, parser: Callable[[Any], Any]) -> Any:
        payload = load_image(image_path)
        text = self.chat([
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "input_text", "text": "请按照系统规则转录这张数学错题图片。"},
                {"type": "input_image", "image_url": payload.data_url},
            ]},
        ], output_schema=output_schema)
        return parser(text)

    def ocr_image(self, image_path: str, prompt: str) -> dict[str, str]:
        return self._image_json(image_path, prompt, OCR_OUTPUT_SCHEMA, parse_ocr_result)

    def transcribe_image(self, image_path: str, prompt: str) -> str:
        return self._image_json(image_path, prompt, TEXT_OUTPUT_SCHEMA, parse_text_result)

    def chat_json(self, messages: list[dict], **kwargs: Any) -> dict:
        # Known contracts use Responses text.format; generic JSON retains
        # local parse/repair without inventing a wildcard strict schema.
        output_schema = kwargs.pop("output_schema", None)
        json_attempts = max(
            1, int(kwargs.pop("max_json_attempts", self.max_retries))
        )
        if output_schema is not None:
            kwargs["output_schema"] = output_schema
        retry_messages = list(messages)
        last_text = ""
        for attempt in range(json_attempts):
            last_text = self.chat(retry_messages, **kwargs)
            try:
                result = json.loads(last_text)
                if not isinstance(result, dict):
                    raise ValueError("顶层必须是 JSON 对象")
                return result
            except (json.JSONDecodeError, TypeError, ValueError):
                if attempt + 1 < json_attempts:
                    retry_messages = [
                        *messages,
                        {"role": "assistant", "content": last_text},
                        {"role": "user", "content": "只输出完整合法 JSON 对象。"},
                    ]
        # Application recognizes this prefix as a repairable contract error.
        raise CodexError("JSON 解析失败：Codex 返回的内容不是合法 JSON 对象") from None

    def account(self, *, refresh_token: bool = False) -> Any:
        return self._control().account(refresh_token=refresh_token)

    def login_chatgpt(self) -> Any:
        return self._control().login_chatgpt()

    def login_chatgpt_device_code(self) -> Any:
        return self._control().login_chatgpt_device_code()

    def models(self, *, include_hidden: bool = False) -> Any:
        return self._control().models(include_hidden=include_hidden)
