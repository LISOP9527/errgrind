import sqlite3
import os
from datetime import datetime
from typing import Optional

from .schema import create_tables
from ..models.types import Session, Question, Attempt, ErrorRecord


DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "errgrind.db")


class Database:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        create_tables(self.conn)

    def create_session(self, goal: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO sessions (goal) VALUES (?)", (goal,)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_session(self, session_id: int) -> Optional[Session]:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return Session(
            id=row["id"],
            goal=row["goal"],
            summary=row["summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_question(
        self,
        session_id: int,
        content: str,
        correct_answer: str,
        source: str,
        options: Optional[str] = None,
        difficulty: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO questions (session_id, content, options, correct_answer, source, difficulty) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, content, options, correct_answer, source, difficulty),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_question(self, question_id: int) -> Optional[Question]:
        row = self.conn.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if not row:
            return None
        return Question(
            id=row["id"],
            session_id=row["session_id"],
            content=row["content"],
            options=row["options"],
            correct_answer=row["correct_answer"],
            source=row["source"],
            difficulty=row["difficulty"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_attempt(
        self,
        session_id: int,
        question_id: int,
        user_answer: str,
        is_correct: bool,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO attempts (session_id, question_id, user_answer, is_correct) "
            "VALUES (?, ?, ?, ?)",
            (session_id, question_id, user_answer, int(is_correct)),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_error_record(
        self,
        session_id: int,
        attempt_id: int,
        raw_conversation: str,
        compressed_summary: str,
        categories: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO error_records (session_id, attempt_id, raw_conversation, compressed_summary, categories) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, attempt_id, raw_conversation, compressed_summary, categories),
        )
        self.conn.commit()
        return cur.lastrowid

    def close(self):
        self.conn.close()
