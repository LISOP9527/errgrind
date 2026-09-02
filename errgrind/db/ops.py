import json
import sqlite3
import os
from datetime import datetime
from typing import Optional

from .schema import create_tables
from ..config import prepare_database_path
from ..models.types import ErrorRecord, DrillAttempt, DrillAttemptResult, DrillContext


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
        rows = self.conn.execute(
            "SELECT id, question, grilling_summary FROM error_records "
            "WHERE status IN ('pending-teach', 'done') "
            "AND grilling_summary IS NOT NULL "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            DrillContext(r["id"], r["question"], r["grilling_summary"])
            for r in rows
        ]

    def record_drill_attempt(
        self,
        source_error_id: int,
        drill_spec: dict,
        question: str,
        reference_answer: str,
        user_response: str,
        is_correct: bool,
        feedback: str,
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
                "user_response, is_correct, feedback, derived_error_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    source_error_id,
                    drill_spec_json,
                    question,
                    reference_answer,
                    user_response,
                    int(is_correct),
                    feedback,
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
