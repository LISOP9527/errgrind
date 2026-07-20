import sqlite3
import os
from datetime import datetime
from typing import Optional

from .schema import create_tables
from ..models.types import ErrorRecord


DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "errgrind.db")


class Database:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        create_tables(self.conn)

    def _row_to_record(self, row) -> ErrorRecord:
        return ErrorRecord(
            id=row["id"],
            status=row["status"],
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
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO error_records (question, user_thoughts, reference_answer) "
            "VALUES (?, ?, ?)",
            (question, user_thoughts, reference_answer),
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

    def clear_teach_and_summary(self, error_id: int):
        self.conn.execute(
            "UPDATE error_records SET teach_conversation = NULL, grilling_summary = NULL, "
            "updated_at = datetime('now') WHERE id = ?",
            (error_id,),
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

    def get_drill_context(self, limit: int) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT question, grilling_summary FROM error_records "
            "WHERE status IN ('pending-teach', 'done') "
            "AND grilling_summary IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r["question"], r["grilling_summary"]) for r in rows]

    def close(self):
        self.conn.close()
