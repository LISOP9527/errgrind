from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class ErrorRecord:
    id: int
    status: str
    origin: str
    source_error_id: Optional[int]
    source_drill_attempt_id: Optional[int]
    question: str
    user_thoughts: Optional[str]
    reference_answer: Optional[str]
    grilling_conversation: Optional[str]
    grilling_summary: Optional[str]
    teach_conversation: Optional[str]
    created_at: datetime
    updated_at: datetime
    grilling_diagnostic_state: Optional[str] = None


@dataclass
class DrillAttempt:
    id: int
    source_error_id: int
    drill_spec: dict[str, Any]
    question: str
    reference_answer: str
    user_response: str
    is_correct: bool
    feedback: str
    judge_provider: str
    judge_model: str
    judge_prompt_sha256: str
    judge_schema_sha256: str
    derived_error_id: Optional[int]
    created_at: datetime


@dataclass(frozen=True)
class DrillAttemptResult:
    attempt_id: int
    derived_error_id: Optional[int]


@dataclass(frozen=True)
class DrillAttemptView:
    """最小的、可供前端查看已判分 Drill 历史的公开结果。"""

    attempt_id: int
    question: str
    target_pattern: dict[str, str]


@dataclass
class DrillContext:
    error_id: int
    question: str
    grilling_summary: str
