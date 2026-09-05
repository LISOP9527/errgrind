"""The small application facade used by every frontend."""

from collections.abc import Callable
from typing import Optional

from .contracts import (
    ConversationResult,
    DrillJudgment,
    DrillPreparation,
    DrillStage,
    GrillResult,
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

    def delete_error(self, error_id: int) -> None:
        self.db.delete_error(error_id)

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
