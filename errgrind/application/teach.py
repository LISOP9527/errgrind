"""Teach conversation workflow; input and rendering belong to its caller."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING
from ..llm.usage import usage_action
from ..llm.ocr import OcrError

from .attachments import attachment_tuples, hydrate_messages, image_parts_from_paths
from .contracts import (
    ConversationResult,
    ErrorNotFound,
    InvalidWorkflowState,
    OutputContractError,
    WorkflowModelError,
    public_error,
)

if TYPE_CHECKING:
    from ..db.ops import Database
    from ..llm.prompts import PromptManager


def _messages(raw):
    return json.loads(raw or "[]")


class TeachWorkflow:
    def __init__(self, db: "Database", llm, prompts: "PromptManager"):
        self.db = db
        self.llm = llm
        self.prompts = prompts

    @usage_action("teach", "reply")
    def start_or_resume(
        self,
        error_id: int,
        *,
        before_model_call: Callable[[], None] | None = None,
    ) -> ConversationResult:
        error = self._error(error_id)
        if error.status == "pending-grill" or not error.grilling_conversation:
            raise InvalidWorkflowState("请先进行 Grill 诊断")
        resumed = bool(error.teach_conversation)
        messages = _messages(error.teach_conversation)
        if not messages:
            history = "\n".join(
                f"{'导师' if item['role'] == 'assistant' else '学生'}：{item['content']}"
                for item in _messages(error.grilling_conversation)
                if item["role"] != "system"
            )
            system_prompt = self.prompts.load("teach.md").format(
                question=error.question,
                user_thoughts=error.user_thoughts or "",
                reference_answer=error.reference_answer or "",
                grilling_history=history,
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "请开始讲解"},
            ]
            self._save(error_id, messages)
        if messages[-1]["role"] == "user":
            return self._respond(
                error_id,
                messages,
                resumed=resumed,
                before_model_call=before_model_call,
            )
        return ConversationResult(public_error(self._error(error_id)), messages, None, resumed)

    @usage_action("teach", "reply")
    def submit_answer(
        self,
        error_id: int,
        answer: str,
        *,
        image_paths: list[str] | None = None,
        before_model_call: Callable[[], None] | None = None,
    ) -> ConversationResult:
        error = self._error(error_id)
        if error.status == "pending-grill" or not error.grilling_conversation:
            raise InvalidWorkflowState("请先进行 Grill 诊断")
        messages = _messages(error.teach_conversation)
        if not messages:
            raise InvalidWorkflowState("Teach 尚未开始，请先开始或恢复 Teach")
        if messages[-1]["role"] != "assistant":
            raise InvalidWorkflowState("当前正在等待 Teach 的模型回复")
        try:
            images = image_parts_from_paths(image_paths or [])
        except OcrError as exc:
            raise OutputContractError(str(exc)) from exc
        if not answer.strip() and not images:
            raise InvalidWorkflowState("Teach 回答不能为空；可以输入文字或添加图片。")
        messages.append({"role": "user", "content": answer})
        self._save(error_id, messages, attachments=attachment_tuples(images))
        return self._respond(
            error_id,
            messages,
            resumed=True,
            before_model_call=before_model_call,
        )

    def finish(self, error_id: int) -> ConversationResult:
        error = self._error(error_id)
        if error.status == "pending-grill" or not error.grilling_conversation:
            raise InvalidWorkflowState("请先进行 Grill 诊断")
        messages = _messages(error.teach_conversation)
        if not messages:
            raise InvalidWorkflowState("Teach 尚未开始，不能保存为完成")
        self.db.update_teach(error_id, json.dumps(messages, ensure_ascii=False))
        return ConversationResult(public_error(self._error(error_id)), messages, None, bool(messages))

    def _respond(self, error_id, messages, *, resumed, before_model_call):
        # The caller may show progress here. Conversation evidence has already
        # been saved, so an interrupt in that adapter hook remains recoverable.
        if before_model_call is not None:
            before_model_call()
        try:
            response = self.llm.chat(
                hydrate_messages(self.db, messages, error_id=error_id)
            )
        except Exception as exc:
            self._save(error_id, messages)
            raise WorkflowModelError(f"API 错误: {exc}") from exc
        messages.append({"role": "assistant", "content": response})
        self._save(error_id, messages)
        return ConversationResult(public_error(self._error(error_id)), messages, response, resumed)

    def _save(self, error_id, messages, *, attachments=()):
        attachment_ids = self.db.save_teach_conversation(
            error_id,
            json.dumps(messages, ensure_ascii=False),
            attachments=attachments,
        )
        if attachment_ids:
            messages[-1]["attachments"] = attachment_ids

    def _error(self, error_id):
        error = self.db.get_error(error_id)
        if error is None:
            raise ErrorNotFound(f"找不到 Error #{error_id}")
        return error
