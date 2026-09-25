"""SQLite-backed long-term memory: conversation history, progress, notes, lessons."""

import json
import sqlite3
import threading
from datetime import datetime, timezone

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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


class Memory:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO profile (id, data) VALUES (1, ?)",
                (json.dumps(DEFAULT_PROFILE),),
            )

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock, self._conn:
            self._conn.execute(sql, params)

    # Conversation history (append-only per session)
    def get_history(self, session_id: str) -> list[dict]:
        rows = self._query(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
        )
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]

    def append_messages(self, session_id: str, messages: list[dict]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
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
        return [dict(r) for r in rows]

    # Notes
    def save_note(self, topic: str, content: str) -> None:
        self._execute(
            "INSERT INTO notes (topic, content, created_at) VALUES (?, ?, ?)",
            (topic, content, _now()),
        )

    def get_notes(self, topic: str | None = None, limit: int = 20) -> list[dict]:
        if topic:
            rows = self._query(
                "SELECT topic, content, created_at FROM notes WHERE topic LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (f"%{topic}%", limit),
            )
        else:
            rows = self._query(
                "SELECT topic, content, created_at FROM notes ORDER BY id DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in rows]

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
        return dict(rows[0]) if rows else None

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
        return [dict(r) for r in rows]
