"""Drill's Spec -> sanitized spec -> Draft -> Judge workflow."""

import hashlib
import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from .contracts import (
    DrillJudgment,
    DrillPreparation,
    DrillStage,
    NoDrillContext,
    OutputContractError,
    WorkflowModelError,
    WorkflowPersistenceError,
)

if TYPE_CHECKING:
    from ..db.ops import Database
    from ..llm.prompts import PromptManager


DRILL_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "source_error_number": {"type": "integer"},
        "target_pattern": {
            "type": "object",
            "properties": {
                key: {"type": "string"}
                for key in (
                    "mechanism",
                    "trigger",
                    "failure_behavior",
                    "desired_behavior",
                    "success_signal",
                )
            },
            "required": [
                "mechanism",
                "trigger",
                "failure_behavior",
                "desired_behavior",
                "success_signal",
            ],
            "additionalProperties": False,
        },
        "new_problem": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "task_type": {"type": "string"},
                "setting": {"type": "string"},
                "task_goal": {"type": "string"},
                "essential_trigger": {"type": "string"},
                "solution_strategy": {"type": "string"},
                "avoid": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "domain",
                "task_type",
                "setting",
                "task_goal",
                "essential_trigger",
                "solution_strategy",
                "avoid",
            ],
            "additionalProperties": False,
        },
        "difficulty": {
            "type": "object",
            "properties": {
                "level": {"type": "integer"},
                "reasoning_depth": {"type": "integer"},
                "calculation_load": {"type": "integer"},
            },
            "required": ["level", "reasoning_depth", "calculation_load"],
            "additionalProperties": False,
        },
    },
    "required": ["source_error_number", "target_pattern", "new_problem", "difficulty"],
    "additionalProperties": False,
}

DRILL_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "reference_answer": {"type": "string"},
    },
    "required": ["question", "reference_answer"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_correct": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["is_correct", "feedback"],
    "additionalProperties": False,
}


def source_leak_terms(source_text):
    folded = source_text.casefold()
    terms = set(re.findall(r"[a-z_][a-z0-9_]{3,}", folded))
    # Workflow vocabulary and broad math functions are not source fingerprints.
    terms.difference_update(
        {
            "error",
            "pattern",
            "student",
            "grill",
            "sin",
            "cos",
            "tan",
            "cot",
            "sec",
            "csc",
            "log",
            "sqrt",
        }
    )
    terms.update(re.findall(r"\d{2,}(?:\.\d+)?", folded))
    terms.update(re.findall(r"[\u3400-\u9fff]{4,}", folded))
    for expression in re.findall(r"[a-z0-9_+\-*/^=().°√]+", folded):
        if len(expression) >= 6 and any(operator in expression for operator in "+-*/^="):
            terms.add(expression)
    return terms


def _required_text(data, key, label):
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}字段 {key} 必须是非空文本")
    return value.strip()


def _required_text_list(data, key, label):
    value = data[key]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label}字段 {key} 必须是文本列表")
    return [item.strip() for item in value]


def _bounded_int(data, key, label, minimum=1, maximum=5):
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}字段 {key} 必须是整数")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label}字段 {key} 必须在 {minimum}–{maximum} 之间")
    return value


def normalize_drill_spec(raw_spec, source_records):
    """Whitelist the only data that may reach Draft, and reject obvious leaks."""
    if not isinstance(raw_spec, dict):
        raise ValueError("DrillSpec 必须是 JSON 对象")
    source_number = raw_spec["source_error_number"]
    if isinstance(source_number, bool) or not isinstance(source_number, int):
        raise ValueError("source_error_number 必须是整数")
    if not 1 <= source_number <= len(source_records):
        raise ValueError("source_error_number 超出本次 Error 上下文范围")
    source_index = source_number - 1
    target = raw_spec["target_pattern"]
    new_problem = raw_spec["new_problem"]
    difficulty = raw_spec["difficulty"]
    if not all(
        isinstance(section, dict) for section in (target, new_problem, difficulty)
    ):
        raise ValueError("DrillSpec 的分组字段必须是 JSON 对象")
    task_type = _required_text(new_problem, "task_type", "new_problem")
    task_types = {
        "calculate",
        "simplify",
        "solve",
        "prove",
        "classify",
        "construct",
        "optimize",
        "explain",
        "determine_truth",
    }
    if task_type not in task_types:
        raise ValueError(f"new_problem.task_type 使用了未知枚举值 {task_type}")
    normalized = {
        "target_pattern": {
            key: _required_text(target, key, "target_pattern")
            for key in (
                "mechanism",
                "trigger",
                "failure_behavior",
                "desired_behavior",
                "success_signal",
            )
        },
        "new_problem": {
            "domain": _required_text(new_problem, "domain", "new_problem"),
            "task_type": task_type,
            "setting": _required_text(new_problem, "setting", "new_problem"),
            "task_goal": _required_text(new_problem, "task_goal", "new_problem"),
            "essential_trigger": _required_text(new_problem, "essential_trigger", "new_problem"),
            "solution_strategy": _required_text(new_problem, "solution_strategy", "new_problem"),
            "avoid": _required_text_list(new_problem, "avoid", "new_problem"),
        },
        "difficulty": {
            key: _bounded_int(difficulty, key, "difficulty")
            for key in ("level", "reasoning_depth", "calculation_load")
        },
    }
    public_text = json.dumps(normalized, ensure_ascii=False).casefold()
    if any(term in public_text for term in ("原题", "旧题", "历史题", "源题", "原始题目")):
        raise ValueError("公开 DrillSpec 只能正面描述新题，不能引用原题")
    source_texts = [record.question for record in source_records]
    source_texts.extend(
        record.grilling_summary
        for index, record in enumerate(source_records)
        if index != source_index
    )
    leaked_terms = {
        term
        for text in source_texts
        for term in source_leak_terms(text)
        if term in public_text
    }
    if leaked_terms:
        examples = "、".join(sorted(leaked_terms, key=len, reverse=True)[:3])
        raise ValueError(f"公开 DrillSpec 仍包含源 Error 特征文本: {examples}")
    return normalized, source_index


class DrillWorkflow:
    def __init__(self, db: "Database", llm, prompts: "PromptManager", cfg: dict):
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.cfg = cfg

    def prepare(
        self, *, on_stage: Callable[[DrillStage], None] | None = None
    ) -> DrillPreparation:
        if on_stage is not None:
            on_stage(DrillStage.SPEC)
        context = self.db.get_drill_context(self.cfg.get("drill_context_n", 10))
        if not context:
            raise NoDrillContext(
                "暂无可用于出题的 error，请先通过 /resume "
                "完成至少一条 error 的 grilling"
            )
        error_context = "\n\n".join(
            f"[Error {index + 1}]\n[原题]\n{item.question}\n[Grill 摘要]\n{item.grilling_summary}"
            for index, item in enumerate(context)
        )
        drill_spec, source_index = self._request_spec(error_context, context)
        if on_stage is not None:
            on_stage(DrillStage.DRAFT)
        question, reference_answer = self._generate_draft(drill_spec)
        return DrillPreparation(
            context[source_index].error_id,
            drill_spec,
            question,
            reference_answer,
        )

    def judge_and_record(
        self, preparation: DrillPreparation, user_response: str
    ) -> DrillJudgment:
        template = self.prompts.load("judge.md")
        prompt = template.format(
            question=preparation.question,
            reference_answer=preparation.reference_answer,
            user_response=user_response,
            target_pattern=preparation.drill_spec["target_pattern"]["mechanism"],
            success_signal=preparation.drill_spec["target_pattern"]["success_signal"],
        )
        try:
            judgment = self.llm.chat_json(
                [{"role": "user", "content": prompt}], output_schema=JUDGE_SCHEMA
            )
        except Exception as exc:
            raise WorkflowModelError(f"判分失败: {exc}") from exc
        is_correct, feedback = judgment.get("is_correct"), judgment.get("feedback")
        if not isinstance(is_correct, bool):
            raise OutputContractError("判分失败: is_correct 必须是 JSON 布尔值")
        if not isinstance(feedback, str):
            raise OutputContractError("判分失败: feedback 必须是文本")
        canonical_schema = json.dumps(
            JUDGE_SCHEMA,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            attempt = self.db.record_drill_attempt(
                preparation.source_error_id,
                preparation.drill_spec,
                preparation.question,
                preparation.reference_answer,
                user_response,
                is_correct,
                feedback.strip(),
                judge_provider=self.cfg.get("provider") or "unknown",
                judge_model=self.cfg.get("model") or "unknown",
                judge_prompt_sha256=hashlib.sha256(template.encode("utf-8")).hexdigest(),
                judge_schema_sha256=hashlib.sha256(canonical_schema.encode("utf-8")).hexdigest(),
            )
        except Exception as exc:
            raise WorkflowPersistenceError(f"保存演练结果失败: {exc}") from exc
        return DrillJudgment(is_correct, feedback.strip(), attempt)

    def _request_spec(self, error_context, context):
        messages = [
            {
                "role": "user",
                "content": self.prompts.load("drill_spec.md").format(
                    error_context=error_context
                ),
            }
        ]
        for attempt in range(3):
            try:
                raw = self.llm.chat_json(messages, output_schema=DRILL_SPEC_SCHEMA)
            except Exception as exc:
                raise WorkflowModelError(f"提炼出题规格失败: {exc}") from exc
            try:
                return normalize_drill_spec(raw, context)
            except (KeyError, TypeError, ValueError) as error:
                if attempt == 2:
                    raise OutputContractError(f"提炼出题规格失败: {error}") from error
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": json.dumps(raw, ensure_ascii=False),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"上次输出不符合 DrillSpec 契约：{error}。"
                                "修复字段、类型或泄漏问题后重新输出完整 JSON；"
                                "不要解释修改过程。"
                            ),
                        },
                    ]
                )

    def _generate_draft(self, drill_spec):
        messages = [
            {
                "role": "user",
                "content": self.prompts.load("drill.md").format(
                    drill_spec=json.dumps(drill_spec, ensure_ascii=False, indent=2)
                ),
            }
        ]
        for attempt in range(2):
            try:
                draft = self.llm.chat_json(messages, output_schema=DRILL_DRAFT_SCHEMA)
            except Exception as exc:
                raise WorkflowModelError(f"生成题目失败: {exc}") from exc
            try:
                return (
                    _required_text(draft, "question", "出题结果"),
                    _required_text(draft, "reference_answer", "出题结果"),
                )
            except (KeyError, TypeError, ValueError) as error:
                if attempt == 1:
                    raise OutputContractError(f"生成题目失败: {error}") from error
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": json.dumps(draft, ensure_ascii=False),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"上次出题结果不符合输出契约：{error}。"
                                "只重新输出包含非空 question 和 "
                                "reference_answer 的完整 JSON。"
                            ),
                        },
                    ]
                )
