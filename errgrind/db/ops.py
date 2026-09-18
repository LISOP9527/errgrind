import json
import hashlib
import re
import sqlite3
import os
from datetime import datetime
from typing import Iterable, Optional

from .schema import create_tables
from ..config import prepare_database_path
from ..models.types import (
    ErrorRecord,
    DrillAttempt,
    DrillAttemptResult,
    DrillContext,
    StoredAttachment,
)
from ..models.titles import display_title_for_question


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
_ATTACHMENT_REF_RE = re.compile(
    r"^(?:initial_attachment:[1-9][0-9]*|message:[0-9]+:attachment:[1-9][0-9]*)$"
)
_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


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
        source_ref = evidence.get("source_ref") if isinstance(evidence, dict) else None
        is_attachment = (
            isinstance(source_ref, str)
            and _ATTACHMENT_REF_RE.fullmatch(source_ref) is not None
        )
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
                or is_attachment
            )
            or not (
                isinstance(evidence["quote"], str)
                and (bool(evidence["quote"].strip()) if not is_attachment else evidence["quote"] == "")
            )
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
            display_title=row["display_title"],
            user_thoughts=row["user_thoughts"],
            reference_answer=row["reference_answer"],
            grilling_conversation=row["grilling_conversation"],
            grilling_summary=row["grilling_summary"],
            grilling_diagnostic_state=row["grilling_diagnostic_state"],
            teach_conversation=row["teach_conversation"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_attachment(row) -> StoredAttachment:
        return StoredAttachment(
            id=row["id"],
            mime_type=row["mime_type"],
            data=bytes(row["data"]),
            sha256=row["sha256"],
            error_id=row["error_id"],
            drill_attempt_id=row["drill_attempt_id"],
            pending_key=row["pending_key"],
            conversation_kind=row["conversation_kind"],
            message_index=row["message_index"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _attachment_values(item) -> tuple[str, bytes]:
        """Accept only neutral (mime type, bytes) values at the DB boundary."""
        if isinstance(item, tuple) and len(item) == 2:
            mime_type, data = item
        else:
            mime_type = getattr(item, "mime_type", None)
            data = getattr(item, "data", None)
        if mime_type not in _IMAGE_MIME_TYPES or not isinstance(data, (bytes, bytearray)):
            raise ValueError("附件必须是 PNG、JPEG 或 WebP 图片")
        data = bytes(data)
        if not data:
            raise ValueError("附件不能为空")
        return mime_type, data

    def _insert_attachments(
        self,
        attachments: Iterable,
        *,
        error_id: Optional[int] = None,
        drill_attempt_id: Optional[int] = None,
        pending_key: Optional[str] = None,
        conversation_kind: Optional[str] = None,
        message_index: Optional[int] = None,
    ) -> list[int]:
        ids = []
        for item in attachments:
            mime_type, data = self._attachment_values(item)
            cursor = self.conn.execute(
                "INSERT INTO attachments "
                "(error_id, drill_attempt_id, pending_key, conversation_kind, "
                "message_index, mime_type, sha256, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    error_id,
                    drill_attempt_id,
                    pending_key,
                    conversation_kind,
                    message_index,
                    mime_type,
                    hashlib.sha256(data).hexdigest(),
                    sqlite3.Binary(data),
                ),
            )
            ids.append(int(cursor.lastrowid))
        return ids

    @staticmethod
    def _message_with_attachment_ids(
        conversation_json: str,
        attachment_ids: list[int],
    ) -> str:
        if not attachment_ids:
            return conversation_json
        messages = json.loads(conversation_json)
        if not isinstance(messages, list) or not messages:
            raise ValueError("对话必须是非空消息数组")
        last = messages[-1]
        if not isinstance(last, dict):
            raise ValueError("对话消息必须是对象")
        existing = last.get("attachments", [])
        if not isinstance(existing, list) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in existing
        ):
            raise ValueError("对话附件引用无效")
        last["attachments"] = [*existing, *attachment_ids]
        return json.dumps(messages, ensure_ascii=False)

    def create_error(
        self,
        question: str,
        user_thoughts: Optional[str] = None,
        reference_answer: Optional[str] = None,
        origin: str = "record",
        display_title: Optional[str] = None,
        *,
        attachments: Iterable = (),
        attachment_kind: str = "initial",
        pending_key: Optional[str] = None,
    ) -> int:
        if origin not in {"unknown", "record", "ocr", "drill"}:
            raise ValueError("origin 必须是 unknown、record、ocr 或 drill")
        if not isinstance(attachment_kind, str) or not attachment_kind.strip():
            raise ValueError("附件用途必须是非空文本")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO error_records "
                "(question, display_title, user_thoughts, reference_answer, origin) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    question,
                    (display_title or "").strip() or display_title_for_question(question),
                    user_thoughts,
                    reference_answer,
                    origin,
                ),
            )
            assert cur.lastrowid is not None
            error_id = int(cur.lastrowid)
            direct_attachments = list(attachments)
            if pending_key and direct_attachments:
                pending_hashes = {
                    row["sha256"]
                    for row in self.conn.execute(
                        "SELECT sha256 FROM attachments WHERE pending_key = ?",
                        (pending_key,),
                    ).fetchall()
                }
                direct_attachments = [
                    item for item in direct_attachments
                    if hashlib.sha256(self._attachment_values(item)[1]).hexdigest()
                    not in pending_hashes
                ]
            self._insert_attachments(
                direct_attachments,
                error_id=error_id,
                conversation_kind=attachment_kind,
                message_index=0,
            )
            if pending_key:
                self.conn.execute(
                    "UPDATE attachments SET error_id = ?, pending_key = NULL, "
                    "conversation_kind = ?, message_index = 0 "
                    "WHERE pending_key = ?",
                    (error_id, attachment_kind, pending_key),
                )
            return error_id

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

    def list_error_attachments(
        self,
        error_id: int,
        *,
        conversation_kind: Optional[str] = None,
        message_index: Optional[int] = None,
    ) -> list[StoredAttachment]:
        query = "SELECT * FROM attachments WHERE error_id = ?"
        params: list[object] = [error_id]
        if conversation_kind is not None:
            query += " AND conversation_kind = ?"
            params.append(conversation_kind)
        if message_index is not None:
            query += " AND message_index = ?"
            params.append(message_index)
        query += " ORDER BY id"
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_attachment(row) for row in rows]

    def get_attachments_for_ids(
        self, attachment_ids: Iterable[int], *, error_id: Optional[int] = None
    ) -> list[StoredAttachment]:
        ids = list(attachment_ids)
        if not ids:
            return []
        if any(isinstance(value, bool) or not isinstance(value, int) for value in ids):
            raise ValueError("附件引用无效")
        result = []
        for attachment_id in ids:
            query = "SELECT * FROM attachments WHERE id = ?"
            params: list[object] = [attachment_id]
            if error_id is not None:
                query += " AND error_id = ?"
                params.append(error_id)
            row = self.conn.execute(query, params).fetchone()
            if row is None:
                raise ValueError("附件引用不存在或不属于当前记录")
            result.append(self._row_to_attachment(row))
        return result

    def list_pending_attachments(self, pending_key: str) -> list[StoredAttachment]:
        if not isinstance(pending_key, str) or not pending_key:
            return []
        rows = self.conn.execute(
            "SELECT * FROM attachments WHERE pending_key = ? ORDER BY id",
            (pending_key,),
        ).fetchall()
        return [self._row_to_attachment(row) for row in rows]

    def save_pending_attachments(self, pending_key: str, attachments: Iterable) -> list[int]:
        if not isinstance(pending_key, str) or not pending_key:
            raise ValueError("附件暂存标识无效")
        with self.conn:
            self.conn.execute("DELETE FROM attachments WHERE pending_key = ?", (pending_key,))
            return self._insert_attachments(attachments, pending_key=pending_key)

    def append_pending_attachments(self, pending_key: str, attachments: Iterable) -> list[int]:
        """Append durable pending images, de-duplicating retry uploads by content hash."""
        if not isinstance(pending_key, str) or not pending_key:
            raise ValueError("附件暂存标识无效")
        items = [self._attachment_values(item) for item in attachments]
        if not items:
            return []
        with self.conn:
            existing = {
                row["sha256"]
                for row in self.conn.execute(
                    "SELECT sha256 FROM attachments WHERE pending_key = ?",
                    (pending_key,),
                ).fetchall()
            }
            fresh = []
            for mime_type, data in items:
                digest = hashlib.sha256(data).hexdigest()
                if digest in existing:
                    continue
                existing.add(digest)
                fresh.append((mime_type, data))
            return self._insert_attachments(fresh, pending_key=pending_key)

    def pending_attachment_count(self, pending_key: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS count FROM attachments WHERE pending_key = ?",
            (pending_key,),
        ).fetchone()
        return int(row["count"])

    def delete_pending_attachments(self, pending_key: str) -> None:
        if not isinstance(pending_key, str) or not pending_key:
            return
        with self.conn:
            self.conn.execute(
                "DELETE FROM attachments WHERE pending_key = ?", (pending_key,)
            )

    def _attach_last_message(
        self,
        conversation_json: str,
        attachments: Iterable,
        *,
        error_id: int,
        conversation_kind: str,
    ) -> tuple[str, list[int]]:
        attachment_items = list(attachments)
        if not attachment_items:
            return conversation_json, []
        messages = json.loads(conversation_json)
        if not isinstance(messages, list) or not messages:
            raise ValueError("对话必须是非空消息数组")
        message_index = len(messages) - 1
        ids = self._insert_attachments(
            attachment_items,
            error_id=error_id,
            conversation_kind=conversation_kind,
            message_index=message_index,
        )
        return self._message_with_attachment_ids(conversation_json, ids), ids

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
        attachments: Iterable = (),
    ) -> list[int]:
        """Atomically save a recoverable Grill conversation and its state."""
        with self.conn:
            conversation_json, attachment_ids = self._attach_last_message(
                conversation_json,
                attachments,
                error_id=error_id,
                conversation_kind="grill",
            )
            self.conn.execute(
                "UPDATE error_records SET grilling_conversation = ?, "
                "grilling_diagnostic_state = ?, status = 'pending-grill', "
                "updated_at = datetime('now') WHERE id = ?",
                (conversation_json, diagnostic_state_json, error_id),
            )
            return attachment_ids

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

    def update_teach(self, error_id: int, conversation: str, attachments: Iterable = ()):
        with self.conn:
            conversation, attachment_ids = self._attach_last_message(
                conversation,
                attachments,
                error_id=error_id,
                conversation_kind="teach",
            )
            self.conn.execute(
                "UPDATE error_records SET teach_conversation = ?, status = 'done', "
                "updated_at = datetime('now') WHERE id = ?",
                (conversation, error_id),
            )
            return attachment_ids

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

    def save_teach_conversation(self, error_id: int, conversation: str, attachments: Iterable = ()):
        with self.conn:
            conversation, attachment_ids = self._attach_last_message(
                conversation,
                attachments,
                error_id=error_id,
                conversation_kind="teach",
            )
            self.conn.execute(
                "UPDATE error_records SET teach_conversation = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (conversation, error_id),
            )
            return attachment_ids

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
        pending_key: Optional[str] = None,
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
                    "(question, display_title, user_thoughts, reference_answer, origin, "
                    "source_error_id, source_drill_attempt_id) "
                    "VALUES (?, ?, ?, ?, 'drill', ?, ?)",
                    (
                        question,
                        display_title_for_question(question),
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
            if pending_key:
                pending_rows = self.conn.execute(
                    "SELECT * FROM attachments WHERE pending_key = ? ORDER BY id",
                    (pending_key,),
                ).fetchall()
                self.conn.execute(
                    "UPDATE attachments SET drill_attempt_id = ?, pending_key = NULL, "
                    "conversation_kind = 'drill_answer', message_index = NULL "
                    "WHERE pending_key = ?",
                    (attempt_id, pending_key),
                )
                if derived_id is not None and pending_rows:
                    # Keep the attempt's immutable provenance and give the
                    # derived Error its own cascade-owned copy of the bytes.
                    self._insert_attachments(
                        [
                            (row["mime_type"], bytes(row["data"]))
                            for row in pending_rows
                        ],
                        error_id=int(derived_id),
                        conversation_kind="initial",
                        message_index=0,
                    )
            return DrillAttemptResult(attempt_id, derived_id)

    def _row_to_drill_attempt(self, row) -> DrillAttempt:
        return DrillAttempt(
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
            attachment_ids=tuple(
                item["id"]
                for item in self.conn.execute(
                    "SELECT id FROM attachments WHERE drill_attempt_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
            ),
        )

    def get_drill_attempt(self, attempt_id: int) -> Optional[DrillAttempt]:
        row = self.conn.execute(
            "SELECT * FROM drill_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        return self._row_to_drill_attempt(row) if row else None

    def list_drill_attempts(self, limit: int | None = 20) -> list[DrillAttempt]:
        query = "SELECT * FROM drill_attempts ORDER BY created_at DESC, id DESC"
        params = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_drill_attempt(row) for row in rows]

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
