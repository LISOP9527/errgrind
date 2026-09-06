"""ErrGrind adapter for the official ``openai-codex`` Python SDK.

The SDK owns authentication and starts the local Codex app-server.  This
module deliberately does not inspect Codex's credential files or implement
OAuth itself.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from typing import Any

from .ocr import OCR_OUTPUT_SCHEMA, load_image, parse_ocr_result


class CodexError(Exception):
    """User-facing error raised by the Codex provider."""


def _load_sdk() -> tuple[Any, Any, Any, Any, Any, Any]:
    try:
        from openai_codex import (
            ApprovalMode,
            Codex,
            CodexConfig,
            LocalImageInput,
            Sandbox,
            TextInput,
        )
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise CodexError(
            "Codex provider 需要官方 openai-codex SDK 和匹配的 runtime；"
            "请在项目目录重新运行 bash install.sh。"
        ) from exc
    return Codex, CodexConfig, ApprovalMode, Sandbox, TextInput, LocalImageInput


def _text_from_event(event: Any) -> str:
    """Extract only assistant text deltas from a public SDK notification."""
    # Current SDK exposes AgentMessageDeltaNotification.delta.  Attribute
    # access keeps this compatible with pydantic model instances and mocks.
    payload = getattr(event, "payload", event)
    method = getattr(event, "method", "")
    if str(method) != "item/agentMessage/delta":
        return ""
    delta = getattr(payload, "delta", "")
    return delta if isinstance(delta, str) else ""


def _result_text(result: Any) -> str:
    text = getattr(result, "final_response", None)
    if isinstance(text, str):
        return text
    if isinstance(result, dict) and isinstance(result.get("final_response"), str):
        return result["final_response"]
    raise CodexError("Codex 响应中没有文本")


class CodexClient:
    """Synchronous, read-only Codex provider matching ErrGrind's clients."""

    def __init__(
        self,
        model: str = "gpt-5.6-sol",
        max_retries: int = 3,
        sdk: Any | None = None,
        codex_bin: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_retries = max(1, max_retries)
        self._workspace = tempfile.TemporaryDirectory(prefix="errgrind-codex-")
        self._closed = False
        if sdk is None:
            try:
                (
                    Codex,
                    CodexConfig,
                    _ApprovalMode,
                    _Sandbox,
                    _TextInput,
                    _LocalImageInput,
                ) = _load_sdk()
                # Let the official SDK resolve its matching bundled runtime;
                # accepting an explicit binary is useful for controlled test
                # or deployment environments without silently mixing CLI
                # protocol versions.
                config_kwargs: dict[str, Any] = {"cwd": os.fspath(self._workspace.name)}
                if codex_bin:
                    config_kwargs["codex_bin"] = codex_bin
                config = CodexConfig(**config_kwargs)
                sdk = Codex(config=config)
            except KeyboardInterrupt:
                self._workspace.cleanup()
                raise
            except CodexError:
                self._workspace.cleanup()
                raise
            except Exception as exc:
                self._workspace.cleanup()
                raise CodexError(f"Codex app-server 启动失败: {exc}") from exc
        self._sdk = sdk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._sdk, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # Shutdown must not turn a user Ctrl+C or a completed
                    # session into a second, unrelated error.
                    pass
        finally:
            self._workspace.cleanup()

    def __enter__(self) -> "CodexClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @staticmethod
    def _prompt(messages: list[dict]) -> tuple[str, str]:
        system: list[str] = []
        turns: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if role == "system":
                system.append(content)
            else:
                turns.append({"role": role, "content": content})
        prompt = (
            "下面 JSON 是此前对话记录。严格保持其中的 user/assistant 角色，"
            "只生成对最后一条 user 消息的下一条 assistant 回复。\n"
            + json.dumps(turns, ensure_ascii=False)
        )
        return "\n\n".join(system), prompt

    def _new_thread(self, messages: list[dict]) -> Any:
        _Codex, _Config, ApprovalMode, Sandbox, _TextInput, _LocalImageInput = _load_sdk()
        instructions, prompt = self._prompt(messages)
        kwargs: dict[str, Any] = {
            "base_instructions": instructions or None,
            "developer_instructions": (
                "你是 ErrGrind 的纯文本推理模型。只回答输入中的最后一个用户请求。"
                "绝对不要调用任何工具、执行命令、访问网络、读取或修改文件。"
            ),
            "cwd": os.fspath(self._workspace.name),
            "ephemeral": True,
            "model": self.model,
            "sandbox": Sandbox.read_only,
            "approval_mode": ApprovalMode.deny_all,
        }
        return self._sdk.thread_start(**kwargs), prompt

    def ocr_image(self, image_path: str, prompt: str) -> dict[str, str]:
        """Transcribe one local image through the official image input API."""
        payload = load_image(image_path)
        (
            _Codex,
            _Config,
            ApprovalMode,
            Sandbox,
            TextInput,
            LocalImageInput,
        ) = _load_sdk()
        last: Exception | None = None
        for attempt in range(self.max_retries):
            turn = None
            try:
                thread = self._sdk.thread_start(
                    base_instructions=prompt,
                    developer_instructions=(
                        "你只执行图片转录。不要调用工具、执行命令、访问网络或修改文件。"
                        "不得根据常识补全图片中不可见的内容。"
                    ),
                    cwd=os.fspath(self._workspace.name),
                    ephemeral=True,
                    model=self.model,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                )
                turn = thread.turn(
                    [
                        TextInput("请按照系统规则转录这张数学错题图片。"),
                        LocalImageInput(payload.path),
                    ],
                    model=self.model,
                    output_schema=OCR_OUTPUT_SCHEMA,
                    **({"effort": self.reasoning_effort} if self.reasoning_effort else {}),
                )
                result = turn.run()
                return parse_ocr_result(_result_text(result))
            except KeyboardInterrupt:
                if turn is not None:
                    try:
                        turn.interrupt()
                    except Exception:
                        pass
                raise
            except Exception as exc:
                last = exc
            if attempt + 1 < self.max_retries:
                time.sleep(2**attempt)
        raise CodexError(f"Codex OCR 调用失败: {last}") from last

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        last: Exception | None = None
        for attempt in range(self.max_retries):
            turn = None
            try:
                thread, prompt = self._new_thread(messages)
                turn_kwargs = dict(kwargs)
                turn_kwargs.setdefault("model", self.model)
                if self.reasoning_effort:
                    turn_kwargs.setdefault("effort", self.reasoning_effort)
                turn = thread.turn(prompt, **turn_kwargs)
                result = turn.run()
                return _result_text(result)
            except KeyboardInterrupt:
                if turn is not None:
                    try:
                        turn.interrupt()
                    except Exception:
                        pass
                raise
            except Exception as exc:
                last = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(2**attempt)
        raise CodexError(f"Codex 调用失败: {last}") from last

    def stream_chat(self, messages: list[dict], on_token: Callable[[str], None], **kwargs: Any) -> str:
        last: Exception | None = None
        for attempt in range(self.max_retries):
            collected: list[str] = []
            turn = None
            try:
                thread, prompt = self._new_thread(messages)
                turn_kwargs = dict(kwargs)
                turn_kwargs.setdefault("model", self.model)
                if self.reasoning_effort:
                    turn_kwargs.setdefault("effort", self.reasoning_effort)
                turn = thread.turn(prompt, **turn_kwargs)
                for event in turn.stream():
                    token = _text_from_event(event)
                    if token:
                        collected.append(token)
                        on_token(token)
                return "".join(collected)
            except KeyboardInterrupt as exc:
                if turn is not None:
                    try:
                        turn.interrupt()
                    except Exception:
                        pass
                raise KeyboardInterrupt from exc
            except Exception as exc:
                if collected:
                    raise CodexError(f"Codex 流式调用中断: {exc}") from exc
                last = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(2**attempt)
        raise CodexError(f"Codex 调用失败: {last}") from last

    def chat_json(self, messages: list[dict], **kwargs: Any) -> dict:
        # Codex structured output requires a fully strict schema.  Callers
        # with a known contract pass one; generic JSON calls rely on the
        # prompt plus the local parse/retry guard below.
        output_schema = kwargs.pop("output_schema", None)
        json_attempts = max(
            1, int(kwargs.pop("max_json_attempts", self.max_retries))
        )
        if output_schema is not None:
            kwargs["output_schema"] = output_schema
        retry_messages = list(messages)
        last_text = ""
        last_error: Exception | None = None
        for attempt in range(json_attempts):
            last_text = self.chat(retry_messages, **kwargs)
            try:
                result = json.loads(last_text)
                if not isinstance(result, dict):
                    raise ValueError("顶层必须是 JSON 对象")
                return result
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < json_attempts:
                    retry_messages = [
                        *messages,
                        {"role": "assistant", "content": last_text},
                        {"role": "user", "content": "只输出完整合法 JSON 对象。"},
                    ]
        raise CodexError(f"JSON 解析失败: {last_error}\n原始响应: {last_text}")

    def account(self, *, refresh_token: bool = False) -> Any:
        """Return SDK-owned account status without touching credential files."""
        return self._sdk.account(refresh_token=refresh_token)

    def login_chatgpt(self) -> Any:
        return self._sdk.login_chatgpt()

    def login_chatgpt_device_code(self) -> Any:
        return self._sdk.login_chatgpt_device_code()

    def models(self, *, include_hidden: bool = False) -> Any:
        return self._sdk.models(include_hidden=include_hidden)
