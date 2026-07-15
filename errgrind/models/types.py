from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ErrorCategory(str, Enum):
    CONCEPT_MISUNDERSTANDING = "概念误解"
    KNOWLEDGE_BLIND_SPOT = "知识盲区"
    CARELESS = "粗心"
    QUESTION_COMPREHENSION = "题干理解偏差"
    WRONG_DIRECTION = "思考方向错误"
    SUBOPTIMAL_METHOD = "思考方法不优"


@dataclass
class Session:
    id: int
    goal: str
    created_at: datetime
    summary: Optional[str] = None


@dataclass
class Question:
    id: int
    session_id: int
    content: str
    options: Optional[str]
    correct_answer: str
    source: str
    difficulty: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Attempt:
    id: int
    session_id: int
    question_id: int
    user_answer: str
    is_correct: bool
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ErrorRecord:
    id: int
    session_id: int
    attempt_id: int
    raw_conversation: str
    compressed_summary: str
    categories: str
    created_at: datetime = field(default_factory=datetime.now)
