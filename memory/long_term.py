"""Long-term operational memory stored in Sam's SQLite database."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult


_lock = Lock()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS long_term_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'medium',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(scope, key)
);
CREATE INDEX IF NOT EXISTS idx_long_term_facts_scope ON long_term_facts(scope);
CREATE INDEX IF NOT EXISTS idx_long_term_facts_key ON long_term_facts(key);

CREATE TABLE IF NOT EXISTS long_term_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT '',
    situation TEXT NOT NULL,
    what_worked TEXT NOT NULL DEFAULT '',
    what_failed TEXT NOT NULL DEFAULT '',
    tool_used TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_long_term_lessons_scope ON long_term_lessons(scope);

CREATE TABLE IF NOT EXISTS long_term_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    message TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_long_term_conversations_session ON long_term_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_long_term_conversations_created ON long_term_conversations(created_at);
"""


def ensure_schema(db_path: str | Path) -> SamResult:
    try:
        with _lock, _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()
        return SamResult(
            status="success",
            summary="Long-term memory schema initialized.",
            next_action="stop",
            metadata={"db_path": str(db_path)},
        )
    except sqlite3.Error as exc:
        return SamResult(
            status="failed",
            summary="Failed to initialize long-term memory.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
            metadata={"db_path": str(db_path)},
        )


def store_fact(
    db_path: str | Path,
    *,
    key: str,
    value: str,
    scope: str = "",
    source: str = "",
    confidence: str = "medium",
    metadata: dict[str, Any] | None = None,
) -> SamResult:
    key = key.strip()
    value = value.strip()
    if not key or not value:
        return SamResult(
            status="failed",
            summary="Fact key and value are required.",
            error_type=ErrorType.TOOL_FAILED,
            error_message="missing fact key/value",
            next_action="ask_user",
        )
    now = _now()
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO long_term_facts(scope, key, value, source, confidence, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, key) DO UPDATE SET
                    value = excluded.value,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (scope, key, value, source, confidence, _json(metadata), now),
            )
            conn.commit()
        return SamResult(status="success", summary="Fact stored.", next_action="stop")
    except sqlite3.Error as exc:
        return SamResult(
            status="failed",
            summary="Failed to store fact.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
        )


def recall(
    db_path: str | Path,
    query: str,
    *,
    scope: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    terms = [item for item in query.lower().split() if len(item) > 2][:8]
    if not terms:
        return []
    clauses: list[str] = []
    params: list[Any] = []
    for term in terms:
        like = f"%{term}%"
        clauses.append("(LOWER(key) LIKE ? OR LOWER(value) LIKE ?)")
        params.extend([like, like])
    if scope:
        clauses.append("(scope = ? OR scope = '')")
        params.append(scope)
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM long_term_facts
            WHERE {' OR '.join(clauses)}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_dict(row) for row in rows]


def learn(
    db_path: str | Path,
    *,
    situation: str,
    what_worked: str = "",
    what_failed: str = "",
    tool_used: str = "",
    scope: str = "",
    metadata: dict[str, Any] | None = None,
) -> SamResult:
    if not situation.strip():
        return SamResult(
            status="failed",
            summary="Lesson situation is required.",
            error_type=ErrorType.TOOL_FAILED,
            error_message="missing situation",
            next_action="ask_user",
        )
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO long_term_lessons(scope, situation, what_worked, what_failed, tool_used, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (scope, situation[:500], what_worked[:1000], what_failed[:1000], tool_used, _json(metadata)),
            )
            conn.commit()
        return SamResult(status="success", summary="Lesson stored.", next_action="stop")
    except sqlite3.Error as exc:
        return SamResult(
            status="failed",
            summary="Failed to store lesson.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
        )


def recall_lessons(
    db_path: str | Path,
    situation: str,
    *,
    scope: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    terms = [item for item in situation.lower().split() if len(item) > 2][:8]
    if not terms:
        return []
    clauses: list[str] = []
    params: list[Any] = []
    for term in terms:
        like = f"%{term}%"
        clauses.append("(LOWER(situation) LIKE ? OR LOWER(what_worked) LIKE ? OR LOWER(what_failed) LIKE ?)")
        params.extend([like, like, like])
    if scope:
        clauses.append("(scope = ? OR scope = '')")
        params.append(scope)
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM long_term_lessons
            WHERE {' OR '.join(clauses)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_dict(row) for row in rows]


def log_turn(
    db_path: str | Path,
    *,
    session_id: str,
    role: str,
    message: str,
    action: str = "",
    scope: str = "",
    metadata: dict[str, Any] | None = None,
) -> SamResult:
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO long_term_conversations(session_id, role, message, action, scope, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, message, action, scope, _json(metadata)),
            )
            conn.execute(
                """
                DELETE FROM long_term_conversations
                WHERE id NOT IN (
                    SELECT id FROM long_term_conversations ORDER BY id DESC LIMIT 400
                )
                """
            )
            conn.commit()
        return SamResult(status="success", summary="Conversation turn stored.", next_action="stop")
    except sqlite3.Error as exc:
        return SamResult(
            status="failed",
            summary="Failed to store conversation turn.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
        )


def recall_recent_conversation(db_path: str | Path, *, limit: int = 30) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM long_term_conversations
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_dict(row) for row in reversed(rows)]


def snapshot(db_path: str | Path, *, session_id: str, query: str = "", scope: str = "") -> dict[str, Any]:
    ensure_schema(db_path)
    return {
        "recent_conversation": recall_recent_conversation(db_path, limit=30),
        "relevant_facts": recall(db_path, query, scope=scope, limit=20) if query else [],
        "relevant_lessons": recall_lessons(db_path, query, scope=scope, limit=5) if query else [],
        "session_id": session_id,
        "snapshot_at": _now(),
    }


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True)


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["metadata"] = json.loads(item.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        item["metadata"] = {}
    return item


def _now() -> str:
    return datetime.now(UTC).isoformat()
