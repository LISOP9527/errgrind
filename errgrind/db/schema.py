from ..models.titles import display_title_for_question, is_safe_display_title

SCHEMA_VERSION = 6


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS error_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'pending-grill',
    origin TEXT NOT NULL DEFAULT 'unknown' CHECK (origin IN ('unknown', 'record', 'ocr', 'drill')),
    source_error_id INTEGER,
    source_drill_attempt_id INTEGER,
    question TEXT NOT NULL,
    display_title TEXT,
    user_thoughts TEXT,
    reference_answer TEXT,
    grilling_conversation TEXT,
    grilling_summary TEXT,
    grilling_diagnostic_state TEXT,
    teach_conversation TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_error_id) REFERENCES error_records(id) ON DELETE SET NULL,
    FOREIGN KEY (source_drill_attempt_id) REFERENCES drill_attempts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS drill_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_error_id INTEGER NOT NULL,
    drill_spec TEXT NOT NULL,
    question TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    user_response TEXT NOT NULL,
    is_correct INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
    feedback TEXT NOT NULL,
    judge_provider TEXT NOT NULL DEFAULT 'unknown',
    judge_model TEXT NOT NULL DEFAULT 'unknown',
    judge_prompt_sha256 TEXT NOT NULL DEFAULT 'unknown',
    judge_schema_sha256 TEXT NOT NULL DEFAULT 'unknown',
    derived_error_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_error_id) REFERENCES error_records(id) ON DELETE CASCADE,
    FOREIGN KEY (derived_error_id) REFERENCES error_records(id) ON DELETE SET NULL
);

-- Image bytes are kept out of conversation JSON.  Conversation messages and
-- Drill attempts store only attachment IDs, so retries can reload the exact
-- original bytes without putting them in templates or browser state.
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_id INTEGER,
    drill_attempt_id INTEGER,
    pending_key TEXT,
    conversation_kind TEXT,
    message_index INTEGER,
    mime_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (error_id) REFERENCES error_records(id) ON DELETE CASCADE,
    FOREIGN KEY (drill_attempt_id) REFERENCES drill_attempts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_error_records_status ON error_records(status);
CREATE INDEX IF NOT EXISTS idx_error_records_updated_at ON error_records(updated_at);
CREATE INDEX IF NOT EXISTS idx_drill_attempts_source ON drill_attempts(source_error_id);
CREATE INDEX IF NOT EXISTS idx_attachments_error_message
    ON attachments(error_id, conversation_kind, message_index);
CREATE INDEX IF NOT EXISTS idx_attachments_attempt ON attachments(drill_attempt_id);
CREATE INDEX IF NOT EXISTS idx_attachments_pending ON attachments(pending_key);
"""


def create_tables(conn):
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current_version} 高于当前程序支持的版本 "
            f"{SCHEMA_VERSION}，请升级 ErrGrind"
        )

    conn.executescript(SCHEMA_SQL)
    # 旧版单表数据库无 provenance 字段；历史来源必须保持未知，不能推断。
    columns = {row[1] for row in conn.execute("PRAGMA table_info(error_records)")}
    if "origin" not in columns:
        conn.execute("ALTER TABLE error_records ADD COLUMN origin TEXT NOT NULL DEFAULT 'unknown'")
    if "source_error_id" not in columns:
        conn.execute("ALTER TABLE error_records ADD COLUMN source_error_id INTEGER")
    if "source_drill_attempt_id" not in columns:
        conn.execute("ALTER TABLE error_records ADD COLUMN source_drill_attempt_id INTEGER")
    added_display_title = "display_title" not in columns
    if added_display_title:
        conn.execute("ALTER TABLE error_records ADD COLUMN display_title TEXT")
    # Backfill only missing or obviously unsafe generated titles.  A stable
    # persisted title that is already plain remains unchanged.
    legacy_titles = conn.execute(
        "SELECT id, question, display_title FROM error_records"
    ).fetchall()
    unsafe_titles = [
        (display_title_for_question(row["question"]), row["id"])
        for row in legacy_titles
        if not is_safe_display_title(row["display_title"])
    ]
    if unsafe_titles:
        conn.executemany(
            "UPDATE error_records SET display_title = ? WHERE id = ?",
            unsafe_titles,
        )
    if "grilling_diagnostic_state" not in columns:
        conn.execute(
            "ALTER TABLE error_records ADD COLUMN grilling_diagnostic_state TEXT"
        )
    attempt_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(drill_attempts)")
    }
    for name in (
        "judge_provider",
        "judge_model",
        "judge_prompt_sha256",
        "judge_schema_sha256",
    ):
        if name not in attempt_columns:
            conn.execute(
                f"ALTER TABLE drill_attempts ADD COLUMN {name} "
                "TEXT NOT NULL DEFAULT 'unknown'"
            )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
