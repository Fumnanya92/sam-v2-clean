"""Long-term operational memory stored in Sam's SQLite database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult


_lock = Lock()

MEMORY_TYPES = {"short_term", "episodic", "semantic", "procedural"}
INTENT_TYPES = {
    "casual_chat",
    "planning",
    "architecture",
    "memory_update",
    "coding",
    "debugging",
    "review",
    "unclear",
}


@dataclass
class MemoryCandidate:
    content: str
    memory_type: str
    importance_score: int
    confidence_score: float
    source_conversation_id: str
    source_session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)
    related_project_id: str = ""
    explicit: bool = False

    def normalized(self) -> "MemoryCandidate":
        now = _now()
        memory_type = self.memory_type if self.memory_type in MEMORY_TYPES else "episodic"
        return MemoryCandidate(
            content=self.content.strip(),
            memory_type=memory_type,
            importance_score=max(1, min(10, int(self.importance_score))),
            confidence_score=max(0.0, min(1.0, float(self.confidence_score))),
            source_conversation_id=str(self.source_conversation_id),
            source_session_id=str(self.source_session_id),
            created_at=self.created_at or now,
            updated_at=self.updated_at or now,
            tags=[str(tag).strip().lower() for tag in self.tags if str(tag).strip()],
            related_project_id=self.related_project_id.strip(),
            explicit=self.explicit,
        )


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

CREATE TABLE IF NOT EXISTS long_term_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK(memory_type IN ('short_term', 'episodic', 'semantic', 'procedural')),
    importance_score INTEGER NOT NULL CHECK(importance_score BETWEEN 1 AND 10),
    confidence_score REAL NOT NULL CHECK(confidence_score >= 0 AND confidence_score <= 1),
    source_conversation_id TEXT NOT NULL DEFAULT '',
    related_project_id TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_accessed_at TEXT NOT NULL DEFAULT '',
    access_count INTEGER NOT NULL DEFAULT 0,
    consolidation_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_long_term_memories_type ON long_term_memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_long_term_memories_project ON long_term_memories(related_project_id);
CREATE INDEX IF NOT EXISTS idx_long_term_memories_updated ON long_term_memories(updated_at);
CREATE INDEX IF NOT EXISTS idx_long_term_memories_archived ON long_term_memories(archived);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    from_turn_id INTEGER NOT NULL DEFAULT 0,
    to_turn_id INTEGER NOT NULL DEFAULT 0,
    token_hint INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conversation_summaries_session ON conversation_summaries(session_id);

CREATE TABLE IF NOT EXISTS coding_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    related_project_id TEXT NOT NULL DEFAULT '',
    source_conversation_id TEXT NOT NULL DEFAULT '',
    planned_requirements TEXT NOT NULL DEFAULT '',
    implementation_status TEXT NOT NULL DEFAULT 'planned',
    missing_items TEXT NOT NULL DEFAULT '',
    test_status TEXT NOT NULL DEFAULT '',
    review_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_coding_reviews_project ON coding_reviews(related_project_id);
CREATE INDEX IF NOT EXISTS idx_coding_reviews_session ON coding_reviews(session_id);

CREATE TABLE IF NOT EXISTS memory_review_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_content TEXT NOT NULL DEFAULT '',
    memory_type TEXT NOT NULL DEFAULT '',
    importance_score INTEGER NOT NULL DEFAULT 0,
    confidence_score REAL NOT NULL DEFAULT 0,
    action TEXT NOT NULL DEFAULT '',
    rejection_reason TEXT NOT NULL DEFAULT '',
    dedupe_result TEXT NOT NULL DEFAULT '',
    existing_memory_id INTEGER,
    saved_memory_id INTEGER,
    source_conversation_id TEXT NOT NULL DEFAULT '',
    source_session_id TEXT NOT NULL DEFAULT '',
    related_project_id TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    review_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_memory_review_log_created ON memory_review_log(created_at);
CREATE INDEX IF NOT EXISTS idx_memory_review_log_action ON memory_review_log(action);
CREATE INDEX IF NOT EXISTS idx_memory_review_log_session ON memory_review_log(source_session_id);

CREATE TABLE IF NOT EXISTS compact_context_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    memory_ids_json TEXT NOT NULL DEFAULT '[]',
    memory_count INTEGER NOT NULL DEFAULT 0,
    query_terms_json TEXT NOT NULL DEFAULT '[]',
    active_project_id TEXT NOT NULL DEFAULT '',
    active_project_root TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_compact_context_log_session ON compact_context_log(session_id);

CREATE TABLE IF NOT EXISTS memory_consolidation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL DEFAULT '',
    memory_id INTEGER,
    merged_memory_id INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_memory_consolidation_log_action ON memory_consolidation_log(action);

CREATE TABLE IF NOT EXISTS project_states (
    project_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    current_focus TEXT NOT NULL DEFAULT '',
    next_steps_json TEXT NOT NULL DEFAULT '[]',
    related_memories_json TEXT NOT NULL DEFAULT '[]',
    implementation_status TEXT NOT NULL DEFAULT '',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    last_discussed_topic TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_project_states_status ON project_states(status);
CREATE INDEX IF NOT EXISTS idx_project_states_updated ON project_states(updated_at);

CREATE TABLE IF NOT EXISTS session_recaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    decisions_json TEXT NOT NULL DEFAULT '[]',
    goals_json TEXT NOT NULL DEFAULT '[]',
    unresolved_questions_json TEXT NOT NULL DEFAULT '[]',
    next_actions_json TEXT NOT NULL DEFAULT '[]',
    project_changes_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_session_recaps_session ON session_recaps(session_id);
CREATE INDEX IF NOT EXISTS idx_session_recaps_project ON session_recaps(project_id);

CREATE TABLE IF NOT EXISTS operational_tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'planned',
    linked_memories_json TEXT NOT NULL DEFAULT '[]',
    linked_recap TEXT NOT NULL DEFAULT '',
    planner_output_json TEXT NOT NULL DEFAULT '{}',
    execution_history_json TEXT NOT NULL DEFAULT '[]',
    review_history_json TEXT NOT NULL DEFAULT '[]',
    blocked_reasons_json TEXT NOT NULL DEFAULT '[]',
    completion_score REAL NOT NULL DEFAULT 0,
    auto_corrections_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_operational_tasks_state ON operational_tasks(state);
CREATE INDEX IF NOT EXISTS idx_operational_tasks_project ON operational_tasks(project_id);
"""


def ensure_schema(db_path: str | Path) -> SamResult:
    try:
        with _lock, _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            _ensure_memory_columns(conn)
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
    schema_result = ensure_schema(db_path)
    if not schema_result.ok:
        return schema_result
    try:
        with _lock, _connect(db_path) as conn:
            cur = conn.execute(
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
            conversation_id = int(cur.lastrowid)
        return SamResult(
            status="success",
            summary="Conversation turn stored.",
            next_action="stop",
            metadata={"conversation_id": conversation_id},
        )
    except sqlite3.Error as exc:
        return SamResult(
            status="failed",
            summary="Failed to store conversation turn.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
        )


def recall_recent_conversation(db_path: str | Path, *, limit: int = 30, session_id: str = "") -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    params.append(limit)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM long_term_conversations
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_dict(row) for row in reversed(rows)]


def classify_intent(user_text: str) -> str:
    """Classify high-level user intent for memory/context policy.

    This is deliberately conservative: coding requires explicit implementation,
    file-editing, test-running, or code-generation language. Thinking aloud stays
    in conversation/planning.
    """
    text = user_text.strip().lower()
    if not text:
        return "unclear"
    tokens = set(_tokens(text))

    if any(phrase in text for phrase in ("remember that", "remember this", "don't forget", "forget that", "update memory")):
        return "memory_update"
    if any(phrase in text for phrase in ("review this", "code review", "review my", "audit the implementation")):
        return "review"
    if any(phrase in text for phrase in ("debug", "failing test", "trace the bug", "fix the bug", "why is this failing")):
        return "debugging"
    coding_verbs = {"implement", "edit", "modify", "change", "patch", "build", "create", "generate", "run", "test"}
    coding_targets = {"file", "files", "repo", "repository", "code", "tests", "test", "app", "module", "function", "class"}
    explicit_file_signal = any(signal in text for signal in (".py", ".js", ".ts", ".tsx", ".dart", "package.json", "pyproject.toml"))
    if (tokens & coding_verbs and (tokens & coding_targets or explicit_file_signal)) or "run tests" in text or "write code" in text:
        return "coding"
    if any(phrase in text for phrase in ("architecture", "design the system", "system design", "refactor architecture")):
        return "architecture"
    if any(phrase in text for phrase in ("make a plan", "plan for", "let's plan", "think through", "roadmap", "strategy")):
        return "planning"

    casual_markers = {"hi", "hello", "hey", "thanks", "thank", "okay", "ok"}
    if tokens & casual_markers or len(tokens) <= 6:
        return "casual_chat"
    return "planning"


def extract_candidate_memories(
    *,
    source_conversation_id: str,
    source_session_id: str = "",
    user_text: str,
    assistant_text: str = "",
    intent: str = "",
    related_project_id: str = "",
    explicit: bool = False,
) -> list[MemoryCandidate]:
    """Extract durable memory candidates from a meaningful turn.

    The extractor is local and conservative. It catches explicit remember
    requests, decisions, preferences, plans, mistakes, milestones, and Sam work
    rules without persisting every casual exchange.
    """
    combined = " ".join(part.strip() for part in (user_text, assistant_text) if part and part.strip())
    if not combined:
        return []
    lowered = combined.lower()
    candidates: list[MemoryCandidate] = []

    explicit = explicit or any(phrase in lowered for phrase in ("remember that", "remember this", "don't forget"))
    if explicit:
        content = _strip_memory_command(user_text) or user_text.strip()
        candidates.append(
            MemoryCandidate(
                content=content,
                memory_type=classify_memory_content(content),
                importance_score=9,
                confidence_score=0.95,
                source_conversation_id=source_conversation_id,
                source_session_id=source_session_id,
                tags=["explicit", "user"],
                related_project_id=related_project_id,
                explicit=True,
            )
        )

    if any(marker in lowered for marker in ("we decided", "decision:", "decided to", "we will", "milestone", "completed", "migration is complete")):
        candidates.append(
            MemoryCandidate(
                content=_compact_sentence(combined),
                memory_type="episodic",
                importance_score=8,
                confidence_score=0.82,
                source_conversation_id=source_conversation_id,
                source_session_id=source_session_id,
                tags=["event", "decision"],
                related_project_id=related_project_id,
            )
        )

    if any(marker in lowered for marker in ("i prefer", "my preference", "working style", "communication style", "i like", "i don't like", "constraint")):
        candidates.append(
            MemoryCandidate(
                content=_compact_sentence(user_text),
                memory_type="semantic",
                importance_score=8,
                confidence_score=0.84,
                source_conversation_id=source_conversation_id,
                source_session_id=source_session_id,
                tags=["preference"],
                related_project_id=related_project_id,
            )
        )

    if any(marker in lowered for marker in ("sam should", "always ", "never ", "when coding", "when planning", "when reviewing", "do not route normal conversations")):
        candidates.append(
            MemoryCandidate(
                content=_compact_sentence(user_text),
                memory_type="procedural",
                importance_score=8,
                confidence_score=0.8,
                source_conversation_id=source_conversation_id,
                source_session_id=source_session_id,
                tags=["procedure"],
                related_project_id=related_project_id,
            )
        )

    if any(marker in lowered for marker in ("mistake", "bug", "broken", "failed because", "root cause", "missing item")):
        candidates.append(
            MemoryCandidate(
                content=_compact_sentence(combined),
                memory_type="episodic",
                importance_score=7,
                confidence_score=0.76,
                source_conversation_id=source_conversation_id,
                source_session_id=source_session_id,
                tags=["mistake", "lesson"],
                related_project_id=related_project_id,
            )
        )

    if intent in {"coding", "debugging", "review", "autonomous_request", "delegate_coding_task"} and assistant_text:
        candidates.append(
            MemoryCandidate(
                content=f"Coding work status: {_compact_sentence(assistant_text)}",
                memory_type="episodic",
                importance_score=6,
                confidence_score=0.7,
                source_conversation_id=source_conversation_id,
                source_session_id=source_session_id,
                tags=["coding", "status"],
                related_project_id=related_project_id,
            )
        )

    return _unique_candidates(candidates)


def classify_memory_content(content: str) -> str:
    lowered = content.lower()
    if any(marker in lowered for marker in ("prefer", "preference", "i like", "i don't like", "goal", "tool", "working style", "communication style")):
        return "semantic"
    if any(marker in lowered for marker in ("sam should", "always", "never", "when coding", "when planning", "when reviewing", "how sam")):
        return "procedural"
    if any(marker in lowered for marker in ("decided", "completed", "milestone", "mistake", "happened", "discovered")):
        return "episodic"
    return "semantic"


def save_memory_candidate(db_path: str | Path, candidate: MemoryCandidate) -> tuple[SamResult, int | None]:
    candidate = candidate.normalized()
    schema_result = ensure_schema(db_path)
    if not schema_result.ok:
        return schema_result, None
    generic_reason = _memory_rejection_reason(candidate)
    if generic_reason:
        _log_memory_review(
            db_path,
            candidate=candidate,
            action="rejected",
            rejection_reason=generic_reason,
            dedupe_result="not_checked",
            review_notes=f"Rejected before dedupe: {generic_reason}.",
        )
        return SamResult(status="success", summary=f"Memory rejected: {generic_reason}.", next_action="stop"), None

    existing = search_memories(
        db_path,
        candidate.content,
        memory_types=[candidate.memory_type],
        project_id=candidate.related_project_id,
        limit=10,
        min_score=0.45,
    )
    now = _now()
    if existing:
        best = existing[0]
        contradiction = _memory_contradiction(str(best.get("content", "")), candidate.content)
        existing_explicit = bool(best.get("metadata", {}).get("explicit", False)) if isinstance(best.get("metadata"), dict) else False
        if contradiction and not (candidate.explicit and not existing_explicit):
            _log_memory_review(
                db_path,
                candidate=candidate,
                action="conflict",
                rejection_reason="contradicted_existing_memory",
                dedupe_result="conflict",
                existing_memory_id=int(best["id"]),
                review_notes=f"Potential contradiction with memory {best['id']}: {contradiction}.",
            )
            return SamResult(status="success", summary="Memory conflict flagged for review.", next_action="stop"), None

        if contradiction and candidate.explicit and not existing_explicit:
            merged_content = candidate.content
            merge_reason = "explicit_newer_instruction_replaced_inferred_memory"
        else:
            should_update, merge_reason = _memory_update_decision(best, candidate)
            if not should_update:
                _log_memory_review(
                    db_path,
                    candidate=candidate,
                    action="rejected",
                    rejection_reason="duplicate",
                    dedupe_result="duplicate",
                    existing_memory_id=int(best["id"]),
                    saved_memory_id=int(best["id"]),
                    review_notes=merge_reason,
                )
                _log_consolidation(
                    db_path,
                    action="merged",
                    memory_id=int(best["id"]),
                    reason="duplicate_already_represented",
                    before=best,
                )
                return SamResult(status="success", summary="Memory duplicate rejected.", next_action="stop"), int(best["id"])
            merged_content = _merge_memory_text(str(best.get("content", "")), candidate.content)
        tags = sorted(set(_safe_json_list(best.get("tags_json", "[]"))) | set(candidate.tags))
        merged_confidence = _merged_confidence(float(best.get("confidence_score", 0.0)), candidate)
        metadata = dict(best.get("metadata", {})) if isinstance(best.get("metadata"), dict) else {}
        metadata["explicit"] = bool(metadata.get("explicit", False) or candidate.explicit)
        metadata["last_review_note"] = merge_reason
        try:
            with _lock, _connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE long_term_memories
                    SET content = ?, normalized_content = ?, importance_score = ?,
                        confidence_score = ?, source_conversation_id = ?,
                        related_project_id = ?, tags_json = ?, metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        merged_content,
                        _normalize_text(merged_content),
                        max(int(best.get("importance_score", 1)), candidate.importance_score),
                        merged_confidence,
                        candidate.source_conversation_id or str(best.get("source_conversation_id", "")),
                        candidate.related_project_id or str(best.get("related_project_id", "")),
                        json.dumps(tags, sort_keys=True),
                        _json(metadata),
                        now,
                        int(best["id"]),
                    ),
                )
                conn.commit()
            _log_memory_review(
                db_path,
                candidate=candidate,
                action="merged",
                dedupe_result="merged",
                existing_memory_id=int(best["id"]),
                saved_memory_id=int(best["id"]),
                review_notes=merge_reason,
            )
            _log_consolidation(
                db_path,
                action="merged",
                memory_id=int(best["id"]),
                reason="save_time_dedupe",
                before=best,
            )
            return SamResult(status="success", summary="Memory merged.", next_action="stop"), int(best["id"])
        except sqlite3.Error as exc:
            return SamResult(status="failed", summary="Failed to merge memory.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry"), None

    try:
        with _lock, _connect(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO long_term_memories(
                    content, normalized_content, memory_type, importance_score,
                    confidence_score, source_conversation_id, related_project_id,
                    tags_json, metadata_json, created_at, updated_at,
                    last_accessed_at, access_count, consolidation_count, archived
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.content,
                    _normalize_text(candidate.content),
                    candidate.memory_type,
                    candidate.importance_score,
                    candidate.confidence_score,
                    candidate.source_conversation_id,
                    candidate.related_project_id,
                    json.dumps(candidate.tags, sort_keys=True),
                    _json({"explicit": candidate.explicit}),
                    candidate.created_at or now,
                    candidate.updated_at or now,
                    "",
                    0,
                    0,
                    0,
                ),
            )
            conn.commit()
            memory_id = int(cur.lastrowid)
        _log_memory_review(
            db_path,
            candidate=candidate,
            action="saved",
            dedupe_result="new",
            saved_memory_id=memory_id,
            review_notes="New memory passed quality checks.",
        )
        return SamResult(status="success", summary="Memory stored.", next_action="stop", metadata={"memory_id": memory_id}), memory_id
    except sqlite3.Error as exc:
        return SamResult(status="failed", summary="Failed to store memory.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry"), None


def search_memories(
    db_path: str | Path,
    query: str,
    *,
    memory_types: list[str] | None = None,
    project_id: str = "",
    limit: int = 8,
    min_score: float = 0.1,
) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    terms = set(_tokens(query))
    if not terms:
        return []
    type_filter = [item for item in (memory_types or []) if item in MEMORY_TYPES]
    params: list[Any] = []
    clauses: list[str] = ["1 = 1"]
    if type_filter:
        clauses.append(f"memory_type IN ({','.join('?' for _ in type_filter)})")
        params.extend(type_filter)
    if project_id:
        clauses.append("(related_project_id = ? OR related_project_id = '')")
        params.append(project_id)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM long_term_memories
            WHERE {' AND '.join(clauses)} AND archived = 0
            ORDER BY importance_score DESC, updated_at DESC
            LIMIT 80
            """,
            params,
        ).fetchall()

    scored: list[dict[str, Any]] = []
    for row in rows:
        item = _row_dict(row)
        score = _similarity_terms(terms, set(_tokens(str(item.get("content", "")))))
        if project_id and str(item.get("related_project_id", "")) == project_id:
            score += 0.35
        score += min(0.25, int(item.get("access_count", 0) or 0) * 0.03)
        if _is_decayed(item):
            score -= 0.2
        if score >= min_score:
            item["relevance_score"] = round(score, 4)
            scored.append(item)
    scored.sort(key=lambda item: (float(item.get("relevance_score", 0)), int(item.get("importance_score", 0))), reverse=True)
    selected = scored[:limit]
    _record_memory_access(db_path, [int(item.get("id", 0) or 0) for item in selected])
    return selected


def recent_memory_review_log(
    db_path: str | Path,
    *,
    limit: int = 20,
    action: str = "",
    source_session_id: str = "",
) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if action:
        clauses.append("action = ?")
        params.append(action)
    if source_session_id:
        clauses.append("source_session_id = ?")
        params.append(source_session_id)
    params.append(limit)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM memory_review_log
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_review_row(row) for row in rows]


def memory_debug_report(
    db_path: str | Path,
    *,
    limit: int = 10,
    memory_type: str = "",
    project_id: str = "",
    tag: str = "",
) -> dict[str, Any]:
    ensure_schema(db_path)
    memories = _list_recent_memories(db_path, limit=limit, memory_type=memory_type, project_id=project_id, tag=tag)
    with _connect(db_path) as conn:
        compact_row = conn.execute(
            """
            SELECT * FROM compact_context_log
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    latest_context = _row_dict(compact_row) if compact_row is not None else {}
    if latest_context:
        latest_context["memory_ids"] = _safe_json_list(latest_context.get("memory_ids_json", "[]"))
        latest_context["query_terms"] = _safe_json_list(latest_context.get("query_terms_json", "[]"))
        latest_context.pop("memory_ids_json", None)
        latest_context.pop("query_terms_json", None)
    return {
        "recent_saved_memories": memories,
        "recent_rejected_memories": recent_memory_review_log(db_path, limit=limit, action="rejected"),
        "recent_conflicts": recent_memory_review_log(db_path, limit=limit, action="conflict"),
        "recent_review_log": recent_memory_review_log(db_path, limit=limit),
        "recent_consolidations": recent_consolidation_log(db_path, limit=limit),
        "archived_memories": _list_archived_memories(db_path, limit=limit),
        "active_project_state": list_project_states(db_path, status="active", limit=limit),
        "current_active_task": latest_operational_task(db_path),
        "latest_compact_context": latest_context,
    }


def recent_consolidation_log(db_path: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM memory_consolidation_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_consolidation_row(row) for row in rows]


def consolidate_memories(db_path: str | Path, *, stale_days: int = 90, limit: int = 250) -> SamResult:
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM long_term_memories
            WHERE archived = 0
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    memories = [_row_dict(row) for row in rows]
    merged = 0
    decayed = 0
    archived = 0

    consumed: set[int] = set()
    for index, current in enumerate(memories):
        current_id = int(current.get("id", 0) or 0)
        if current_id in consumed:
            continue
        for other in memories[index + 1:]:
            other_id = int(other.get("id", 0) or 0)
            if other_id in consumed:
                continue
            if current.get("memory_type") != other.get("memory_type"):
                continue
            if _memory_contradiction(str(current.get("content", "")), str(other.get("content", ""))):
                continue
            if _similarity_terms(set(_tokens(str(current.get("content", "")))), set(_tokens(str(other.get("content", ""))))) < 0.75:
                continue
            keep, discard = _choose_memory_to_keep(current, other)
            _merge_existing_memories(db_path, keep, discard, reason="consolidated_duplicate")
            consumed.add(int(discard.get("id", 0) or 0))
            merged += 1

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM long_term_memories
            WHERE archived = 0
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    for memory in [_row_dict(row) for row in rows]:
        action = _consolidation_action(memory, stale_days=stale_days)
        if action == "strengthen":
            _strengthen_memory(db_path, memory)
            decayed += 0
        elif action == "decay":
            _decay_memory(db_path, memory)
            decayed += 1
        elif action == "archive":
            _archive_memory(db_path, memory)
            archived += 1

    return SamResult(
        status="success",
        summary=f"Memory consolidation complete: {merged} merged, {decayed} decayed, {archived} archived.",
        next_action="stop",
        metadata={"merged": merged, "decayed": decayed, "archived": archived},
    )


def upsert_project_state(
    db_path: str | Path,
    *,
    project_id: str,
    title: str,
    description: str = "",
    status: str = "active",
    current_focus: str = "",
    next_steps: list[str] | None = None,
    related_memories: list[int] | None = None,
    implementation_status: str = "",
    blockers: list[str] | None = None,
    last_discussed_topic: str = "",
) -> tuple[SamResult, str | None]:
    ensure_schema(db_path)
    project_id = project_id.strip() or _project_id_from_title(title)
    title = title.strip()
    if not project_id or not title:
        return SamResult(status="failed", summary="Project id and title are required.", next_action="ask_user"), None
    now = _now()
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO project_states(
                    project_id, title, description, status, current_focus,
                    next_steps_json, related_memories_json, implementation_status,
                    blockers_json, last_discussed_topic, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    title = excluded.title,
                    description = COALESCE(NULLIF(excluded.description, ''), project_states.description),
                    status = excluded.status,
                    current_focus = COALESCE(NULLIF(excluded.current_focus, ''), project_states.current_focus),
                    next_steps_json = excluded.next_steps_json,
                    related_memories_json = excluded.related_memories_json,
                    implementation_status = COALESCE(NULLIF(excluded.implementation_status, ''), project_states.implementation_status),
                    blockers_json = excluded.blockers_json,
                    last_discussed_topic = COALESCE(NULLIF(excluded.last_discussed_topic, ''), project_states.last_discussed_topic),
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    title,
                    description,
                    status,
                    current_focus,
                    json.dumps(next_steps or []),
                    json.dumps([int(item) for item in related_memories or []]),
                    implementation_status,
                    json.dumps(blockers or []),
                    last_discussed_topic,
                    now,
                ),
            )
            conn.commit()
        return SamResult(status="success", summary="Project state stored.", next_action="stop", metadata={"project_id": project_id}), project_id
    except sqlite3.Error as exc:
        return SamResult(status="failed", summary="Failed to store project state.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry"), None


def list_project_states(
    db_path: str | Path,
    *,
    status: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE status = ?"
        params.append(status)
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM project_states
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_project_row(row) for row in rows]


def get_project_state(db_path: str | Path, project_id: str) -> dict[str, Any] | None:
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM project_states WHERE project_id = ?", (project_id,)).fetchone()
    return _project_row(row) if row is not None else None


def generate_session_recap(
    db_path: str | Path,
    *,
    session_id: str,
    project_id: str = "",
    limit: int = 80,
) -> tuple[SamResult, int | None]:
    ensure_schema(db_path)
    turns = recall_recent_conversation(db_path, limit=limit, session_id=session_id)
    if not turns:
        return SamResult(status="success", summary="No conversation turns to recap.", next_action="stop"), None
    decisions: list[str] = []
    goals: list[str] = []
    unresolved: list[str] = []
    next_actions: list[str] = []
    project_changes: list[str] = []
    for turn in turns:
        text = str(turn.get("message", ""))
        lowered = text.lower()
        if any(marker in lowered for marker in ("decided", "decision", "we will", "migration is complete")):
            decisions.append(_compact_sentence(text, 180))
        if any(marker in lowered for marker in ("goal", "plan", "focus")):
            goals.append(_compact_sentence(text, 180))
        if any(marker in lowered for marker in ("blocked", "blocker", "unresolved", "question")):
            unresolved.append(_compact_sentence(text, 180))
        if any(marker in lowered for marker in ("next", "todo", "follow up")):
            next_actions.append(_compact_sentence(text, 180))
        if any(marker in lowered for marker in ("project", "implementation", "architecture")):
            project_changes.append(_compact_sentence(text, 180))
    summary = _compact_sentence(
        f"Session recap for {session_id}: {len(decisions)} decision(s), {len(goals)} goal update(s), "
        f"{len(unresolved)} unresolved item(s), {len(next_actions)} next action(s).",
        500,
    )
    try:
        with _lock, _connect(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO session_recaps(
                    session_id, project_id, decisions_json, goals_json,
                    unresolved_questions_json, next_actions_json,
                    project_changes_json, summary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    project_id,
                    json.dumps(decisions[:12]),
                    json.dumps(goals[:12]),
                    json.dumps(unresolved[:12]),
                    json.dumps(next_actions[:12]),
                    json.dumps(project_changes[:12]),
                    summary,
                ),
            )
            conn.commit()
            recap_id = int(cur.lastrowid)
        return SamResult(status="success", summary="Session recap stored.", next_action="stop", metadata={"recap_id": recap_id}), recap_id
    except sqlite3.Error as exc:
        return SamResult(status="failed", summary="Failed to store session recap.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry"), None


def create_operational_task(
    db_path: str | Path,
    *,
    task_id: str,
    project_id: str = "",
    goal: str,
    planner_output: dict[str, Any],
    linked_memories: list[int] | None = None,
    linked_recap: str = "",
) -> SamResult:
    ensure_schema(db_path)
    now = _now()
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO operational_tasks(
                    task_id, project_id, goal, state, linked_memories_json,
                    linked_recap, planner_output_json, created_at, updated_at
                )
                VALUES (?, ?, ?, 'planned', ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    goal = excluded.goal,
                    planner_output_json = excluded.planner_output_json,
                    linked_memories_json = excluded.linked_memories_json,
                    linked_recap = excluded.linked_recap,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    project_id,
                    goal,
                    json.dumps([int(item) for item in linked_memories or []]),
                    linked_recap,
                    _json(planner_output),
                    now,
                    now,
                ),
            )
            conn.commit()
        return SamResult(status="success", summary="Operational task planned.", next_action="stop", metadata={"task_id": task_id})
    except sqlite3.Error as exc:
        return SamResult(status="failed", summary="Failed to store operational task.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry")


def update_operational_task(
    db_path: str | Path,
    *,
    task_id: str,
    state: str | None = None,
    execution_event: dict[str, Any] | None = None,
    review_event: dict[str, Any] | None = None,
    blocked_reason: str = "",
    completion_score: float | None = None,
    auto_correction: dict[str, Any] | None = None,
) -> SamResult:
    ensure_schema(db_path)
    task = get_operational_task(db_path, task_id)
    if task is None:
        return SamResult(status="failed", summary="Operational task not found.", next_action="ask_user", metadata={"task_id": task_id})
    execution_history = list(task.get("execution_history", []))
    review_history = list(task.get("review_history", []))
    blocked_reasons = list(task.get("blocked_reasons", []))
    auto_corrections = list(task.get("auto_corrections", []))
    if execution_event:
        execution_history.append(execution_event)
    if review_event:
        review_history.append(review_event)
    if blocked_reason:
        blocked_reasons.append(blocked_reason)
    if auto_correction:
        auto_corrections.append(auto_correction)
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                UPDATE operational_tasks
                SET state = COALESCE(?, state),
                    execution_history_json = ?,
                    review_history_json = ?,
                    blocked_reasons_json = ?,
                    completion_score = COALESCE(?, completion_score),
                    auto_corrections_json = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    state,
                    json.dumps(execution_history),
                    json.dumps(review_history),
                    json.dumps(blocked_reasons),
                    completion_score,
                    json.dumps(auto_corrections),
                    _now(),
                    task_id,
                ),
            )
            conn.commit()
        return SamResult(status="success", summary="Operational task updated.", next_action="stop", metadata={"task_id": task_id})
    except sqlite3.Error as exc:
        return SamResult(status="failed", summary="Failed to update operational task.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry")


def get_operational_task(db_path: str | Path, task_id: str) -> dict[str, Any] | None:
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM operational_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return _operational_task_row(row) if row is not None else None


def latest_operational_task(db_path: str | Path, *, project_id: str = "") -> dict[str, Any] | None:
    ensure_schema(db_path)
    params: list[Any] = []
    where = ""
    if project_id:
        where = "WHERE project_id = ?"
        params.append(project_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT * FROM operational_tasks
            {where}
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return _operational_task_row(row) if row is not None else None


def summarize_conversation_if_needed(
    db_path: str | Path,
    *,
    session_id: str,
    max_raw_turns: int = 24,
) -> SamResult:
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM long_term_conversations
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    if len(rows) <= max_raw_turns:
        return SamResult(status="success", summary="Conversation did not need summarization.", next_action="stop")

    older = rows[: max(0, len(rows) - max_raw_turns)]
    if not older:
        return SamResult(status="success", summary="Conversation did not need summarization.", next_action="stop")
    summary = _summarize_turns([_row_dict(row) for row in older])
    from_id = int(older[0]["id"])
    to_id = int(older[-1]["id"])
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO conversation_summaries(session_id, summary, from_turn_id, to_turn_id, token_hint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, summary, from_id, to_id, len(summary.split()), _now()),
            )
            conn.execute(
                """
                DELETE FROM long_term_conversations
                WHERE session_id = ? AND id <= ?
                """,
                (session_id, to_id),
            )
            conn.commit()
        return SamResult(status="success", summary="Conversation summarized.", next_action="stop", metadata={"from_turn_id": from_id, "to_turn_id": to_id})
    except sqlite3.Error as exc:
        return SamResult(status="failed", summary="Failed to summarize conversation.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry")


def latest_conversation_summary(db_path: str | Path, *, session_id: str) -> str:
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT summary FROM conversation_summaries
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 3
            """,
            (session_id,),
        ).fetchall()
    return "\n".join(str(row["summary"]) for row in reversed(rows) if str(row["summary"]).strip())


def create_coding_review(
    db_path: str | Path,
    *,
    session_id: str = "",
    related_project_id: str = "",
    source_conversation_id: str = "",
    planned_requirements: str = "",
    implementation_status: str = "planned",
    missing_items: str = "",
    test_status: str = "",
    review_notes: str = "",
) -> tuple[SamResult, int | None]:
    ensure_schema(db_path)
    try:
        with _lock, _connect(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO coding_reviews(
                    session_id, related_project_id, source_conversation_id,
                    planned_requirements, implementation_status, missing_items,
                    test_status, review_notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    related_project_id,
                    source_conversation_id,
                    planned_requirements,
                    implementation_status,
                    missing_items,
                    test_status,
                    review_notes,
                    _now(),
                ),
            )
            conn.commit()
            review_id = int(cur.lastrowid)
        return SamResult(status="success", summary="Coding review stored.", next_action="stop", metadata={"review_id": review_id}), review_id
    except sqlite3.Error as exc:
        return SamResult(status="failed", summary="Failed to store coding review.", error_type=ErrorType.FILE_ACCESS_ERROR, error_message=str(exc), next_action="retry"), None


def latest_coding_review(db_path: str | Path, *, session_id: str = "", project_id: str = "") -> dict[str, Any] | None:
    ensure_schema(db_path)
    clauses = ["1 = 1"]
    params: list[Any] = []
    if project_id:
        clauses.append("related_project_id = ?")
        params.append(project_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT * FROM coding_reviews
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return _row_dict(row) if row is not None else None


def build_compact_context(
    db_path: str | Path,
    *,
    session_id: str,
    query: str,
    active_project_id: str = "",
    active_project_root: str = "",
    recent_limit: int = 8,
) -> dict[str, Any]:
    ensure_schema(db_path)
    intent = classify_intent(query)
    project_state = get_project_state(db_path, active_project_id) if active_project_id else None
    project_query = " ".join(
        str(project_state.get(key, ""))
        for key in ("title", "current_focus", "last_discussed_topic", "implementation_status")
        if isinstance(project_state, dict)
    )
    retrieval_query = " ".join(part for part in (query, project_query) if part.strip())
    relevant = search_memories(db_path, retrieval_query, project_id=active_project_id or active_project_root, limit=16)
    relevant = _optimize_context_memories(relevant, limit=8)
    grouped: dict[str, list[dict[str, Any]]] = {memory_type: [] for memory_type in MEMORY_TYPES}
    injected_memory_ids: list[int] = []
    for item in relevant:
        memory_type = str(item.get("memory_type", "episodic"))
        memory_id = int(item.get("id", 0) or 0)
        if memory_id:
            injected_memory_ids.append(memory_id)
        grouped.setdefault(memory_type, []).append(
            {
                "id": memory_id,
                "content": item.get("content", ""),
                "importance_score": item.get("importance_score", 0),
                "confidence_score": item.get("confidence_score", 0),
                "tags": _safe_json_list(item.get("tags_json", "[]")),
                "related_project_id": item.get("related_project_id", ""),
                "priority_reason": item.get("priority_reason", "relevant_to_query"),
            }
        )
    _log_compact_context(
        db_path,
        session_id=session_id,
        intent=intent,
        memory_ids=injected_memory_ids,
        query=query,
        active_project_id=active_project_id,
        active_project_root=active_project_root,
    )
    return {
        "intent": intent,
        "active_project": {
            "project_id": active_project_id,
            "root_path": active_project_root,
            "state": project_state or {},
        },
        "recent_summary": latest_conversation_summary(db_path, session_id=session_id),
        "recent_conversation": recall_recent_conversation(db_path, limit=recent_limit, session_id=session_id),
        "relevant_memories": grouped,
        "injected_memory_ids": injected_memory_ids,
        "coding_review": latest_coding_review(db_path, session_id=session_id, project_id=active_project_id or active_project_root) or {},
        "context_policy": {
            "raw_history_limit": recent_limit,
            "coding_requires_explicit_request": True,
            "persist_importance_threshold": 6,
        },
    }


def snapshot(db_path: str | Path, *, session_id: str, query: str = "", scope: str = "") -> dict[str, Any]:
    ensure_schema(db_path)
    compact_context = build_compact_context(
        db_path,
        session_id=session_id,
        query=query,
        active_project_root=scope,
        recent_limit=8,
    )
    return {
        "recent_conversation": compact_context["recent_conversation"],
        "recent_summary": compact_context["recent_summary"],
        "relevant_facts": recall(db_path, query, scope=scope, limit=20) if query else [],
        "relevant_lessons": recall_lessons(db_path, query, scope=scope, limit=5) if query else [],
        "relevant_memories": compact_context["relevant_memories"],
        "compact_context": compact_context,
        "detected_intent": compact_context["intent"],
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
    if row is None:
        return {}
    item = dict(row)
    try:
        item["metadata"] = json.loads(item.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        item["metadata"] = {}
    return item


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _memory_rejection_reason(candidate: MemoryCandidate) -> str:
    if not candidate.content.strip() or _is_generic_memory(candidate.content):
        return "empty_or_generic"
    if candidate.memory_type == "short_term":
        return "temporary_only"
    if candidate.confidence_score < 0.45 and not candidate.explicit:
        return "low_confidence"
    if candidate.importance_score < 6 and not candidate.explicit:
        return "low_importance"
    return ""


def _is_generic_memory(content: str) -> bool:
    tokens = _tokens(content)
    if len(tokens) < 3:
        return True
    lowered = content.strip().lower()
    generic = {
        "ok",
        "okay",
        "thanks",
        "thank you",
        "got it",
        "hello",
        "hi",
        "yes",
        "no",
        "done",
    }
    return lowered in generic


def _memory_update_decision(existing: dict[str, Any], candidate: MemoryCandidate) -> tuple[bool, str]:
    existing_content = str(existing.get("content", ""))
    existing_importance = int(existing.get("importance_score", 0) or 0)
    existing_confidence = float(existing.get("confidence_score", 0.0) or 0.0)
    existing_tags = set(_safe_json_list(existing.get("tags_json", "[]")))
    existing_metadata = existing.get("metadata", {}) if isinstance(existing.get("metadata"), dict) else {}
    existing_explicit = bool(existing_metadata.get("explicit", False))

    if existing_explicit and not candidate.explicit and candidate.confidence_score < existing_confidence:
        return False, "Rejected uncertain inferred update to stable explicit memory."

    improvements: list[str] = []
    if candidate.explicit and not existing_explicit:
        improvements.append("explicit_user_instruction")
    if candidate.importance_score > existing_importance:
        improvements.append("higher_importance")
    if candidate.confidence_score >= existing_confidence + 0.1:
        improvements.append("higher_confidence")
    if len(_tokens(candidate.content)) > len(_tokens(existing_content)) + 3:
        improvements.append("more_specific_content")
    if set(candidate.tags) - existing_tags:
        improvements.append("new_tags")

    if not improvements:
        return False, "Candidate did not improve existing memory."
    return True, "Merged because candidate added: " + ", ".join(improvements) + "."


def _merged_confidence(existing_confidence: float, candidate: MemoryCandidate) -> float:
    if candidate.explicit:
        return max(existing_confidence, candidate.confidence_score)
    if candidate.confidence_score >= existing_confidence + 0.1:
        return min(1.0, max(existing_confidence, candidate.confidence_score))
    return existing_confidence


def _memory_contradiction(existing: str, candidate: str) -> str:
    existing_terms = set(_tokens(existing))
    candidate_terms = set(_tokens(candidate))
    if _similarity_terms(existing_terms, candidate_terms) < 0.35:
        return ""
    existing_neg = _has_negative_instruction(existing)
    candidate_neg = _has_negative_instruction(candidate)
    if existing_neg != candidate_neg:
        return "opposing_instruction_polarity"
    existing_pref = _preference_polarity(existing)
    candidate_pref = _preference_polarity(candidate)
    if existing_pref and candidate_pref and existing_pref != candidate_pref:
        return "opposing_preference_polarity"
    return ""


def _has_negative_instruction(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("do not", "don't", "dont", "never", "avoid", "should not", "must not"))


def _preference_polarity(text: str) -> str:
    lowered = text.lower()
    negative = any(marker in lowered for marker in ("i don't like", "i do not like", "dislike", "hate", "do not prefer", "don't prefer"))
    positive = any(marker in lowered for marker in ("i like", "i prefer", "prefer", "likes"))
    if negative:
        return "negative"
    if positive:
        return "positive"
    return ""


def _log_memory_review(
    db_path: str | Path,
    *,
    candidate: MemoryCandidate,
    action: str,
    rejection_reason: str = "",
    dedupe_result: str = "",
    existing_memory_id: int | None = None,
    saved_memory_id: int | None = None,
    review_notes: str = "",
) -> None:
    ensure_schema(db_path)
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_review_log(
                    candidate_content, memory_type, importance_score, confidence_score,
                    action, rejection_reason, dedupe_result, existing_memory_id,
                    saved_memory_id, source_conversation_id, source_session_id,
                    related_project_id, tags_json, review_notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _compact_sentence(candidate.content, limit=600),
                    candidate.memory_type,
                    candidate.importance_score,
                    candidate.confidence_score,
                    action,
                    rejection_reason,
                    dedupe_result,
                    existing_memory_id,
                    saved_memory_id,
                    candidate.source_conversation_id,
                    candidate.source_session_id,
                    candidate.related_project_id,
                    json.dumps(candidate.tags, sort_keys=True),
                    review_notes,
                ),
            )
            conn.commit()
    except sqlite3.Error:
        return


def _log_compact_context(
    db_path: str | Path,
    *,
    session_id: str,
    intent: str,
    memory_ids: list[int],
    query: str,
    active_project_id: str,
    active_project_root: str,
) -> None:
    ensure_schema(db_path)
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO compact_context_log(
                    session_id, intent, memory_ids_json, memory_count,
                    query_terms_json, active_project_id, active_project_root
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    intent,
                    json.dumps(memory_ids),
                    len(memory_ids),
                    json.dumps(_tokens(query)[:12]),
                    active_project_id,
                    active_project_root,
                ),
            )
            conn.commit()
    except sqlite3.Error:
        return


def _review_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["tags"] = _safe_json_list(item.get("tags_json", "[]"))
    item.pop("tags_json", None)
    return item


def _list_recent_memories(
    db_path: str | Path,
    *,
    limit: int,
    memory_type: str = "",
    project_id: str = "",
    tag: str = "",
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if memory_type:
        clauses.append("memory_type = ?")
        params.append(memory_type)
    if project_id:
        clauses.append("related_project_id = ?")
        params.append(project_id)
    params.append(limit * 4 if tag else limit)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM long_term_memories
            {where}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    memories = [_row_dict(row) for row in rows]
    for item in memories:
        item["tags"] = _safe_json_list(item.get("tags_json", "[]"))
        item.pop("tags_json", None)
        item.pop("metadata_json", None)
    if tag:
        memories = [item for item in memories if tag in item.get("tags", [])]
    return memories[:limit]


def _ensure_memory_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(long_term_memories)").fetchall()}
    columns = {
        "last_accessed_at": "TEXT NOT NULL DEFAULT ''",
        "access_count": "INTEGER NOT NULL DEFAULT 0",
        "consolidation_count": "INTEGER NOT NULL DEFAULT 0",
        "archived": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE long_term_memories ADD COLUMN {name} {definition}")


def _record_memory_access(db_path: str | Path, memory_ids: list[int]) -> None:
    ids = [memory_id for memory_id in memory_ids if memory_id]
    if not ids:
        return
    now = _now()
    try:
        with _lock, _connect(db_path) as conn:
            conn.executemany(
                """
                UPDATE long_term_memories
                SET access_count = access_count + 1,
                    last_accessed_at = ?,
                    importance_score = CASE
                        WHEN importance_score < 10 AND confidence_score >= 0.75 THEN importance_score + 1
                        ELSE importance_score
                    END
                WHERE id = ? AND archived = 0
                """,
                [(now, memory_id) for memory_id in ids],
            )
            conn.commit()
    except sqlite3.Error:
        return


def _is_decayed(memory: dict[str, Any]) -> bool:
    metadata = memory.get("metadata", {}) if isinstance(memory.get("metadata"), dict) else {}
    if metadata.get("explicit") or memory.get("memory_type") == "procedural":
        return False
    if float(memory.get("confidence_score", 0.0) or 0.0) < 0.55:
        return True
    if int(memory.get("access_count", 0) or 0) == 0 and _days_since(str(memory.get("updated_at", ""))) > 45:
        return True
    return False


def _consolidation_action(memory: dict[str, Any], *, stale_days: int) -> str:
    metadata = memory.get("metadata", {}) if isinstance(memory.get("metadata"), dict) else {}
    memory_type = str(memory.get("memory_type", ""))
    content = str(memory.get("content", ""))
    if metadata.get("explicit") or memory_type == "procedural":
        return "strengthen" if int(memory.get("access_count", 0) or 0) >= 3 else ""
    if memory_type == "episodic" and any(marker in content.lower() for marker in ("milestone", "completed", "decided", "migration is complete")):
        return ""
    if int(memory.get("access_count", 0) or 0) >= 3 and float(memory.get("confidence_score", 0.0) or 0.0) >= 0.7:
        return "strengthen"
    stale = _days_since(str(memory.get("last_accessed_at") or memory.get("updated_at", ""))) > stale_days
    low_value = int(memory.get("importance_score", 0) or 0) <= 4 or float(memory.get("confidence_score", 0.0) or 0.0) < 0.45
    if stale and low_value:
        return "archive"
    if stale or low_value:
        return "decay"
    return ""


def _strengthen_memory(db_path: str | Path, memory: dict[str, Any]) -> None:
    before = dict(memory)
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                UPDATE long_term_memories
                SET importance_score = MIN(10, importance_score + 1),
                    confidence_score = MIN(1.0, confidence_score + 0.03),
                    consolidation_count = consolidation_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (_now(), int(memory["id"])),
            )
            conn.commit()
        _log_consolidation(db_path, action="strengthened", memory_id=int(memory["id"]), reason="recurring_high_confidence", before=before)
    except sqlite3.Error:
        return


def _decay_memory(db_path: str | Path, memory: dict[str, Any]) -> None:
    before = dict(memory)
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                UPDATE long_term_memories
                SET importance_score = MAX(1, importance_score - 1),
                    consolidation_count = consolidation_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (_now(), int(memory["id"])),
            )
            conn.commit()
        _log_consolidation(db_path, action="decayed", memory_id=int(memory["id"]), reason="stale_or_low_confidence", before=before)
    except sqlite3.Error:
        return


def _archive_memory(db_path: str | Path, memory: dict[str, Any]) -> None:
    before = dict(memory)
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                UPDATE long_term_memories
                SET archived = 1,
                    consolidation_count = consolidation_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (_now(), int(memory["id"])),
            )
            conn.commit()
        _log_consolidation(db_path, action="archived", memory_id=int(memory["id"]), reason="stale_low_value", before=before)
    except sqlite3.Error:
        return


def _merge_existing_memories(db_path: str | Path, keep: dict[str, Any], discard: dict[str, Any], *, reason: str) -> None:
    keep_id = int(keep.get("id", 0) or 0)
    discard_id = int(discard.get("id", 0) or 0)
    if not keep_id or not discard_id or keep_id == discard_id:
        return
    merged_content = _merge_memory_text(str(keep.get("content", "")), str(discard.get("content", "")))
    tags = sorted(set(_safe_json_list(keep.get("tags_json", "[]"))) | set(_safe_json_list(discard.get("tags_json", "[]"))))
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                UPDATE long_term_memories
                SET content = ?, normalized_content = ?, importance_score = ?,
                    confidence_score = ?, tags_json = ?, consolidation_count = consolidation_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    merged_content,
                    _normalize_text(merged_content),
                    max(int(keep.get("importance_score", 1) or 1), int(discard.get("importance_score", 1) or 1)),
                    max(float(keep.get("confidence_score", 0) or 0), float(discard.get("confidence_score", 0) or 0)),
                    json.dumps(tags),
                    _now(),
                    keep_id,
                ),
            )
            conn.execute(
                """
                UPDATE long_term_memories
                SET archived = 1, consolidation_count = consolidation_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (_now(), discard_id),
            )
            conn.commit()
        _log_consolidation(db_path, action="merged", memory_id=keep_id, merged_memory_id=discard_id, reason=reason, before=discard)
    except sqlite3.Error:
        return


def _choose_memory_to_keep(left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    left_meta = left.get("metadata", {}) if isinstance(left.get("metadata"), dict) else {}
    right_meta = right.get("metadata", {}) if isinstance(right.get("metadata"), dict) else {}
    left_score = int(left.get("importance_score", 0) or 0) + float(left.get("confidence_score", 0) or 0) + int(left.get("access_count", 0) or 0)
    right_score = int(right.get("importance_score", 0) or 0) + float(right.get("confidence_score", 0) or 0) + int(right.get("access_count", 0) or 0)
    if left_meta.get("explicit") and not right_meta.get("explicit"):
        return left, right
    if right_meta.get("explicit") and not left_meta.get("explicit"):
        return right, left
    return (left, right) if left_score >= right_score else (right, left)


def _log_consolidation(
    db_path: str | Path,
    *,
    action: str,
    memory_id: int,
    reason: str,
    merged_memory_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_consolidation_log(action, memory_id, merged_memory_id, reason, before_json, after_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (action, memory_id, merged_memory_id, reason, _json(before), _json(after)),
            )
            conn.commit()
    except sqlite3.Error:
        return


def _consolidation_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("before_json", "after_json"):
        try:
            item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
        except json.JSONDecodeError:
            item[key.replace("_json", "")] = {}
        item.pop(key, None)
    return item


def _list_archived_memories(db_path: str | Path, *, limit: int) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM long_term_memories
            WHERE archived = 1
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_dict(row) for row in rows]


def _project_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["next_steps"] = _safe_json_list(item.get("next_steps_json", "[]"))
    item["related_memories"] = [int(value) for value in _safe_json_list(item.get("related_memories_json", "[]")) if str(value).isdigit()]
    item["blockers"] = _safe_json_list(item.get("blockers_json", "[]"))
    for key in ("next_steps_json", "related_memories_json", "blockers_json"):
        item.pop(key, None)
    return item


def _operational_task_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["linked_memories"] = [int(value) for value in _safe_json_list(item.get("linked_memories_json", "[]")) if str(value).isdigit()]
    item["planner_output"] = _safe_json_obj(item.get("planner_output_json", "{}"))
    item["execution_history"] = _safe_json_array_obj(item.get("execution_history_json", "[]"))
    item["review_history"] = _safe_json_array_obj(item.get("review_history_json", "[]"))
    item["blocked_reasons"] = _safe_json_list(item.get("blocked_reasons_json", "[]"))
    item["auto_corrections"] = _safe_json_array_obj(item.get("auto_corrections_json", "[]"))
    for key in (
        "linked_memories_json",
        "planner_output_json",
        "execution_history_json",
        "review_history_json",
        "blocked_reasons_json",
        "auto_corrections_json",
    ):
        item.pop(key, None)
    return item


def _project_id_from_title(title: str) -> str:
    tokens = _tokens(title)
    return "-".join(tokens[:6])


def _days_since(timestamp: str) -> int:
    if not timestamp:
        return 999
    try:
        cleaned = timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - dt.astimezone(UTC)).days)
    except ValueError:
        return 999


def _optimize_context_memories(memories: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_norms: list[set[str]] = []
    procedural_count = 0
    for item in memories:
        terms = set(_tokens(str(item.get("content", ""))))
        if any(_similarity_terms(terms, existing) >= 0.7 for existing in seen_norms):
            continue
        memory_type = str(item.get("memory_type", ""))
        if memory_type == "procedural":
            procedural_count += 1
            if procedural_count > 2:
                continue
            item["priority_reason"] = "procedural_rule"
        elif item.get("related_project_id"):
            item["priority_reason"] = "project_linked"
        elif memory_type == "episodic":
            item["priority_reason"] = "recent_decision_or_event"
        else:
            item["priority_reason"] = "semantic_relevance"
        selected.append(item)
        seen_norms.append(terms)
        if len(selected) >= limit:
            break
    return selected


def _tokens(text: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "you",
        "sam",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "into",
        "from",
        "when",
        "what",
        "where",
        "your",
        "our",
        "but",
    }
    return [token for token in cleaned.split() if len(token) > 2 and token not in stop]


def _normalize_text(text: str) -> str:
    return " ".join(_tokens(text))


def _similarity_terms(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return overlap / max(1, min(len(left), len(right)))


def _strip_memory_command(text: str) -> str:
    lowered = text.lower()
    for marker in ("remember that", "remember this", "don't forget that", "dont forget that"):
        idx = lowered.find(marker)
        if idx >= 0:
            return text[idx + len(marker):].strip(" :.-")
    return text.strip()


def _compact_sentence(text: str, limit: int = 500) -> str:
    compact = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _unique_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[MemoryCandidate] = []
    for candidate in candidates:
        normalized = candidate.normalized()
        key = (normalized.memory_type, _normalize_text(normalized.content))
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _merge_memory_text(existing: str, newer: str) -> str:
    if _normalize_text(newer) in _normalize_text(existing):
        return existing
    if _normalize_text(existing) in _normalize_text(newer):
        return newer
    return _compact_sentence(f"{existing} Updated: {newer}", limit=900)


def _safe_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _safe_json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_json_array_obj(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _summarize_turns(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return ""
    first = turns[0]
    last = turns[-1]
    topics: list[str] = []
    for item in turns:
        message = str(item.get("message", ""))
        action = str(item.get("action", ""))
        if action and action not in topics:
            topics.append(action)
        for token in _tokens(message):
            if token not in topics and len(topics) < 12:
                topics.append(token)
    return _compact_sentence(
        "Older conversation summary: "
        f"{len(turns)} turn(s) from {first.get('created_at', '')} to {last.get('created_at', '')}. "
        f"Topics/actions: {', '.join(topics[:12])}. "
        f"First user/sam signal: {first.get('role', '')}: {first.get('message', '')}. "
        f"Latest older signal: {last.get('role', '')}: {last.get('message', '')}.",
        limit=1000,
    )
