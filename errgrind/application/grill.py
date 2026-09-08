"""Recoverable, structured Grill workflow with no terminal dependencies."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional

from ..llm.usage import usage_action, usage_scope

from .contracts import (
    ErrorNotFound,
    GrillResult,
    GrillState,
    InvalidWorkflowState,
    OutputContractError,
    WorkflowModelError,
    WorkflowPersistenceError,
    public_error,
)
from .grill_diagnosis import (
    GRILL_TURN_SCHEMA,
    _is_bootstrap_user_message,
    apply_turn_decision,
    load_diagnostic_state,
    validate_turn_decision,
)

if TYPE_CHECKING:
    from ..db.ops import Database
    from ..llm.prompts import PromptManager


# Kept only for old local fixtures/providers which have no chat_json method.
# New structured Grill never calls these helpers.
GRILLING_END = "[GRILLING_END]"


def is_grilling_complete(response: str) -> bool:
    """Legacy marker helper for old completed conversation fixtures."""
    return response.strip().endswith(GRILLING_END)


def grilling_summary(response: str) -> str:
    """Extract a legacy marker summary; structured Grill stores its own summary."""
    return response.strip()[: -len(GRILLING_END)].strip()


def _load_messages(raw: Optional[str]) -> list[dict[str, str]]:
    try:
        messages = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError) as exc:
        raise OutputContractError(f"Grill 对话不是合法 JSON: {exc}") from exc
    if not isinstance(messages, list):
        raise OutputContractError("Grill 对话必须是消息数组")
    return messages


def _json_state(state: dict[str, Any] | None) -> str | None:
    if state is None:
        return None
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _decision_value(decision: Any, key: str) -> Any:
    if isinstance(decision, dict):
        return decision[key]
    return getattr(decision, key)


class _MalformedStructuredResponse(Exception):
    """The provider returned text that its JSON parser could not decode."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class _LegacyJsonFixture(Exception):
    """Compatibility-only signal for an exhausted old test double."""


def _looks_like_json_parse_failure(exc: Exception) -> bool:
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return True
    detail = str(exc)
    folded = detail.casefold()
    return any(
        marker in detail
        for marker in (
            "JSON 解析失败",
            "JSON parse",
            "JSONDecodeError",
        )
    ) or ("json" in folded and any(word in folded for word in ("invalid", "decode", "parse")))


class GrillWorkflow:
    def __init__(self, db: "Database", llm, prompts: "PromptManager"):
        self.db = db
        self.llm = llm
        self.prompts = prompts

    @usage_action("grill", "turn")
    def start_or_resume(
        self, error_id: int, *, on_token: Optional[Callable[[str], None]] = None
    ) -> GrillResult:
        error = self._error(error_id)
        if error.status != "pending-grill":
            return GrillResult(
                error=self._public_error(error),
                messages=_load_messages(error.grilling_conversation),
                assistant_response=None,
                resumed=True,
                state=GrillState.READ_ONLY,
                summary=error.grilling_summary,
            )

        had_conversation = bool(error.grilling_conversation)
        messages = _load_messages(error.grilling_conversation)
        diagnostic_state = load_diagnostic_state(error.grilling_diagnostic_state)
        if not messages:
            system_prompt = self.prompts.load("grilling.md").format(
                question=error.question,
                user_thoughts=error.user_thoughts or "",
                reference_answer=error.reference_answer or "",
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "开始吧"},
            ]
            # Bootstrap is a recoverable fact even if the first model call fails.
            self._save(error_id, messages, diagnostic_state)

        if messages[-1].get("role") == "user":
            return self._respond(
                error_id,
                error,
                messages,
                diagnostic_state,
                resumed=had_conversation,
                require_latest_user_evidence=not _is_bootstrap_user_message(
                    messages, len(messages) - 1
                ),
                on_token=on_token,
            )
        return GrillResult(
            error=self._public_error(self._error(error_id)),
            messages=messages,
            assistant_response=None,
            resumed=had_conversation,
            state=GrillState.ACTIVE,
        )

    @usage_action("grill", "turn")
    def submit_answer(
        self,
        error_id: int,
        answer: str,
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> GrillResult:
        error = self._error(error_id)
        if error.status != "pending-grill":
            raise InvalidWorkflowState("已完成的 Grill 记录只读")
        messages = _load_messages(error.grilling_conversation)
        if not messages:
            raise InvalidWorkflowState("Grill 尚未开始，请先开始或恢复 Grill")
        if messages[-1].get("role") != "assistant":
            raise InvalidWorkflowState("当前正在等待 Grill 的模型回复")
        if not answer.strip():
            raise InvalidWorkflowState("Grill 回答不能为空；如果不记得，可以直接输入“不记得”。")
        diagnostic_state_json = error.grilling_diagnostic_state
        diagnostic_state = load_diagnostic_state(diagnostic_state_json)
        messages.append({"role": "user", "content": answer})
        # User evidence is durable before any fallible structured model call.
        self._save(
            error_id,
            messages,
            diagnostic_state,
            diagnostic_state_json=diagnostic_state_json,
        )
        return self._respond(
            error_id,
            error,
            messages,
            diagnostic_state,
            resumed=True,
            require_latest_user_evidence=True,
            on_token=on_token,
        )

    def pause(self, error_id: int) -> GrillResult:
        error = self._error(error_id)
        if error.status != "pending-grill":
            raise InvalidWorkflowState("已完成的 Grill 无需暂停")
        messages = _load_messages(error.grilling_conversation)
        diagnostic_state_json = error.grilling_diagnostic_state
        diagnostic_state = load_diagnostic_state(diagnostic_state_json)
        self._save(
            error_id,
            messages,
            diagnostic_state,
            diagnostic_state_json=diagnostic_state_json,
        )
        return GrillResult(
            error=self._public_error(self._error(error_id)),
            messages=messages,
            assistant_response=None,
            resumed=bool(messages),
            state=GrillState.PAUSED,
        )

    def _respond(
        self,
        error_id: int,
        error,
        messages: list[dict[str, str]],
        diagnostic_state: dict[str, Any] | None,
        *,
        resumed: bool,
        require_latest_user_evidence: bool,
        on_token: Optional[Callable[[str], None]],
    ) -> GrillResult:
        # Existing test doubles and pre-structured providers may only expose
        # chat/stream_chat.  Keep that isolated fallback for old conversations.
        if not callable(getattr(self.llm, "chat_json", None)):
            return self._respond_legacy(error_id, messages, resumed=resumed, on_token=on_token)
        try:
            decision = self._request_structured(
                error,
                messages,
                diagnostic_state,
                require_latest_user_evidence=require_latest_user_evidence,
            )
        except _LegacyJsonFixture:
            # A legacy test double may advertise chat_json but have no JSON
            # fixture.  It is not a production provider capability failure.
            return self._respond_legacy(error_id, messages, resumed=resumed, on_token=on_token)

        new_state = apply_turn_decision(diagnostic_state, decision)
        action = _decision_value(decision, "next_action")
        if action in {"reasoning_question", "variant_problem"}:
            visible = _decision_value(decision, "probe")["question"]
            result_state = GrillState.ACTIVE
        else:
            visible = _decision_value(decision, "summary")
            result_state = GrillState.COMPLETE

        completed_messages = [
            *messages,
            {"role": "assistant", "content": visible},
        ]
        conversation_json = json.dumps(completed_messages, ensure_ascii=False)
        try:
            if result_state == GrillState.COMPLETE:
                self.db.complete_grilling(
                    error_id,
                    conversation_json,
                    visible,
                    _json_state(new_state),
                )
            else:
                self.db.save_grilling_progress(
                    error_id,
                    conversation_json,
                    _json_state(new_state),
                )
        except Exception as exc:
            # The user's answer was already saved by submit_answer; this
            # unvalidated/unpersisted assistant output is never shown.
            raise WorkflowPersistenceError(f"保存 Grill 结果失败: {exc}") from exc

        # Structured output is intentionally delivered once, only after the
        # state and visible assistant message are durably saved.
        if on_token is not None:
            on_token(visible)
        return GrillResult(
            error=self._public_error(self._error(error_id)),
            messages=completed_messages,
            assistant_response=visible,
            resumed=resumed,
            state=result_state,
            summary=visible if result_state == GrillState.COMPLETE else None,
        )

    def _request_structured(
        self,
        error,
        messages: list[dict[str, str]],
        diagnostic_state: dict[str, Any] | None,
        *,
        require_latest_user_evidence: bool,
    ):
        # Persisted system prompts are historical records, not today's protocol.
        # Replace them only in the request; evidence indices still use messages.
        current_prompt = self.prompts.load("grilling.md").format(
            question=error.question,
            user_thoughts=error.user_thoughts or "",
            reference_answer=error.reference_answer or "",
        )
        call_messages = [
            {"role": "system", "content": current_prompt},
            *(message for message in messages if message.get("role") != "system"),
            {
                "role": "system",
                "content": self._diagnostic_context(
                    error, messages, diagnostic_state
                ),
            },
        ]
        try:
            raw = self._chat_json(call_messages)
        except _MalformedStructuredResponse as exc:
            first_error: OutputContractError = OutputContractError(
                "Grill 输出不是合法 JSON"
            )
            raw = {"_invalid_json": exc.detail}
        else:
            try:
                return validate_turn_decision(
                    raw,
                    diagnostic_state,
                    messages,
                    initial_user_thoughts=error.user_thoughts,
                    require_latest_user_evidence=require_latest_user_evidence,
                )
            except OutputContractError as exc:
                first_error = exc

        repair_messages = [
            *call_messages,
            {
                "role": "assistant",
                "content": self._repair_payload(raw),
            },
            {
                "role": "user",
                "content": (
                    f"上次 Grill 输出违反本地契约：{first_error}。"
                    "请只重新输出完整 JSON；保留所有 required 字段，"
                    "修复字段、引用、Evidence grounding 或状态约束，不要解释修复过程。"
                ),
            },
        ]
        try:
            with usage_scope(repair_attempt=2):
                repaired = self._chat_json(repair_messages)
        except _LegacyJsonFixture as exc:
            raise OutputContractError("Grill 输出契约修复失败：没有得到第二份 JSON 输出") from exc
        except _MalformedStructuredResponse:
            raise OutputContractError(
                "Grill 输出契约修复失败：仍不是合法 JSON，请稍后恢复诊断。"
            ) from None
        try:
            return validate_turn_decision(
                repaired,
                diagnostic_state,
                messages,
                initial_user_thoughts=error.user_thoughts,
                require_latest_user_evidence=require_latest_user_evidence,
            )
        except OutputContractError:
            # Semantic errors may also contain model-controlled field names.
            raise OutputContractError(
                "Grill 输出契约修复失败：字段或证据不符合要求，请稍后恢复诊断。"
            ) from None

    def _chat_json(self, messages: list[dict[str, str]]):
        try:
            return self.llm.chat_json(
                messages,
                output_schema=GRILL_TURN_SCHEMA,
                max_json_attempts=1,
            )
        except StopIteration as exc:
            raise _LegacyJsonFixture from exc
        except Exception as exc:
            if _looks_like_json_parse_failure(exc):
                raise _MalformedStructuredResponse(str(exc)) from exc
            raise WorkflowModelError("Grill API 调用失败，请稍后恢复诊断。") from None

    @staticmethod
    def _repair_payload(raw: Any) -> str:
        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return repr(raw)

    @staticmethod
    def _diagnostic_context(error, messages, diagnostic_state) -> str:
        addressable = []
        if error.user_thoughts:
            addressable.append(
                "initial_user_thoughts:\n" + str(error.user_thoughts)
            )
        for index, message in enumerate(messages):
            if (
                message.get("role") == "user"
                and isinstance(message.get("content"), str)
                and not _is_bootstrap_user_message(messages, index)
            ):
                addressable.append(f"message:{index}:\n{message['content']}")
        evidence_text = "\n\n".join(addressable) or "（当前没有可引用的用户原话）"
        state_text = json.dumps(
            diagnostic_state, ensure_ascii=False, indent=2
        ) if diagnostic_state is not None else "null"
        return (
            "[Current Diagnostic State]\n"
            + state_text
            + "\n\n[Addressable User Evidence]\n"
            + evidence_text
            + "\n\n[Protocol]\n"
            "只允许引用上面列出的 source_ref。当前 state 是事实来源；"
            "不得重写旧 hypothesis claim、旧 Evidence 或旧 Probe；本轮只输出 delta。"
        )

    def _respond_legacy(
        self,
        error_id: int,
        messages: list[dict[str, str]],
        *,
        resumed: bool,
        on_token: Optional[Callable[[str], None]],
    ) -> GrillResult:
        try:
            stream_chat = getattr(self.llm, "stream_chat", None)
            if callable(stream_chat):
                response = stream_chat(messages, on_token or (lambda _token: None))
            else:
                response = self.llm.chat(messages)
        except Exception as exc:
            self._save_existing_conversation(error_id, messages)
            raise WorkflowModelError(f"API 错误: {exc}") from exc

        messages.append({"role": "assistant", "content": response})
        if is_grilling_complete(response):
            summary = grilling_summary(response)
            messages[-1]["content"] = summary
            try:
                self.db.update_grilling(
                    error_id, json.dumps(messages, ensure_ascii=False), summary
                )
            except Exception as exc:
                raise WorkflowPersistenceError(f"保存 Grill 结果失败: {exc}") from exc
            return GrillResult(
                error=self._public_error(self._error(error_id)),
                messages=messages,
                assistant_response=summary,
                resumed=resumed,
                state=GrillState.COMPLETE,
                summary=summary,
            )

        self._save_existing_conversation(error_id, messages)
        return GrillResult(
            error=self._public_error(self._error(error_id)),
            messages=messages,
            assistant_response=response,
            resumed=resumed,
            state=GrillState.ACTIVE,
        )

    def _save(
        self,
        error_id: int,
        messages: list[dict[str, str]],
        diagnostic_state: dict[str, Any] | None,
        *,
        diagnostic_state_json: str | None = None,
    ) -> None:
        try:
            self.db.save_grilling_progress(
                error_id,
                json.dumps(messages, ensure_ascii=False),
                diagnostic_state_json
                if diagnostic_state_json is not None
                else _json_state(diagnostic_state),
            )
        except Exception as exc:
            raise WorkflowPersistenceError(f"保存 Grill 对话失败: {exc}") from exc

    def _save_existing_conversation(
        self, error_id: int, messages: list[dict[str, str]]
    ) -> None:
        try:
            self.db.save_grilling_conversation(
                error_id, json.dumps(messages, ensure_ascii=False)
            )
        except Exception as exc:
            raise WorkflowPersistenceError(f"保存 Grill 对话失败: {exc}") from exc

    def _error(self, error_id):
        error = self.db.get_error(error_id)
        if error is None:
            raise ErrorNotFound(f"找不到 Error #{error_id}")
        return error

    @staticmethod
    def _public_error(error):
        """Keep internal diagnostic state in DB, not in frontend results."""
        return public_error(error)
