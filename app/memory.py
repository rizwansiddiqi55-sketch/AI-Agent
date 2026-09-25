"""SQLite-backed long-term memory: conversation history, progress, notes, lessons."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

LEVELS = ["Not Started", "Beginner", "Developing", "Intermediate", "Advanced", "Mastered"]
SUBJECTS = ["AI", "Python", "Networking", "Cybersecurity", "English"]

DEFAULT_PROFILE = {
    "name": "Rizwan Siddiqi",
    "location": "Dubai, UAE",
    "current_role": "Senior Network Engineer",
    "experience_years": "15+",
    "education": "BS Computer Science",
    "certifications": ["CCNP Enterprise", "CCNP Security", "CCNP Data Center"],
    "strengths": ["Routing", "Switching", "Cyber security", "NAC", "Firewalls", "Cisco", "Fortinet"],
    "goals": [
        "Become a strong AI Engineer + Network Automation Engineer",
        "Improve spoken English and technical communication",
        "Prepare for Senior Network / Network Security / Infrastructure interviews",
    ],
    "languages": ["English", "Urdu", "Roman Urdu"],
}

# Written once for both backends; {id} becomes the dialect's auto-increment key.
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id {id},
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS progress (
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    level TEXT NOT NULL,
    evidence TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject, topic)
);
CREATE TABLE IF NOT EXISTS notes (
    id {id},
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_lesson (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    step TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS english_corrections (
    id {id},
    original TEXT NOT NULL,
    corrected TEXT NOT NULL,
    rule TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _SqliteBackend:
    param = "?"
    id_type = "INTEGER PRIMARY KEY AUTOINCREMENT"

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def query(self, sql: str, params: tuple) -> list[dict]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params_list: list[tuple]) -> None:
        with self._conn:
            self._conn.executemany(sql, params_list)

    def executescript(self, script: str) -> None:
        with self._conn:
            self._conn.executescript(script)


class _PostgresBackend:
    param = "%s"
    id_type = "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row

        self._psycopg = psycopg
        self._connect = lambda: psycopg.connect(url, autocommit=True, row_factory=dict_row,
                                                prepare_threshold=None)
        self._conn = self._connect()

    def _run(self, fn):
        # Serverless databases close idle connections; reconnect once on failure.
        try:
            return fn(self._conn)
        except self._psycopg.OperationalError:
            self._conn = self._connect()
            return fn(self._conn)

    def query(self, sql: str, params: tuple) -> list[dict]:
        return self._run(lambda c: c.execute(sql, params).fetchall())

    def execute(self, sql: str, params_list: list[tuple]) -> None:
        def run(conn):
            with conn.transaction(), conn.cursor() as cur:
                cur.executemany(sql, params_list)
        self._run(run)

    def executescript(self, script: str) -> None:
        self._run(lambda c: c.execute(script))


class Memory:
    """Long-term memory. `target` is a SQLite file path or a postgres:// URL."""

    def __init__(self, target: str):
        if target.startswith(("postgres://", "postgresql://")):
            self._db: Any = _PostgresBackend(target)
        else:
            self._db = _SqliteBackend(target)
        self._lock = threading.Lock()
        self._db.executescript(SCHEMA.replace("{id}", self._db.id_type))
        self._execute(
            "INSERT INTO profile (id, data) VALUES (1, ?) ON CONFLICT (id) DO NOTHING",
            (json.dumps(DEFAULT_PROFILE),),
        )

    def _sql(self, sql: str) -> str:
        return sql.replace("?", self._db.param)

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return self._db.query(self._sql(sql), params)

    def _execute(self, sql: str, params: tuple = ()) -> None:
        self._executemany(sql, [params])

    def _executemany(self, sql: str, params_list: list[tuple]) -> None:
        with self._lock:
            self._db.execute(self._sql(sql), params_list)

    # Conversation history (append-only per session)
    def get_history(self, session_id: str) -> list[dict]:
        rows = self._query(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
        )
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]

    def append_messages(self, session_id: str, messages: list[dict]) -> None:
        self._executemany(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            [(session_id, m["role"], json.dumps(m["content"]), _now()) for m in messages],
        )

    def clear_history(self, session_id: str) -> None:
        self._execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    # Learner profile
    def get_profile(self) -> dict:
        return json.loads(self._query("SELECT data FROM profile WHERE id = 1")[0]["data"])

    # Progress tracking
    def update_progress(self, subject: str, topic: str, level: str, evidence: str) -> None:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        self._execute(
            """INSERT INTO progress (subject, topic, level, evidence, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(subject, topic) DO UPDATE SET
                 level = excluded.level, evidence = excluded.evidence,
                 updated_at = excluded.updated_at""",
            (subject, topic, level, evidence, _now()),
        )

    def get_progress(self) -> list[dict]:
        rows = self._query(
            "SELECT subject, topic, level, evidence, updated_at FROM progress ORDER BY subject, topic"
        )
        return rows

    # Notes
    def save_note(self, topic: str, content: str) -> None:
        self._execute(
            "INSERT INTO notes (topic, content, created_at) VALUES (?, ?, ?)",
            (topic, content, _now()),
        )

    def get_notes(self, topic: str | None = None, limit: int = 20) -> list[dict]:
        if topic:
            rows = self._query(
                "SELECT topic, content, created_at FROM notes WHERE LOWER(topic) LIKE LOWER(?) "
                "ORDER BY id DESC LIMIT ?",
                (f"%{topic}%", limit),
            )
        else:
            rows = self._query(
                "SELECT topic, content, created_at FROM notes ORDER BY id DESC LIMIT ?", (limit,)
            )
        return rows

    # Current lesson (for "Continue my course")
    def set_current_lesson(self, subject: str, topic: str, step: str) -> None:
        self._execute(
            """INSERT INTO current_lesson (id, subject, topic, step, updated_at)
               VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET subject = excluded.subject,
                 topic = excluded.topic, step = excluded.step, updated_at = excluded.updated_at""",
            (subject, topic, step, _now()),
        )

    def get_current_lesson(self) -> dict | None:
        rows = self._query("SELECT subject, topic, step, updated_at FROM current_lesson WHERE id = 1")
        return rows[0] if rows else None

    # English corrections
    def log_english_correction(self, original: str, corrected: str, rule: str) -> None:
        self._execute(
            "INSERT INTO english_corrections (original, corrected, rule, created_at) "
            "VALUES (?, ?, ?, ?)",
            (original, corrected, rule, _now()),
        )

    def get_english_corrections(self, limit: int = 20) -> list[dict]:
        rows = self._query(
            "SELECT original, corrected, rule, created_at FROM english_corrections "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return rows
