from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from ..db.ops import Database
    from ..llm.client import LLMClient
    from ..llm.codex import CodexClient
    from ..llm.gemini import GeminiClient
    from ..llm.prompts import PromptManager


@dataclass
class AppState:
    db: Optional["Database"] = None
    llm: Optional[Union["GeminiClient", "LLMClient", "CodexClient"]] = None
    prompts: Optional["PromptManager"] = None
    cfg: dict = field(default_factory=dict)

    current_error_id: Optional[int] = None
    accessed_error_ids: set = field(default_factory=set)
