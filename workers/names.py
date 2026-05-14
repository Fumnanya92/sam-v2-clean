"""Persistent operational identities for Sam workers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerIdentity:
    name: str
    role: str
    responsibility: str


WORKER_IDENTITIES: dict[str, WorkerIdentity] = {
    "atlas": WorkerIdentity("Atlas", "research", "repo research and investigation"),
    "forge": WorkerIdentity("Forge", "implementation", "code changes and implementation"),
    "echo": WorkerIdentity("Echo", "observation", "logs, observations, and tracing"),
    "sentinel": WorkerIdentity("Sentinel", "validation", "security, validation, and safety checks"),
    "nova": WorkerIdentity("Nova", "discovery", "discovery, search, and indexing"),
    "pulse": WorkerIdentity("Pulse", "monitoring", "runtime monitoring and worker telemetry"),
    "vector": WorkerIdentity("Vector", "planning", "planning and decomposition"),
    "orbit": WorkerIdentity("Orbit", "orchestration", "orchestration and supervision"),
}


LEGACY_WORKER_TYPE_MAP: dict[str, str] = {
    "code": "forge",
    "dev": "forge",
    "write": "forge",
    "edit": "forge",
    "test": "sentinel",
    "security": "sentinel",
    "inspect": "atlas",
    "research": "atlas",
    "logs": "echo",
    "trace": "echo",
    "discovery": "nova",
    "search": "nova",
    "monitor": "pulse",
    "runtime": "pulse",
    "planning": "vector",
    "plan": "vector",
    "orchestration": "orbit",
    "executor": "orbit",
}


TOOL_WORKER_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("delegate_coding_task", "coding_model"), "forge"),
    (("discover", "discovery", "find", "search", "scan", "list_directory"), "nova"),
    (("repo", "git", "inspect", "read", "details", "recent_changes"), "atlas"),
    (("log", "trace", "observe"), "echo"),
    (("test", "syntax", "health", "compile", "approval", "security"), "sentinel"),
    (("create", "scaffold", "write", "edit", "replace", "run_project"), "forge"),
    (("worker", "runtime", "status", "monitor"), "pulse"),
    (("plan", "decompose", "delegate"), "vector"),
)


def resolve_worker_identity(
    worker_type: str = "",
    explicit_name: str = "",
    *,
    tool_name: str = "",
    action_category: str = "",
) -> WorkerIdentity:
    """Resolve a task to an operational worker identity.

    The mapping is by operational responsibility, not by feature or intent.
    """
    explicit_key = explicit_name.strip().lower()
    if explicit_key:
        for identity in WORKER_IDENTITIES.values():
            if explicit_key == identity.name.lower():
                return identity

    role_key = LEGACY_WORKER_TYPE_MAP.get(worker_type.strip().lower())
    if role_key:
        return WORKER_IDENTITIES[role_key]

    normalized_tool = tool_name.strip().lower()
    for keywords, identity_key in TOOL_WORKER_KEYWORDS:
        if any(keyword in normalized_tool for keyword in keywords):
            return WORKER_IDENTITIES[identity_key]

    normalized_action = action_category.strip().lower()
    if normalized_action in {"write_data", "execute_command"}:
        return WORKER_IDENTITIES["forge"]
    if normalized_action in {"read_data", "inspect_data"}:
        return WORKER_IDENTITIES["atlas"]

    return WORKER_IDENTITIES["orbit"]


def resolve_worker_name(worker_type: str, explicit_name: str = "") -> str:
    return resolve_worker_identity(worker_type, explicit_name).name


WORKER_DISPLAY_NAMES: dict[str, str] = {
    worker_type: resolve_worker_identity(worker_type).name
    for worker_type in LEGACY_WORKER_TYPE_MAP
}
