"""SQLite schema for Sam v2 workflow foundations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from storage.db import _connect

CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS goals (
        id TEXT PRIMARY KEY,
        parent_id TEXT,
        level TEXT NOT NULL DEFAULT 'task',
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        success_criteria TEXT NOT NULL DEFAULT '',
        time_horizon TEXT NOT NULL DEFAULT 'weekly',
        score REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'active',
        health TEXT NOT NULL DEFAULT 'on_track',
        deadline TEXT,
        tags_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_documents (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        content_type TEXT NOT NULL DEFAULT 'post',
        stage TEXT NOT NULL DEFAULT 'draft',
        tags_json TEXT NOT NULL DEFAULT '[]',
        history_json TEXT NOT NULL DEFAULT '[]',
        published_channel TEXT,
        published_result TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
]

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status)",
    "CREATE INDEX IF NOT EXISTS idx_goals_parent_id ON goals(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_documents_stage ON workflow_documents(stage)",
]


def ensure_workflow_schema(db_path: str | Path) -> SamResult:
    """Ensure workflow-related tables exist."""
    try:
        with _connect(db_path) as conn:
            for statement in CREATE_TABLES:
                conn.execute(statement)
            for statement in CREATE_INDEXES:
                conn.execute(statement)
            conn.commit()
        return SamResult(
            status="success",
            summary="Workflow schema initialized.",
            next_action="stop",
            metadata={"db_path": str(db_path)},
        )
    except sqlite3.Error as exc:
        return SamResult(
            status="failed",
            summary="Failed to initialize workflow schema.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
            metadata={"db_path": str(db_path)},
        )
