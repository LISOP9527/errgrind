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
    WorkflowPersistenceError,
    OutputContractError,
    WorkflowModelError,
    public_error,
)
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

    @usage_action("record", "ocr_field")
    def transcribe_record_field(self, image_path: str, field: str) -> str:
        """Return an unpersisted OCR draft for one field, for human review."""
        from ..llm.ocr import OcrError

        fields = {
            "question": "题目",
            "user_thoughts": "用户当时的思路或作答",
            "reference_answer": "参考答案",
        }
        if field not in fields:
            raise OutputContractError("不支持的录题字段")
        transcribe = getattr(self.llm, "transcribe_image", None)
        if not callable(transcribe):
            raise WorkflowModelError("当前 AI provider 不支持字段图片识别，请切换 provider")
        try:
            prompt = self.prompts.load("ocr_field.md").format(field=fields[field])
            with usage_scope(stage="ocr_" + field):
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
