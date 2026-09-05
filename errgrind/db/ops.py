import json
import re
import sqlite3
import os
from datetime import datetime
from typing import Optional

from .schema import create_tables
from ..config import prepare_database_path
from ..models.types import ErrorRecord, DrillAttempt, DrillAttemptResult, DrillContext


_DIAGNOSTIC_STATE_KEYS = {
    "version",
    "diagnosis_status",
    "hypotheses",
    "evidence",
    "probes",
    "current_probe_id",
    "best_hypothesis_id",
    "remaining_uncertainty",
    "what_would_change_judgment",
}
_MESSAGE_REF_RE = re.compile(r"^message:[0-9]+$")


def _is_nonempty_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_string_list(value) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        and len(set(value)) == len(value)
    )


def _is_diagnostic_id(value, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"{prefix}[1-9][0-9]*", value) is not None


def _is_supported_diagnostic_state(raw: str) -> bool:
    """Return False for malformed/non-supported state; never fall back to legacy."""
    try:
        state = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
        return False
    if not isinstance(state, dict) or set(state) != _DIAGNOSTIC_STATE_KEYS:
        return False
    version = state.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
        or state.get("diagnosis_status") != "supported"
    ):
        return False
    if any(
        not isinstance(state.get(key), list)
        for key in ("hypotheses", "evidence", "probes")
    ):
        return False
    if state.get("current_probe_id") != "":
        return False
    best_id = state.get("best_hypothesis_id")
    if not isinstance(best_id, str) or not best_id:
        return False
    if not isinstance(state.get("remaining_uncertainty"), str):
        return False
    if not _is_nonempty_text(state.get("what_would_change_judgment")):
        return False
    hypotheses = state["hypotheses"]
    if any(
        not isinstance(hypothesis, dict)
        or set(hypothesis) != {"id", "claim", "status"}
        or not _is_diagnostic_id(hypothesis["id"], "H")
        or not isinstance(hypothesis["claim"], str)
        or not isinstance(hypothesis["status"], str)
        or not hypothesis["id"]
        or not hypothesis["claim"].strip()
        or hypothesis["status"]
        not in {"plausible", "supported", "weakened", "rejected"}
        for hypothesis in hypotheses
    ):
        return False
    if len({hypothesis["id"] for hypothesis in hypotheses}) != len(hypotheses):
        return False
    if not any(
        hypothesis["id"] == best_id and hypothesis["status"] == "supported"
        for hypothesis in hypotheses
    ):
        return False

    hypothesis_ids = {hypothesis["id"] for hypothesis in hypotheses}
    evidence_ids = set()
    for evidence in state["evidence"]:
        if (
            not isinstance(evidence, dict)
            or set(evidence)
            != {
                "id",
                "source_ref",
                "quote",
                "interpretation",
                "supports",
                "contradicts",
                "probe_id",
            }
            or not _is_diagnostic_id(evidence["id"], "E")
            or not evidence["id"]
            or evidence["id"] in evidence_ids
            or not isinstance(evidence["source_ref"], str)
            or not (
                evidence["source_ref"] == "initial_user_thoughts"
                or _MESSAGE_REF_RE.fullmatch(evidence["source_ref"]) is not None
            )
            or not _is_nonempty_text(evidence["quote"])
            or not _is_nonempty_text(evidence["interpretation"])
            or not _is_string_list(evidence["supports"])
            or not _is_string_list(evidence["contradicts"])
            or set(evidence["supports"]) & set(evidence["contradicts"])
            or not (
                set(evidence["supports"]) | set(evidence["contradicts"])
            ).issubset(hypothesis_ids)
            or not isinstance(evidence["probe_id"], str)
        ):
            return False
        evidence_ids.add(evidence["id"])

    probes = state["probes"]
    probe_ids = set()
    has_supporting_evidence = False
    for probe in probes:
        if (
            not isinstance(probe, dict)
            or set(probe)
            != {
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
            or not _is_diagnostic_id(probe["id"], "P")
            or not probe["id"]
            or probe["id"] in probe_ids
            or not isinstance(probe["type"], str)
            or probe["type"] not in {"reasoning_question", "variant_problem"}
            or not _is_nonempty_text(probe["question"])
            or not _is_nonempty_text(probe["discrimination_goal"])
            or not _is_string_list(probe["target_hypothesis_ids"])
            or not probe["target_hypothesis_ids"]
            or not set(probe["target_hypothesis_ids"]).issubset(hypothesis_ids)
            or not isinstance(probe["predictions"], list)
            or not all(
                isinstance(probe[key], str)
                for key in ("answer_key", "preserved_mechanism", "surface_change")
            )
        ):
            return False
        hidden = [probe[key] for key in ("answer_key", "preserved_mechanism", "surface_change")]
        if probe["type"] == "variant_problem":
            if len(probe["target_hypothesis_ids"]) < 2 or not all(map(_is_nonempty_text, hidden)):
                return False
        elif any(hidden):
            return False
        prediction_ids = set()
        for prediction in probe["predictions"]:
            if (
                not isinstance(prediction, dict)
                or set(prediction) != {"hypothesis_id", "expected_observation"}
                or not isinstance(prediction["hypothesis_id"], str)
                or prediction["hypothesis_id"] in prediction_ids
                or prediction["hypothesis_id"] not in probe["target_hypothesis_ids"]
                or not _is_nonempty_text(prediction["expected_observation"])
            ):
                return False
            prediction_ids.add(prediction["hypothesis_id"])
        if prediction_ids != set(probe["target_hypothesis_ids"]):
            return False
        probe_ids.add(probe["id"])

    for evidence in state["evidence"]:
        if evidence["probe_id"] and evidence["probe_id"] not in probe_ids:
            return False
        if best_id in evidence["supports"]:
            has_supporting_evidence = True
    return has_supporting_evidence


class Database:
    def __init__(self, db_path: str | None = None):
        db_path = db_path or prepare_database_path()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        try:
            create_tables(self.conn)
        except Exception:
            self.conn.close()
            raise

    def _row_to_record(self, row) -> ErrorRecord:
        return ErrorRecord(
            id=row["id"],
            status=row["status"],
            origin=row["origin"],
            source_error_id=row["source_error_id"],
            source_drill_attempt_id=row["source_drill_attempt_id"],
            question=row["question"],
            user_thoughts=row["user_thoughts"],
            reference_answer=row["reference_answer"],
            grilling_conversation=row["grilling_conversation"],
            grilling_summary=row["grilling_summary"],
            grilling_diagnostic_state=row["grilling_diagnostic_state"],
            teach_conversation=row["teach_conversation"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def create_error(
        self,
        question: str,
        user_thoughts: Optional[str] = None,
        reference_answer: Optional[str] = None,
        origin: str = "record",
    ) -> int:
        if origin not in {"unknown", "record", "ocr", "drill"}:
            raise ValueError("origin 必须是 unknown、record、ocr 或 drill")
        cur = self.conn.execute(
            "INSERT INTO error_records "
            "(question, user_thoughts, reference_answer, origin) "
            "VALUES (?, ?, ?, ?)",
            (question, user_thoughts, reference_answer, origin),
        )
        self.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_error(self, error_id: int) -> Optional[ErrorRecord]:
        row = self.conn.execute(
            "SELECT * FROM error_records WHERE id = ?", (error_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_all_errors(self) -> list[ErrorRecord]:
        rows = self.conn.execute(
            "SELECT * FROM error_records ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count_by_status(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM error_records GROUP BY status"
        ).fetchall()
        counts = {"pending-grill": 0, "pending-teach": 0, "done": 0, "total": 0}
        for r in rows:
            counts[r["status"]] = r["cnt"]
            counts["total"] += r["cnt"]
        return counts

    def count_by_origin(self) -> dict[str, int]:
        counts = {"unknown": 0, "record": 0, "ocr": 0, "drill": 0}
        rows = self.conn.execute(
            "SELECT origin, COUNT(*) AS cnt FROM error_records GROUP BY origin"
        )
        for row in rows:
            counts[row["origin"]] = row["cnt"]
        return counts

    def update_grilling(self, error_id: int, conversation: str, summary: str):
        self.conn.execute(
            "UPDATE error_records SET grilling_conversation = ?, grilling_summary = ?, "
            "status = 'pending-teach', updated_at = datetime('now') WHERE id = ?",
            (conversation, summary, error_id),
        )
        self.conn.commit()

    def save_grilling_progress(
        self,
        error_id: int,
        conversation_json: str,
        diagnostic_state_json: Optional[str],
    ):
        """Atomically save a recoverable Grill conversation and its state."""
        with self.conn:
            self.conn.execute(
                "UPDATE error_records SET grilling_conversation = ?, "
                "grilling_diagnostic_state = ?, status = 'pending-grill', "
                "updated_at = datetime('now') WHERE id = ?",
                (conversation_json, diagnostic_state_json, error_id),
            )

    def complete_grilling(
        self,
        error_id: int,
        conversation_json: str,
        summary: str,
        diagnostic_state_json: str,
    ):
        """Atomically finish Grill and move the Error to pending-teach."""
        with self.conn:
            self.conn.execute(
                "UPDATE error_records SET grilling_conversation = ?, "
                "grilling_summary = ?, grilling_diagnostic_state = ?, "
                "status = 'pending-teach', updated_at = datetime('now') "
                "WHERE id = ?",
                (conversation_json, summary, diagnostic_state_json, error_id),
            )

    def update_teach(self, error_id: int, conversation: str):
        self.conn.execute(
            "UPDATE error_records SET teach_conversation = ?, status = 'done', "
            "updated_at = datetime('now') WHERE id = ?",
            (conversation, error_id),
        )
        self.conn.commit()

    def set_status(self, error_id: int, status: str):
        self.conn.execute(
            "UPDATE error_records SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, error_id),
        )
        self.conn.commit()

    def save_grilling_conversation(self, error_id: int, conversation: str):
        self.conn.execute(
            "UPDATE error_records SET grilling_conversation = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (conversation, error_id),
        )
        self.conn.commit()

    def save_teach_conversation(self, error_id: int, conversation: str):
        self.conn.execute(
            "UPDATE error_records SET teach_conversation = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (conversation, error_id),
        )
        self.conn.commit()

    def get_drill_context(self, limit: int) -> list[DrillContext]:
        if limit <= 0:
            return []
        rows = self.conn.execute(
            "SELECT id, question, grilling_summary, grilling_diagnostic_state "
            "FROM error_records "
            "WHERE status IN ('pending-teach', 'done') "
            "AND grilling_summary IS NOT NULL "
            "ORDER BY updated_at DESC, id DESC",
        ).fetchall()
        context = []
        for row in rows:
            diagnostic_state = row["grilling_diagnostic_state"]
            if diagnostic_state is not None and not _is_supported_diagnostic_state(
                diagnostic_state
            ):
                continue
            context.append(
                DrillContext(row["id"], row["question"], row["grilling_summary"])
            )
            if len(context) >= limit:
                break
        return context

    def record_drill_attempt(
        self,
        source_error_id: int,
        drill_spec: dict,
        question: str,
        reference_answer: str,
        user_response: str,
        is_correct: bool,
        feedback: str,
        judge_provider: str = "unknown",
        judge_model: str = "unknown",
        judge_prompt_sha256: str = "unknown",
        judge_schema_sha256: str = "unknown",
    ) -> DrillAttemptResult:
        """原子保存一次判分；错误结果同时创建可追溯的衍生 Error。"""
        if not isinstance(is_correct, bool):
            raise ValueError("is_correct 必须是布尔值")
        if not isinstance(drill_spec, dict):
            raise ValueError("drill_spec 必须是 JSON 对象")
        drill_spec_json = json.dumps(drill_spec, ensure_ascii=False)

        with self.conn:
            derived_id = None
            cur = self.conn.execute(
                "INSERT INTO drill_attempts "
                "(source_error_id, drill_spec, question, reference_answer, "
                "user_response, is_correct, feedback, judge_provider, judge_model, "
                "judge_prompt_sha256, judge_schema_sha256, derived_error_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    source_error_id,
                    drill_spec_json,
                    question,
                    reference_answer,
                    user_response,
                    int(is_correct),
                    feedback,
                    judge_provider,
                    judge_model,
                    judge_prompt_sha256,
                    judge_schema_sha256,
                ),
            )
            attempt_id = int(cur.lastrowid)
            if not is_correct:
                derived_id = self.conn.execute(
                    "INSERT INTO error_records "
                    "(question, user_thoughts, reference_answer, origin, "
                    "source_error_id, source_drill_attempt_id) "
                    "VALUES (?, ?, ?, 'drill', ?, ?)",
                    (
                        question,
                        user_response,
                        reference_answer,
                        source_error_id,
                        attempt_id,
                    ),
                ).lastrowid
            self.conn.execute(
                "UPDATE drill_attempts SET derived_error_id = ? WHERE id = ?",
                (derived_id, attempt_id),
            )
            return DrillAttemptResult(attempt_id, derived_id)

    def list_drill_attempts(self, limit: int = 20) -> list[DrillAttempt]:
        rows = self.conn.execute(
            "SELECT * FROM drill_attempts "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            DrillAttempt(
                id=row["id"],
                source_error_id=row["source_error_id"],
                drill_spec=json.loads(row["drill_spec"]),
                question=row["question"],
                reference_answer=row["reference_answer"],
                user_response=row["user_response"],
                is_correct=bool(row["is_correct"]),
                feedback=row["feedback"],
                judge_provider=row["judge_provider"],
                judge_model=row["judge_model"],
                judge_prompt_sha256=row["judge_prompt_sha256"],
                judge_schema_sha256=row["judge_schema_sha256"],
                derived_error_id=row["derived_error_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def drill_stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) total, COALESCE(SUM(is_correct), 0) correct "
            "FROM drill_attempts"
        ).fetchone()
        return {
            "total": row["total"],
            "correct": row["correct"],
            "incorrect": row["total"] - row["correct"],
        }

    def delete_error(self, error_id: int):
        with self.conn:
            # 删除父 Error 后清除子 Error 的结构化来源字段，避免悬空关系被误读。
            self.conn.execute(
                "UPDATE error_records SET source_error_id = NULL, "
                "source_drill_attempt_id = NULL, updated_at = datetime('now') "
                "WHERE source_error_id = ?",
                (error_id,),
            )
            self.conn.execute(
                "DELETE FROM drill_attempts "
                "WHERE source_error_id = ? OR derived_error_id = ?",
                (error_id, error_id),
            )
            self.conn.execute("DELETE FROM error_records WHERE id = ?", (error_id,))

    def close(self):
        self.conn.close()
