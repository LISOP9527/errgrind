"""The small application facade used by every frontend."""

from collections.abc import Callable
from typing import Optional
from ..llm.usage import usage_action, usage_scope

from .contracts import (
    ConversationResult,
    DrillJudgment,
    DrillPreparation,
    DrillStage,
    GrillResult,
    OutputContractError,
    WorkflowModelError,
    public_error,
)
from ..models.types import DrillAttemptView
from .drill import DrillWorkflow
from .grill import GrillWorkflow
from .teach import TeachWorkflow


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
        }
        if field not in fields:
            raise OutputContractError("不支持的录题字段")
        transcribe = getattr(self.llm, "transcribe_image", None)
        if not callable(transcribe):
            raise WorkflowModelError("当前 AI provider 不支持字段图片识别，请切换 provider")
        try:
            prompt = self.prompts.load("ocr_field.md").format(field=fields[field])
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
