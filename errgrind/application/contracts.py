"""Small, UI-neutral contracts for ErrGrind's reusable workflows."""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Optional

from ..models.types import DrillAttemptResult, DrillAttemptView, ErrorRecord


def public_error(error: Optional[ErrorRecord]) -> Optional[ErrorRecord]:
    """Return the frontend view without the private Grill audit ledger."""
    return replace(error, grilling_diagnostic_state=None) if error is not None else None


class ApplicationError(Exception):
    """Base error for a workflow that could not produce a business result."""


class ErrorNotFound(ApplicationError):
    pass


class InvalidWorkflowState(ApplicationError):
    pass


class WorkflowModelError(ApplicationError):
    """The model call failed after the recoverable conversation state was saved."""


class NoDrillContext(ApplicationError):
    """No completed Grill evidence is currently available for a Drill."""


class OutputContractError(ApplicationError):
    """A model response failed the workflow's local output contract."""


class WorkflowPersistenceError(ApplicationError):
    """A validated workflow result could not be saved."""


class GrillState(str, Enum):
    ACTIVE = "active"
    COMPLETE = "complete"
    READ_ONLY = "read-only"
    PAUSED = "paused"


@dataclass(frozen=True)
class NextStep:
    """A stable application-level recommendation code for a current Error."""

    code: str


def next_step_for_error(error: ErrorRecord) -> NextStep:
    """Choose the deterministic next action without exposing storage states."""
    if error.status == "pending-grill":
        conversation = error.grilling_conversation
        has_conversation = bool(conversation and conversation.strip())
        if has_conversation:
            try:
                import json
                has_conversation = bool(json.loads(conversation))
            except (TypeError, ValueError):
                # Preserve recoverability for an old/non-JSON partial record.
                has_conversation = True
        return NextStep(
            "resume_grill" if has_conversation else "start_grill"
        )
    if error.status == "pending-teach":
        return NextStep("start_teach")
    return NextStep("start_drill")


class DrillStage(str, Enum):
    SPEC = "spec"
    DRAFT = "draft"


@dataclass(frozen=True)
class ConversationResult:
    error: ErrorRecord
    messages: list[dict[str, str]]
    assistant_response: Optional[str]
    resumed: bool


@dataclass(frozen=True)
class GrillResult(ConversationResult):
    state: GrillState
    summary: Optional[str] = None


@dataclass(frozen=True)
class DrillPreparation:
    """The sanitized hand-off from historical errors to the Draft/Judge stages."""

    source_error_id: int
    drill_spec: dict[str, Any]
    question: str
    reference_answer: str


@dataclass(frozen=True)
class RecordDraft:
    """User-reviewable structure extracted from a raw record submission."""

    question: str
    user_thoughts: str
    reference_answer: str
    origin: str


@dataclass(frozen=True)
class DrillJudgment:
    is_correct: bool
    feedback: str
    attempt: DrillAttemptResult
