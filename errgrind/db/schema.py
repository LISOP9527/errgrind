SCHEMA_VERSION = 3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS error_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'pending-grill',
    origin TEXT NOT NULL DEFAULT 'unknown' CHECK (origin IN ('unknown', 'record', 'ocr', 'drill')),
    source_error_id INTEGER,
    source_drill_attempt_id INTEGER,
    question TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_error_records_status ON error_records(status);
CREATE INDEX IF NOT EXISTS idx_error_records_updated_at ON error_records(updated_at);
CREATE INDEX IF NOT EXISTS idx_drill_attempts_source ON drill_attempts(source_error_id);
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
