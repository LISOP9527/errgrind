"""Recoverable Grill workflow with no terminal dependencies."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Optional

from .contracts import (
    ErrorNotFound,
    GrillResult,
    GrillState,
    InvalidWorkflowState,
    WorkflowModelError,
)

if TYPE_CHECKING:
    from ..db.ops import Database
    from ..llm.prompts import PromptManager


GRILLING_END = "[GRILLING_END]"


def is_grilling_complete(response: str) -> bool:
    """The protocol marker only has meaning when it is the final content."""
    return response.strip().endswith(GRILLING_END)


def grilling_summary(response: str) -> str:
    return response.strip()[: -len(GRILLING_END)].strip()


def _load_messages(raw: Optional[str]) -> list[dict[str, str]]:
    return json.loads(raw or "[]")


class GrillWorkflow:
    def __init__(self, db: "Database", llm, prompts: "PromptManager"):
        self.db = db
        self.llm = llm
        self.prompts = prompts

    def start_or_resume(
        self, error_id: int, *, on_token: Optional[Callable[[str], None]] = None
    ) -> GrillResult:
        error = self._error(error_id)
        if error.status != "pending-grill":
            return GrillResult(
                error=error,
                messages=_load_messages(error.grilling_conversation),
                assistant_response=None,
                resumed=True,
                state=GrillState.READ_ONLY,
                summary=error.grilling_summary,
            )

        had_conversation = bool(error.grilling_conversation)
        messages = _load_messages(error.grilling_conversation)
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
            # The bootstrap is a recoverable fact even if the first API call fails.
            self._save(error_id, messages)

        if messages[-1]["role"] == "user":
            return self._respond(
                error_id,
                messages,
                resumed=had_conversation,
                on_token=on_token,
            )
        return GrillResult(
            error=self._error(error_id),
            messages=messages,
            assistant_response=None,
            resumed=had_conversation,
            state=GrillState.ACTIVE,
        )

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
        if messages[-1]["role"] != "assistant":
            raise InvalidWorkflowState("当前正在等待 Grill 的模型回复")
        messages.append({"role": "user", "content": answer})
        # Save the user evidence before attempting a fallible model call.
        self._save(error_id, messages)
        return self._respond(error_id, messages, resumed=True, on_token=on_token)

    def pause(self, error_id: int) -> GrillResult:
        error = self._error(error_id)
        if error.status != "pending-grill":
            raise InvalidWorkflowState("已完成的 Grill 无需暂停")
        messages = _load_messages(error.grilling_conversation)
        self._save(error_id, messages)
        self.db.set_status(error_id, "pending-grill")
        return GrillResult(
            error=self._error(error_id),
            messages=messages,
            assistant_response=None,
            resumed=bool(messages),
            state=GrillState.PAUSED,
        )

    def _respond(self, error_id, messages, *, resumed, on_token):
        try:
            stream_chat = getattr(self.llm, "stream_chat", None)
            if callable(stream_chat):
                response = stream_chat(messages, on_token or (lambda _token: None))
            else:
                response = self.llm.chat(messages)
        except Exception as exc:
            self._save(error_id, messages)
            raise WorkflowModelError(f"API 错误: {exc}") from exc

        messages.append({"role": "assistant", "content": response})
        if is_grilling_complete(response):
            summary = grilling_summary(response)
            messages[-1]["content"] = summary
            self.db.update_grilling(
                error_id, json.dumps(messages, ensure_ascii=False), summary
            )
            return GrillResult(
                error=self._error(error_id),
                messages=messages,
                assistant_response=summary,
                resumed=resumed,
                state=GrillState.COMPLETE,
                summary=summary,
            )

        self._save(error_id, messages)
        return GrillResult(
            error=self._error(error_id),
            messages=messages,
            assistant_response=response,
            resumed=resumed,
            state=GrillState.ACTIVE,
        )

    def _save(self, error_id: int, messages: list[dict[str, str]]) -> None:
        self.db.save_grilling_conversation(
            error_id, json.dumps(messages, ensure_ascii=False)
        )

    def _error(self, error_id):
        error = self.db.get_error(error_id)
        if error is None:
            raise ErrorNotFound(f"找不到 Error #{error_id}")
        return error
