"""The small application facade used by every frontend."""

import hashlib
from collections.abc import Callable
from collections.abc import Mapping
from typing import Optional
from ..llm.usage import usage_action, usage_scope

from .contracts import (
    ConversationResult,
    DrillJudgment,
    DrillPreparation,
    DrillStage,
    ErrorNotFound,
    GrillResult,
    NextStep,
    RecordDraft,
    WorkflowPersistenceError,
    OutputContractError,
    WorkflowModelError,
    next_step_for_error,
    public_error,
)
from ..models.types import DrillAttemptView
from ..models.titles import display_title_for_question
from ..llm.messages import ImagePart, MultimodalMessage
from .attachments import attachment_tuples, image_parts_from_paths
from .drill import DrillWorkflow
from .grill import GrillWorkflow
from .teach import TeachWorkflow


RECORD_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "user_thoughts": {"type": "string"},
        "reference_answer": {"type": "string"},
    },
    "required": ["question", "user_thoughts", "reference_answer"],
    "additionalProperties": False,
}
RECORD_DRAFT_FIELDS = ("question", "user_thoughts", "reference_answer")


class ErrGrindApplication:
    """Compose current infrastructure without exposing a CLI-shaped API."""

    def __init__(self, db, llm, prompts, cfg: Optional[dict] = None):
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.cfg = cfg if cfg is not None else {}

    def get_error(self, error_id: int):
        return public_error(self.db.get_error(error_id))

    def list_errors(self):
        return [
            public_error(error)
            for error in self.db.list_all_errors()
        ]

    def get_next_step(self, error_id: int) -> NextStep:
        """Return a small, stable recommendation for an Error workspace."""
        error = self.db.get_error(error_id)
        if error is None:
            raise ErrorNotFound(f"找不到 Error #{error_id}")
        return next_step_for_error(error)

    def record_error(
        self,
        question: str,
        user_thoughts: Optional[str],
        reference_answer: Optional[str] = None,
        *,
        origin: str = "record",
        image_paths: Optional[list[str]] = None,
        pending_key: Optional[str] = None,
    ):
        """Validate and persist one user-recorded Error.

        This is deliberately small: frontends collect and review text, while
        the application boundary owns validation, provenance, and the public
        (diagnostic-state-free) return value.
        """
        if not isinstance(question, str):
            raise OutputContractError("题目必须是文字")
        if user_thoughts is not None and not isinstance(user_thoughts, str):
            raise OutputContractError("用户思路必须是文字")
        if reference_answer is not None and not isinstance(reference_answer, str):
            raise OutputContractError("参考答案必须是文字")
        if origin not in {"record", "ocr"}:
            raise OutputContractError("录题来源必须是 record 或 ocr")

        answer = reference_answer.strip() if reference_answer is not None else None
        answer = answer or None
        thoughts = user_thoughts.strip() if isinstance(user_thoughts, str) else None
        try:
            images = image_parts_from_paths(image_paths or [])
        except Exception as exc:
            from ..llm.ocr import OcrError

            if isinstance(exc, OcrError):
                raise OutputContractError(str(exc)) from exc
            raise WorkflowPersistenceError("读取 Error 图片失败，请重试") from exc
        has_pending_images = bool(
            pending_key and self.db.pending_attachment_count(pending_key)
        )
        if not question.strip() and not images and not has_pending_images:
            raise OutputContractError("题目不能为空")
        try:
            error_id = self.db.create_error(
                question.strip(),
                thoughts or None,
                answer,
                origin=origin,
                display_title=display_title_for_question(question),
                attachments=attachment_tuples(images),
                pending_key=pending_key,
            )
            error = self.db.get_error(error_id)
        except Exception as exc:
            raise WorkflowPersistenceError("保存 Error 失败，请稍后重试") from exc
        if error is None:
            raise WorkflowPersistenceError("保存 Error 失败，请稍后重试")
        return public_error(error)

    def delete_error(self, error_id: int) -> None:
        self.db.delete_error(error_id)

    def _transcribe_image_field(
        self, image_path: str, field: str, *, action: str = "record"
    ) -> str:
        """Transcribe one image into an editable, unpersisted draft."""
        from ..llm.ocr import OcrError

        fields = {
            "question": "题目",
            "user_thoughts": "用户当时的思路或作答",
            "reference_answer": "参考答案",
            "drill_answer": "当前 Drill 的答案与解题思路",
            "record_input": "整张数学错题材料（题目、用户思路或作答、参考答案）",
        }
        if field not in fields:
            raise OutputContractError("不支持的录题字段")
        transcribe = getattr(self.llm, "transcribe_image", None)
        if not callable(transcribe):
            raise WorkflowModelError("当前 AI provider 不支持字段图片识别，请切换 provider")
        try:
            prompt_name = "ocr_record_input.md" if field == "record_input" else "ocr_field.md"
            prompt = self.prompts.load(prompt_name).format(field=fields[field])
            with usage_scope(action=action, stage="ocr_" + field):
                result = transcribe(image_path, prompt)
        except (KeyboardInterrupt, EOFError):
            raise
        except OcrError as exc:
            raise OutputContractError(str(exc)) from exc
        except Exception as exc:
            # 模型原始响应不是面向用户的错误说明，也不能作为录题文本回填。
            raise WorkflowModelError("图片识别失败，请重试或检查当前模型是否支持图片") from exc
        if not isinstance(result, str) or not result.strip():
            raise OutputContractError("没有识别出当前字段的文字，请换一张图片或手动输入")
        return result.strip()

    @usage_action("record", "ocr_field")
    def transcribe_record_field(self, image_path: str, field: str) -> str:
        """Return an unpersisted OCR draft for one record field."""
        if field not in {"question", "user_thoughts", "reference_answer"}:
            raise OutputContractError("不支持的录题字段")
        return self._transcribe_image_field(image_path, field, action="record")

    @usage_action("record", "ocr_input")
    def transcribe_record_input(self, image_path: str) -> str:
        """Transcribe a raw record image without organizing or persisting it."""
        return self._transcribe_image_field(image_path, "record_input", action="record")

    def prepare_record_draft(
        self,
        raw_input: str,
        image_paths: Optional[list[str]] = None,
        *,
        current_draft: Optional[Mapping[str, str] | RecordDraft] = None,
        pending_key: Optional[str] = None,
    ) -> RecordDraft:
        """Organize supplied record material into a draft for human review.

        Each call is an adjustment to the current editable draft.  Empty
        semantic fields are valid here: ``record_error`` enforces that the
        final record contains a question or at least one original image.
        """
        if not isinstance(raw_input, str):
            raise OutputContractError("录入内容必须是文字")
        current = self._normalize_record_draft(current_draft)
        image_paths = list(image_paths or [])
        try:
            direct_images = image_parts_from_paths(image_paths)
        except Exception as exc:
            from ..llm.ocr import OcrError

            if isinstance(exc, OcrError):
                raise OutputContractError(str(exc)) from exc
            raise WorkflowModelError("读取图片失败，请重试") from exc
        try:
            pending_images = tuple(
                ImagePart(item.mime_type, item.data)
                for item in (
                    self.db.list_pending_attachments(pending_key)
                    if pending_key else ()
                )
            )
        except Exception as exc:
            raise WorkflowPersistenceError("读取暂存图片失败，请重试") from exc
        images = []
        seen_image_hashes = set()
        for image in (*pending_images, *direct_images):
            digest = hashlib.sha256(image.data).digest()
            if digest in seen_image_hashes:
                continue
            seen_image_hashes.add(digest)
            images.append(image)
        if not raw_input.strip() and not images and not any(current.values()):
            raise OutputContractError("请先输入内容或添加图片")
        source_text = raw_input.strip()
        prompt = self.prompts.load("record_draft.md").format(
            source_text=source_text,
            current_question=current["question"],
            current_user_thoughts=current["user_thoughts"],
            current_reference_answer=current["reference_answer"],
        )
        try:
            with usage_scope(action="record", stage="draft"):
                messages = [
                    MultimodalMessage("user", prompt, tuple(images))
                    if images
                    else {"role": "user", "content": prompt}
                ]
                raw = self.llm.chat_json(
                    messages,
                    output_schema=RECORD_DRAFT_SCHEMA,
                )
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as exc:
            raise WorkflowModelError("整理录入草稿失败，请保留原始内容后重试") from exc
        if not isinstance(raw, dict):
            raise OutputContractError("录入草稿必须是 JSON 对象")
        if set(raw) != set(RECORD_DRAFT_FIELDS):
            raise OutputContractError("录入草稿字段结构无效")
        values = {}
        for field in RECORD_DRAFT_FIELDS:
            value = raw[field]
            if not isinstance(value, str):
                raise OutputContractError(f"录入草稿字段 {field} 必须是文本")
            values[field] = value.strip()
        return RecordDraft(
            question=values["question"],
            user_thoughts=values["user_thoughts"],
            reference_answer=values["reference_answer"],
            origin="record",
        )

    @staticmethod
    def _normalize_record_draft(
        current_draft: Optional[Mapping[str, str] | RecordDraft],
    ) -> dict[str, str]:
        """Validate the editable state before placing it in a model prompt."""
        if current_draft is None:
            return {field: "" for field in RECORD_DRAFT_FIELDS}
        if isinstance(current_draft, RecordDraft):
            source = {
                field: getattr(current_draft, field)
                for field in RECORD_DRAFT_FIELDS
            }
        elif isinstance(current_draft, Mapping):
            source = {
                field: current_draft.get(field, "")
                for field in RECORD_DRAFT_FIELDS
            }
        else:
            raise OutputContractError("当前录入草稿必须是对象")
        values = {}
        for field in RECORD_DRAFT_FIELDS:
            value = source[field]
            if not isinstance(value, str):
                raise OutputContractError(f"当前录入草稿字段 {field} 必须是文本")
            values[field] = value.strip()
        return values

    @usage_action("drill", "ocr_answer")
    def transcribe_drill_answer(self, image_path: str) -> str:
        """Return an editable OCR draft for the user's current Drill answer."""
        return self._transcribe_image_field(image_path, "drill_answer", action="drill")

    def start_or_resume_grill(
        self,
        error_id: int,
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> GrillResult:
        return GrillWorkflow(self.db, self.llm, self.prompts).start_or_resume(
            error_id, on_token=on_token
        )

    def submit_grill_answer(
        self,
        error_id: int,
        answer: str,
        *,
        image_paths: Optional[list[str]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> GrillResult:
        return GrillWorkflow(self.db, self.llm, self.prompts).submit_answer(
            error_id, answer, image_paths=image_paths, on_token=on_token
        )

    def pause_grill(self, error_id: int) -> GrillResult:
        return GrillWorkflow(self.db, self.llm, self.prompts).pause(error_id)

    def start_or_resume_teach(
        self,
        error_id: int,
        *,
        before_model_call: Optional[Callable[[], None]] = None,
    ) -> ConversationResult:
        return TeachWorkflow(self.db, self.llm, self.prompts).start_or_resume(
            error_id, before_model_call=before_model_call
        )

    def submit_teach_answer(
        self,
        error_id: int,
        answer: str,
        *,
        image_paths: Optional[list[str]] = None,
        before_model_call: Optional[Callable[[], None]] = None,
    ) -> ConversationResult:
        return TeachWorkflow(self.db, self.llm, self.prompts).submit_answer(
            error_id,
            answer,
            image_paths=image_paths,
            before_model_call=before_model_call,
        )

    def finish_teach(self, error_id: int) -> ConversationResult:
        return TeachWorkflow(self.db, self.llm, self.prompts).finish(error_id)

    def prepare_drill(
        self, *, on_stage: Optional[Callable[[DrillStage], None]] = None
    ) -> DrillPreparation:
        return DrillWorkflow(self.db, self.llm, self.prompts, self.cfg).prepare(
            on_stage=on_stage
        )

    def judge_and_record_drill(
        self,
        preparation: DrillPreparation,
        user_response: str,
        *,
        image_paths: Optional[list[str]] = None,
        pending_key: Optional[str] = None,
    ) -> DrillJudgment:
        return DrillWorkflow(
            self.db, self.llm, self.prompts, self.cfg
        ).judge_and_record(
            preparation,
            user_response,
            image_paths=image_paths,
            pending_key=pending_key,
        )

    def get_error_attachment_count(
        self, error_id: int, *, conversation_kind: Optional[str] = "initial"
    ) -> int:
        return len(
            self.db.list_error_attachments(
                error_id, conversation_kind=conversation_kind
            )
        )

    def list_error_attachments(
        self, error_id: int, *, conversation_kind: Optional[str] = None
    ):
        """Return attachments belonging to one Error for a frontend adapter.

        The web adapter may expose only metadata or bytes from these returned
        records after it has checked the owning Error.  Keeping this lookup at
        the application boundary avoids teaching the frontend about SQLite.
        """
        return self.db.list_error_attachments(
            error_id, conversation_kind=conversation_kind
        )

    def get_error_attachment(self, error_id: int, attachment_id: int):
        """Return one attachment only when it belongs to ``error_id``."""
        try:
            matches = self.db.get_attachments_for_ids([attachment_id], error_id=error_id)
        except (TypeError, ValueError):
            return None
        return matches[0] if matches else None

    def append_pending_image_attachments(
        self, pending_key: str, image_paths: list[str]
    ) -> int:
        """Durably stage original user images before a fallible model call."""
        try:
            images = image_parts_from_paths(image_paths)
        except Exception as exc:
            from ..llm.ocr import OcrError
            if isinstance(exc, OcrError):
                raise OutputContractError(str(exc)) from exc
            raise WorkflowPersistenceError("读取图片失败，请重试") from exc
        try:
            self.db.append_pending_attachments(pending_key, attachment_tuples(images))
        except Exception as exc:
            raise WorkflowPersistenceError("暂存图片失败，请重试") from exc
        return self.db.pending_attachment_count(pending_key)

    def get_pending_attachment_count(self, pending_key: str) -> int:
        return self.db.pending_attachment_count(pending_key)

    def delete_pending_attachments(self, pending_key: str) -> None:
        self.db.delete_pending_attachments(pending_key)

    def list_drill_history(self) -> list[DrillAttemptView]:
        """Return only the question and target mechanism for every judged Drill."""
        return [
            self._public_drill_attempt(attempt)
            for attempt in self.db.list_drill_attempts(limit=None)
        ]

    def get_drill_history(self, attempt_id: int) -> DrillAttemptView | None:
        """Read one judged Drill without invoking the model or changing the DB."""
        attempt = self.db.get_drill_attempt(attempt_id)
        return self._public_drill_attempt(attempt) if attempt is not None else None

    @staticmethod
    def _public_drill_attempt(attempt) -> DrillAttemptView:
        target = (
            attempt.drill_spec.get("target_pattern")
            if isinstance(attempt.drill_spec, dict) else None
        )
        keys = ("mechanism", "trigger", "failure_behavior", "desired_behavior", "success_signal")
        if not isinstance(target, dict):
            target = {}
        return DrillAttemptView(
            attempt_id=attempt.id,
            question=attempt.question,
            target_pattern={
                key: target[key].strip()
                if isinstance(target.get(key), str) and target[key].strip()
                else "未记录"
                for key in keys
            },
        )
