SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS error_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'pending-grill',
    question TEXT NOT NULL,
    user_thoughts TEXT,
    reference_answer TEXT,
    grilling_conversation TEXT,
    grilling_summary TEXT,
    teach_conversation TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_error_records_status ON error_records(status);
CREATE INDEX IF NOT EXISTS idx_error_records_updated_at ON error_records(updated_at);
"""


def create_tables(conn):
    conn.executescript(SCHEMA_SQL)
    conn.commit()
