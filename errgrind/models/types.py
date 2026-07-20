from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ErrorRecord:
    id: int
    status: str
    question: str
    user_thoughts: Optional[str]
    reference_answer: Optional[str]
    grilling_conversation: Optional[str]
    grilling_summary: Optional[str]
    teach_conversation: Optional[str]
    created_at: datetime
    updated_at: datetime
