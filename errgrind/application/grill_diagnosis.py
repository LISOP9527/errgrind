"""Pure contracts and deterministic merge rules for one Grill diagnosis.

The state in this module belongs to one Error only.  It is deliberately not a
learner model, a Pattern store, or a provider-specific response object.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Any, Mapping, Sequence

from .contracts import OutputContractError


DIAGNOSIS_STATE_VERSION = 1
HYPOTHESIS_STATUSES = frozenset(
    {"plausible", "supported", "weakened", "rejected"}
)
DIAGNOSIS_STATUSES = frozenset({"active", "supported", "undetermined"})
NEXT_ACTIONS = frozenset(
    {
        "reasoning_question",
        "variant_problem",
        "finish_supported",
        "finish_undetermined",
    }
)
PROBE_TYPES = frozenset({"reasoning_question", "variant_problem"})
_MESSAGE_REF_RE = re.compile(r"^message:([0-9]+)$")


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_PREDICTION_SCHEMA = _object_schema(
    {
        "hypothesis_id": {"type": "string"},
        "expected_observation": {"type": "string"},
    },
    ["hypothesis_id", "expected_observation"],
)

_PROBE_SCHEMA = _object_schema(
    {
        "question": {"type": "string"},
        "target_hypothesis_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "discrimination_goal": {"type": "string"},
        "predictions": {"type": "array", "items": _PREDICTION_SCHEMA},
        "answer_key": {"type": "string"},
        "preserved_mechanism": {"type": "string"},
        "surface_change": {"type": "string"},
    },
    [
        "question",
        "target_hypothesis_ids",
        "discrimination_goal",
        "predictions",
        "answer_key",
        "preserved_mechanism",
        "surface_change",
    ],
)

GRILL_TURN_SCHEMA: dict[str, Any] = _object_schema(
    {
        "new_hypotheses": {
            "type": "array",
            "items": _object_schema(
                {"id": {"type": "string"}, "claim": {"type": "string"}},
                ["id", "claim"],
            ),
        },
        "hypothesis_status_updates": {
            "type": "array",
            "items": _object_schema(
                {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": sorted(HYPOTHESIS_STATUSES),
                    },
                },
                ["id", "status"],
            ),
        },
        "new_evidence": {
            "type": "array",
            "items": _object_schema(
                {
                    "source_ref": {"type": "string"},
                    "quote": {"type": "string"},
                    "interpretation": {"type": "string"},
                    "supports": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "contradicts": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "probe_id": {"type": "string"},
                },
                [
                    "source_ref",
                    "quote",
                    "interpretation",
                    "supports",
                    "contradicts",
                    "probe_id",
                ],
            ),
        },
        "next_action": {"type": "string", "enum": sorted(NEXT_ACTIONS)},
        "probe": _PROBE_SCHEMA,
        "best_hypothesis_id": {"type": "string"},
        "remaining_uncertainty": {"type": "string"},
        "what_would_change_judgment": {"type": "string"},
        "summary": {"type": "string"},
    },
    [
        "new_hypotheses",
        "hypothesis_status_updates",
        "new_evidence",
        "next_action",
        "probe",
        "best_hypothesis_id",
        "remaining_uncertainty",
        "what_would_change_judgment",
        "summary",
    ],
)


@dataclass(frozen=True)
class GrillTurnDecision:
    """Validated, one-turn delta returned by a Grill model call."""

    new_hypotheses: list[dict[str, Any]]
    hypothesis_status_updates: list[dict[str, Any]]
    new_evidence: list[dict[str, Any]]
    next_action: str
    probe: dict[str, Any]
    best_hypothesis_id: str
    remaining_uncertainty: str
    what_would_change_judgment: str
    summary: str


def _fail(message: str) -> None:
    raise OutputContractError(f"Grill 输出契约错误: {message}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} 必须是 JSON 对象")
    return dict(value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        _fail(f"{label} 字段不匹配；缺少 {sorted(missing)}，多出 {sorted(extra)}")


def _text(value: Any, label: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(f"{label} 必须是文本")
    if nonempty and not value.strip():
        _fail(f"{label} 必须是非空文本")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(f"{label} 必须是文本数组")
    if len(set(value)) != len(value):
        _fail(f"{label} 不得包含重复 ID")
    return list(value)


def _valid_id(value: Any, label: str, prefix: str) -> str:
    value = _text(value, label, nonempty=True)
    if not re.fullmatch(rf"{prefix}[1-9][0-9]*", value):
        _fail(f"{label} 必须使用稳定的 {prefix}1、{prefix}2... ID")
    return value


def empty_diagnostic_state() -> dict[str, Any]:
    """Return the exact persisted shape for a new episode diagnosis."""
    return {
        "version": DIAGNOSIS_STATE_VERSION,
        "diagnosis_status": "active",
        "hypotheses": [],
        "evidence": [],
        "probes": [],
        "current_probe_id": "",
        "best_hypothesis_id": "",
        "remaining_uncertainty": "",
        "what_would_change_judgment": "",
    }


def _validate_state(state: Mapping[str, Any]) -> dict[str, Any]:
    expected = set(empty_diagnostic_state())
    _exact_keys(state, expected, "diagnostic state")
    result = deepcopy(dict(state))
    version = result["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        _fail("diagnostic state.version 必须为 1")
    diagnosis_status = _text(
        result["diagnosis_status"], "diagnostic state.diagnosis_status", nonempty=True
    )
    if diagnosis_status not in DIAGNOSIS_STATUSES:
        _fail("diagnostic state.diagnosis_status 无效")
    result["diagnosis_status"] = diagnosis_status

    for key in ("hypotheses", "evidence", "probes"):
        if not isinstance(result[key], list):
            _fail(f"diagnostic state.{key} 必须是数组")

    hypotheses: dict[str, dict[str, Any]] = {}
    for item in result["hypotheses"]:
        item = _mapping(item, "state hypothesis")
        _exact_keys(item, {"id", "claim", "status"}, "state hypothesis")
        ident = _valid_id(item["id"], "hypothesis.id", "H")
        claim = _text(item["claim"], "hypothesis.claim", nonempty=True)
        status = _text(item["status"], "hypothesis.status", nonempty=True)
        if status not in HYPOTHESIS_STATUSES:
            _fail(f"不支持的 hypothesis status: {status}")
        if ident in hypotheses:
            _fail(f"state 中的 hypothesis {ident} 重复")
        hypotheses[ident] = {"id": ident, "claim": claim, "status": status}
    result["hypotheses"] = list(hypotheses.values())

    probes: dict[str, dict[str, Any]] = {}
    for item in result["probes"]:
        item = _mapping(item, "state probe")
        expected_probe = {
            "id",
            "type",
            "question",
            "target_hypothesis_ids",
            "discrimination_goal",
            "predictions",
            "answer_key",
            "preserved_mechanism",
            "surface_change",
        }
        _exact_keys(item, expected_probe, "state probe")
        ident = _valid_id(item["id"], "probe.id", "P")
        if ident in probes:
            _fail(f"state 中的 probe {ident} 重复")
        probe = _validate_probe_payload(item, hypotheses, "state probe")
        probe["id"] = ident
        probe["type"] = item["type"]
        probes[ident] = probe
    result["probes"] = list(probes.values())

    current_probe_id = _text(result["current_probe_id"], "current_probe_id")
    if current_probe_id and current_probe_id not in probes:
        _fail("current_probe_id 必须指向已有 probe")
    if result["diagnosis_status"] != "active" and current_probe_id:
        _fail("已完成 diagnosis 的 current_probe_id 必须为空字符串")
    result["current_probe_id"] = current_probe_id

    best = _text(result["best_hypothesis_id"], "best_hypothesis_id")
    if best and best not in hypotheses:
        _fail("best_hypothesis_id 必须指向已有 hypothesis")
    if best and hypotheses[best]["status"] == "rejected":
        _fail("best_hypothesis_id 不能指向 rejected hypothesis")
    result["best_hypothesis_id"] = best

    result["remaining_uncertainty"] = _text(
        result["remaining_uncertainty"], "remaining_uncertainty"
    )
    result["what_would_change_judgment"] = _text(
        result["what_would_change_judgment"], "what_would_change_judgment"
    )
    if result["diagnosis_status"] == "supported":
        if not best or hypotheses[best]["status"] != "supported":
            _fail("supported diagnosis 必须指向 supported best hypothesis")
    if result["diagnosis_status"] == "undetermined":
        if best or not result["remaining_uncertainty"].strip():
            _fail("undetermined diagnosis 必须保留不确定性且没有 best hypothesis")

    evidence_result = []
    evidence_ids: set[str] = set()
    for item in result["evidence"]:
        item = _mapping(item, "state evidence")
        expected_evidence = {
            "id",
            "source_ref",
            "quote",
            "interpretation",
            "supports",
            "contradicts",
            "probe_id",
        }
        _exact_keys(item, expected_evidence, "state evidence")
        ident = _valid_id(item["id"], "evidence.id", "E")
        if ident in evidence_ids:
            _fail(f"state 中的 evidence {ident} 重复")
        evidence_ids.add(ident)
        source_ref = _text(item["source_ref"], "evidence.source_ref", nonempty=True)
        if source_ref != "initial_user_thoughts" and _MESSAGE_REF_RE.fullmatch(source_ref) is None:
            _fail("state evidence.source_ref 只能是 initial_user_thoughts 或 message:N")
        quote = _text(item["quote"], "evidence.quote", nonempty=True)
        interpretation = _text(
            item["interpretation"], "evidence.interpretation", nonempty=True
        )
        supports = _string_list(item["supports"], "evidence.supports")
        contradicts = _string_list(item["contradicts"], "evidence.contradicts")
        _validate_hypothesis_refs(supports, contradicts, hypotheses, "state evidence")
        probe_id = _text(item["probe_id"], "evidence.probe_id")
        if probe_id and probe_id not in probes:
            _fail("evidence.probe_id 必须为空或指向已有 probe")
        evidence_result.append(
            {
                "id": ident,
                "source_ref": source_ref,
                "quote": quote,
                "interpretation": interpretation,
                "supports": supports,
                "contradicts": contradicts,
                "probe_id": probe_id,
            }
        )
    result["evidence"] = evidence_result
    return result


def load_diagnostic_state(raw: Any) -> dict[str, Any] | None:
    """Decode and validate persisted state; NULL remains a meaningful NULL."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            _fail(f"diagnostic state 不是合法 JSON: {exc}")
    return _validate_state(_mapping(raw, "diagnostic state"))


def _addressable_messages(
    messages: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        _fail("grilling_conversation 必须是消息数组")
    result: dict[str, str] = {}
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str) or _is_bootstrap_user_message(messages, index):
            continue
        result[f"message:{index}"] = content
    return result


def _is_bootstrap_user_message(
    messages: Sequence[Mapping[str, Any]], index: int
) -> bool:
    """Identify only the initial bootstrap, not a later identical answer."""
    if index < 0 or index >= len(messages):
        return False
    message = messages[index]
    if not isinstance(message, Mapping) or message.get("role") != "user":
        return False
    if message.get("content") != "开始吧":
        return False
    return not any(
        isinstance(previous, Mapping) and previous.get("role") == "user"
        for previous in messages[:index]
    )


def _state_hypotheses(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in state["hypotheses"]}


def _validate_hypothesis_refs(
    supports: list[str],
    contradicts: list[str],
    hypotheses: Mapping[str, Mapping[str, Any]],
    label: str,
) -> None:
    if set(supports) & set(contradicts):
        _fail(f"{label} 的 supports 和 contradicts 不能包含同一 hypothesis")
    refs = set(supports) | set(contradicts)
    missing = refs - set(hypotheses)
    if missing:
        _fail(f"{label} 引用了不存在的 hypothesis: {sorted(missing)}")


def _validate_probe_payload(
    probe: Mapping[str, Any],
    hypotheses: Mapping[str, Mapping[str, Any]],
    label: str,
) -> dict[str, Any]:
    expected = {
        "id",
        "type",
        "question",
        "target_hypothesis_ids",
        "discrimination_goal",
        "predictions",
        "answer_key",
        "preserved_mechanism",
        "surface_change",
    }
    _exact_keys(probe, expected, label)
    probe_type = _text(probe["type"], f"{label}.type", nonempty=True)
    if probe_type not in PROBE_TYPES:
        _fail(f"{label}.type 无效")
    question = _text(probe["question"], f"{label}.question")
    goal = _text(probe["discrimination_goal"], f"{label}.discrimination_goal")
    if not question.strip() or not goal.strip():
        _fail(f"{label} 的 question 和 discrimination_goal 必须是非空文本")
    targets = _string_list(probe["target_hypothesis_ids"], f"{label}.target_hypothesis_ids")
    if not targets:
        _fail(f"{label} 至少需要一个 target hypothesis")
    if any(target not in hypotheses for target in targets):
        _fail(f"{label}.target_hypothesis_ids 引用了不存在的 hypothesis")
    hidden = {
        "answer_key": _text(probe["answer_key"], f"{label}.answer_key"),
        "preserved_mechanism": _text(
            probe["preserved_mechanism"], f"{label}.preserved_mechanism"
        ),
        "surface_change": _text(probe["surface_change"], f"{label}.surface_change"),
    }
    predictions = probe["predictions"]
    if not isinstance(predictions, list):
        _fail(f"{label}.predictions 必须是数组")
    prediction_result = []
    prediction_ids: set[str] = set()
    for item in predictions:
        item = _mapping(item, f"{label}.prediction")
        _exact_keys(item, {"hypothesis_id", "expected_observation"}, f"{label}.prediction")
        hypothesis_id = _text(item["hypothesis_id"], "prediction.hypothesis_id", nonempty=True)
        expected_observation = _text(
            item["expected_observation"], "prediction.expected_observation", nonempty=True
        )
        if hypothesis_id in prediction_ids:
            _fail(f"{label}.predictions 不得重复 hypothesis")
        prediction_ids.add(hypothesis_id)
        prediction_result.append(
            {
                "hypothesis_id": hypothesis_id,
                "expected_observation": expected_observation,
            }
        )
    if set(item["hypothesis_id"] for item in prediction_result) != set(targets):
        _fail(f"{label}.predictions 必须覆盖每个 target hypothesis")
    if probe_type == "variant_problem" and len(targets) < 2:
        _fail(f"{label} 的 variant_problem 至少需要两个 target hypotheses")
    if probe_type == "variant_problem" and not all(hidden.values()):
        _fail(f"{label} 的 variant hidden fields 必须非空")
    if probe_type == "reasoning_question" and any(hidden.values()):
        _fail(f"{label} 的 reasoning_question 不得填写 variant hidden fields")
    return {
        "type": probe_type,
        "question": question,
        "target_hypothesis_ids": targets,
        "discrimination_goal": goal,
        "predictions": prediction_result,
        **hidden,
    }


def _validate_decision_probe(
    probe: Any,
    action: str,
    hypotheses: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    probe = _mapping(probe, "probe")
    expected = {
        "question",
        "target_hypothesis_ids",
        "discrimination_goal",
        "predictions",
        "answer_key",
        "preserved_mechanism",
        "surface_change",
    }
    _exact_keys(probe, expected, "probe")
    normalized = {
        "type": "variant_problem" if action == "variant_problem" else "reasoning_question",
        "question": _text(probe["question"], "probe.question"),
        "target_hypothesis_ids": _string_list(
            probe["target_hypothesis_ids"], "probe.target_hypothesis_ids"
        ),
        "discrimination_goal": _text(
            probe["discrimination_goal"], "probe.discrimination_goal"
        ),
        "predictions": probe["predictions"],
        "answer_key": _text(probe["answer_key"], "probe.answer_key"),
        "preserved_mechanism": _text(
            probe["preserved_mechanism"], "probe.preserved_mechanism"
        ),
        "surface_change": _text(probe["surface_change"], "probe.surface_change"),
    }
    if not normalized["question"].strip():
        _fail("probe.question 必须是非空文本")
    if not normalized["discrimination_goal"].strip():
        _fail("probe.discrimination_goal 必须是非空文本")
    targets = normalized["target_hypothesis_ids"]
    if not targets:
        _fail("probe 至少需要一个 target hypothesis")
    if any(target not in hypotheses for target in targets):
        _fail("probe target_hypothesis_ids 引用了不存在的 hypothesis")
    if any(hypotheses[target]["status"] == "rejected" for target in targets):
        _fail("probe 不能把 rejected hypothesis 作为当前诊断 target")

    predictions = normalized["predictions"]
    if not isinstance(predictions, list):
        _fail("probe.predictions 必须是数组")
    pred_ids: set[str] = set()
    normalized_predictions = []
    for item in predictions:
        item = _mapping(item, "probe prediction")
        _exact_keys(item, {"hypothesis_id", "expected_observation"}, "probe prediction")
        ident = _text(item["hypothesis_id"], "prediction.hypothesis_id", nonempty=True)
        if ident not in targets:
            _fail("prediction 必须覆盖 probe 的 target hypothesis")
        if ident in pred_ids:
            _fail("prediction 不得重复 hypothesis")
        pred_ids.add(ident)
        normalized_predictions.append(
            {
                "hypothesis_id": ident,
                "expected_observation": _text(
                    item["expected_observation"],
                    "prediction.expected_observation",
                    nonempty=True,
                ),
            }
        )
    if pred_ids != set(targets):
        _fail("probe.predictions 必须覆盖每个 target hypothesis")
    if action == "variant_problem":
        if len(targets) < 2:
            _fail("variant_problem 至少需要两个 target hypotheses")
        for key in ("answer_key", "preserved_mechanism", "surface_change"):
            if not normalized[key].strip():
                _fail(f"variant_problem 的 {key} 必须是非空文本")
    else:
        if any(normalized[key] for key in ("answer_key", "preserved_mechanism", "surface_change")):
            _fail("reasoning_question 不得填写 variant hidden fields")
    normalized["predictions"] = normalized_predictions
    return normalized


def _decision_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, GrillTurnDecision):
        return {field.name: getattr(raw, field.name) for field in fields(raw)}
    return _mapping(raw, "Grill turn decision")


def validate_turn_decision(
    raw: Any,
    state: Any,
    messages: Sequence[Mapping[str, Any]],
    initial_user_thoughts: str | None = None,
    require_latest_user_evidence: bool = False,
) -> GrillTurnDecision:
    """Validate one model delta against the current state and user messages."""
    decision = _decision_mapping(raw)
    expected = set(GRILL_TURN_SCHEMA["required"])
    _exact_keys(decision, expected, "Grill turn decision")
    current_state = load_diagnostic_state(state) or empty_diagnostic_state()
    hypotheses = _state_hypotheses(current_state)

    if not isinstance(decision["new_hypotheses"], list):
        _fail("new_hypotheses 必须是数组")
    new_hypotheses = []
    for item in decision["new_hypotheses"]:
        item = _mapping(item, "new_hypotheses item")
        _exact_keys(item, {"id", "claim"}, "new_hypotheses item")
        ident = _valid_id(item["id"], "new_hypotheses.id", "H")
        claim = _text(item["claim"], "new_hypotheses.claim", nonempty=True)
        if ident in hypotheses or any(existing["id"] == ident for existing in new_hypotheses):
            _fail(f"hypothesis {ident} 已存在，不能改写旧 claim")
        if any(existing.get("claim") == claim for existing in hypotheses.values()):
            _fail("不能重复添加相同 claim 的 hypothesis")
        hypotheses[ident] = {"id": ident, "claim": claim, "status": "plausible"}
        new_hypotheses.append({"id": ident, "claim": claim})
    status_updates = []
    status_update_ids: set[str] = set()
    if not isinstance(decision["hypothesis_status_updates"], list):
        _fail("hypothesis_status_updates 必须是数组")
    for item in decision["hypothesis_status_updates"]:
        item = _mapping(item, "hypothesis_status_updates item")
        _exact_keys(item, {"id", "status"}, "hypothesis_status_updates item")
        ident = _valid_id(item["id"], "status update.id", "H")
        status = _text(item["status"], "status update.status", nonempty=True)
        if ident not in hypotheses:
            _fail(f"status update 引用了不存在的 hypothesis: {ident}")
        if status not in HYPOTHESIS_STATUSES:
            _fail(f"不支持的 hypothesis status: {status}")
        if ident in status_update_ids:
            _fail(f"hypothesis {ident} 的 status update 重复")
        status_update_ids.add(ident)
        status_updates.append({"id": ident, "status": status})
        hypotheses[ident]["status"] = status

    if not hypotheses:
        _fail("继续诊断前至少需要一个 hypothesis")

    if not isinstance(decision["new_evidence"], list):
        _fail("new_evidence 必须是数组")
    addressable = _addressable_messages(messages)
    initial = initial_user_thoughts if isinstance(initial_user_thoughts, str) else None
    known_probes = {
        item["id"] for item in current_state["probes"] if isinstance(item, Mapping)
    }
    new_evidence = []
    for item in decision["new_evidence"]:
        item = _mapping(item, "new_evidence item")
        expected_evidence = {
            "source_ref",
            "quote",
            "interpretation",
            "supports",
            "contradicts",
            "probe_id",
        }
        _exact_keys(item, expected_evidence, "new_evidence item")
        source_ref = _text(item["source_ref"], "evidence.source_ref", nonempty=True)
        quote = _text(item["quote"], "evidence.quote", nonempty=True)
        interpretation = _text(
            item["interpretation"], "evidence.interpretation", nonempty=True
        )
        supports = _string_list(item["supports"], "evidence.supports")
        contradicts = _string_list(item["contradicts"], "evidence.contradicts")
        _validate_hypothesis_refs(supports, contradicts, hypotheses, "new evidence")
        if source_ref == "initial_user_thoughts":
            if initial is None or quote not in initial:
                _fail("initial_user_thoughts 的 quote 必须是精确 substring")
        else:
            match = _MESSAGE_REF_RE.fullmatch(source_ref)
            if match is None or source_ref not in addressable:
                _fail(
                    "evidence source_ref 只能指向 initial_user_thoughts 或真实 user message:N"
                )
            if quote not in addressable[source_ref]:
                _fail("user message 的 quote 必须是精确 substring")
        probe_id = _text(item["probe_id"], "evidence.probe_id")
        if probe_id and probe_id not in known_probes:
            _fail("evidence.probe_id 必须为空或指向已有 probe")
        new_evidence.append(
            {
                "source_ref": source_ref,
                "quote": quote,
                "interpretation": interpretation,
                "supports": supports,
                "contradicts": contradicts,
                "probe_id": probe_id,
            }
        )

    action = _text(decision["next_action"], "next_action", nonempty=True)
    if action not in NEXT_ACTIONS:
        _fail(f"不支持的 next_action: {action}")
    if action in {"finish_supported", "finish_undetermined"}:
        probe = _mapping(decision["probe"], "probe")
        empty_probe = {
            "question": "",
            "target_hypothesis_ids": [],
            "discrimination_goal": "",
            "predictions": [],
            "answer_key": "",
            "preserved_mechanism": "",
            "surface_change": "",
        }
        _exact_keys(probe, set(empty_probe), "probe")
        if probe != empty_probe:
            _fail("finish action 的 probe 必须使用空字符串和空数组")
        probe = empty_probe
    else:
        probe = _validate_decision_probe(decision["probe"], action, hypotheses)
    best = _text(decision["best_hypothesis_id"], "best_hypothesis_id")
    if best and best not in hypotheses:
        _fail("best_hypothesis_id 必须指向已有 hypothesis")
    if best and hypotheses[best]["status"] == "rejected":
        _fail("best_hypothesis_id 不能指向 rejected hypothesis")
    remaining = _text(decision["remaining_uncertainty"], "remaining_uncertainty")
    what_changes = _text(
        decision["what_would_change_judgment"], "what_would_change_judgment"
    )
    summary = _text(decision["summary"], "summary")

    if action in {"reasoning_question", "variant_problem"}:
        if summary != "":
            _fail("继续提问时 summary 必须为空字符串")
    elif action == "finish_supported":
        if not best:
            _fail("finish_supported 必须提供 best_hypothesis_id")
        if hypotheses[best]["status"] != "supported":
            _fail("finish_supported 的 best hypothesis status 必须是 supported")
        if not (current_state["evidence"] or new_evidence):
            _fail("finish_supported 必须有至少一条 Evidence")
        if "当前最受 Evidence 支持的解释" not in summary:
            _fail("finish_supported 的 summary 必须说明当前最受 Evidence 支持的解释")
    else:
        if best:
            _fail("finish_undetermined 的 best_hypothesis_id 必须为空字符串")
        if not remaining.strip():
            _fail("finish_undetermined 必须说明 remaining_uncertainty")
        if "当前 Evidence 还不能可靠区分主要解释" not in summary:
            _fail("finish_undetermined 的 summary 必须明确当前 Evidence 还不能可靠区分主要解释")

    if require_latest_user_evidence:
        latest = list(addressable)[-1] if addressable else None
        if latest is None or not any(item["source_ref"] == latest for item in new_evidence):
            _fail("每次真实用户回答都必须新增一条引用最新 user message 的 Evidence")
        current_probe_id = current_state["current_probe_id"]
        if current_probe_id:
            current_probe = next(
                item
                for item in current_state["probes"]
                if item["id"] == current_probe_id
            )
            if (
                current_probe["type"] == "variant_problem"
                and not any(item["probe_id"] == current_probe_id for item in new_evidence)
            ):
                _fail("variant 回答的 Evidence 必须关联当前 variant probe")

    return GrillTurnDecision(
        new_hypotheses=new_hypotheses,
        hypothesis_status_updates=status_updates,
        new_evidence=new_evidence,
        next_action=action,
        probe=probe,
        best_hypothesis_id=best,
        remaining_uncertainty=remaining,
        what_would_change_judgment=what_changes,
        summary=summary,
    )


def apply_turn_decision(
    state: Any,
    decision: GrillTurnDecision | Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministically merge a validated delta without rewriting history."""
    result = load_diagnostic_state(state) or empty_diagnostic_state()
    if not isinstance(decision, GrillTurnDecision):
        raw = _decision_mapping(decision)
        try:
            decision = GrillTurnDecision(**raw)
        except (TypeError, KeyError) as exc:
            _fail(f"decision 字段不完整: {exc}")

    hypotheses = _state_hypotheses(result)
    for item in decision.new_hypotheses:
        if item["id"] in hypotheses:
            _fail(f"不能改写已有 hypothesis {item['id']}")
        if any(existing["claim"] == item["claim"] for existing in result["hypotheses"]):
            _fail("不能重复添加相同 claim 的 hypothesis")
        result["hypotheses"].append(
            {"id": item["id"], "claim": item["claim"], "status": "plausible"}
        )
        hypotheses[item["id"]] = result["hypotheses"][-1]

    for update in decision.hypothesis_status_updates:
        if update["id"] not in hypotheses:
            _fail(f"不能更新不存在的 hypothesis {update['id']}")
        hypotheses[update["id"]]["status"] = update["status"]

    used_evidence_ids = {
        item["id"] for item in result["evidence"] if isinstance(item, Mapping)
    }
    evidence_number = 1
    for item in decision.new_evidence:
        while f"E{evidence_number}" in used_evidence_ids:
            evidence_number += 1
        result["evidence"].append(
            {
                "id": f"E{evidence_number}",
                "source_ref": item["source_ref"],
                "quote": item["quote"],
                "interpretation": item["interpretation"],
                "supports": list(item["supports"]),
                "contradicts": list(item["contradicts"]),
                "probe_id": item["probe_id"],
            }
        )
        used_evidence_ids.add(f"E{evidence_number}")
        evidence_number += 1

    if decision.next_action in {"reasoning_question", "variant_problem"}:
        used_probe_ids = {
            item["id"] for item in result["probes"] if isinstance(item, Mapping)
        }
        probe_number = 1
        while f"P{probe_number}" in used_probe_ids:
            probe_number += 1
        probe_id = f"P{probe_number}"
        probe = deepcopy(decision.probe)
        probe["id"] = probe_id
        probe["type"] = decision.next_action
        # Keep the persisted probe field order/shape independent of model order.
        result["probes"].append(
            {
                "id": probe_id,
                "type": probe["type"],
                "question": probe["question"],
                "target_hypothesis_ids": list(probe["target_hypothesis_ids"]),
                "discrimination_goal": probe["discrimination_goal"],
                "predictions": deepcopy(probe["predictions"]),
                "answer_key": probe["answer_key"],
                "preserved_mechanism": probe["preserved_mechanism"],
                "surface_change": probe["surface_change"],
            }
        )
        result["current_probe_id"] = probe_id
        result["diagnosis_status"] = "active"
    else:
        result["current_probe_id"] = ""
        result["diagnosis_status"] = (
            "supported" if decision.next_action == "finish_supported" else "undetermined"
        )

    result["best_hypothesis_id"] = decision.best_hypothesis_id
    result["remaining_uncertainty"] = decision.remaining_uncertainty
    result["what_would_change_judgment"] = decision.what_would_change_judgment
    return _validate_state(result)
