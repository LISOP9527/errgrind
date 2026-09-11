"""The small application facade used by every frontend."""

from collections.abc import Callable
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
        user_thoughts: str,
        reference_answer: Optional[str] = None,
        *,
        origin: str = "record",
    ):
        """Validate and persist one user-recorded Error.

        This is deliberately small: frontends collect and review text, while
        the application boundary owns validation, provenance, and the public
        (diagnostic-state-free) return value.
        """
        if not isinstance(question, str) or not question.strip():
            raise OutputContractError("题目不能为空")
        if not isinstance(user_thoughts, str) or not user_thoughts.strip():
            raise OutputContractError("用户思路不能为空")
        if reference_answer is not None and not isinstance(reference_answer, str):
            raise OutputContractError("参考答案必须是文字")
        if origin not in {"record", "ocr"}:
            raise OutputContractError("录题来源必须是 record 或 ocr")

        answer = reference_answer.strip() if reference_answer is not None else None
        answer = answer or None
        try:
            error_id = self.db.create_error(
                question.strip(), user_thoughts.strip(), answer, origin=origin
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
        self, raw_input: str, image_paths: Optional[list[str]] = None
    ) -> RecordDraft:
        """Organize supplied record material into a draft for human review.

        The prompt explicitly treats user thoughts as optional source material;
        an empty thoughts field remains empty and is rejected only when the
        user tries to confirm the Error.
        """
        if not isinstance(raw_input, str):
            raise OutputContractError("录入内容必须是文字")
        image_paths = list(image_paths or [])
        if not raw_input.strip() and not image_paths:
            raise OutputContractError("请先输入内容或添加图片")
        materials = []
        if raw_input.strip():
            materials.append("[用户输入的原始内容]\n" + raw_input.strip())
        for index, image_path in enumerate(image_paths, start=1):
            text = self.transcribe_record_input(image_path)
            materials.append(f"[图片 {index} 的忠实转录]\n{text}")
        source_text = "\n\n".join(materials)
        prompt = self.prompts.load("record_draft.md").format(source_text=source_text)
        try:
            with usage_scope(action="record", stage="draft"):
                raw = self.llm.chat_json(
                    [{"role": "user", "content": prompt}],
                    output_schema=RECORD_DRAFT_SCHEMA,
                )
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as exc:
            raise WorkflowModelError("整理录入草稿失败，请保留原始内容后重试") from exc
        if not isinstance(raw, dict):
            raise OutputContractError("录入草稿必须是 JSON 对象")
        values = {}
        for field in ("question", "user_thoughts", "reference_answer"):
            value = raw.get(field, "")
            if not isinstance(value, str):
                raise OutputContractError(f"录入草稿字段 {field} 必须是文本")
            values[field] = value.strip()
        if not values["question"]:
            raise OutputContractError("草稿中没有题目，请补充题目后再确认")
        return RecordDraft(
            question=values["question"],
            user_thoughts=values["user_thoughts"],
            reference_answer=values["reference_answer"],
            origin="ocr" if image_paths else "record",
        )

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
        on_token: Optional[Callable[[str], None]] = None,
    ) -> GrillResult:
        return GrillWorkflow(self.db, self.llm, self.prompts).submit_answer(
            error_id, answer, on_token=on_token
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
        before_model_call: Optional[Callable[[], None]] = None,
    ) -> ConversationResult:
        return TeachWorkflow(self.db, self.llm, self.prompts).submit_answer(
            error_id, answer, before_model_call=before_model_call
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
        self, preparation: DrillPreparation, user_response: str
    ) -> DrillJudgment:
        return DrillWorkflow(
            self.db, self.llm, self.prompts, self.cfg
        ).judge_and_record(preparation, user_response)

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
