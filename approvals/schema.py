"""SQLite schema for Sam v2 approvals."""

CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS approval_requests (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        tool_arguments_json TEXT NOT NULL DEFAULT '{}',
        action_category TEXT NOT NULL,
        urgency TEXT NOT NULL DEFAULT 'normal',
        reason TEXT NOT NULL DEFAULT '',
        context TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        decided_at TEXT,
        decided_by TEXT,
        executed_at TEXT,
        execution_result TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS authority_audit_log (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        action_category TEXT NOT NULL,
        authority_decision TEXT NOT NULL,
        approval_id TEXT,
        executed INTEGER NOT NULL DEFAULT 0,
        execution_time_ms INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
]

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_approval_requests_status ON approval_requests(status)",
    "CREATE INDEX IF NOT EXISTS idx_approval_requests_agent ON approval_requests(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_authority_audit_created_at ON authority_audit_log(created_at)",
]
