"""Human-friendly names for Sam v2 workers."""

from __future__ import annotations

WORKER_DISPLAY_NAMES: dict[str, str] = {
    "code": "Trinity",
    "test": "Nigel",
    "dev": "Priase",
}


def resolve_worker_name(worker_type: str, explicit_name: str = "") -> str:
    if explicit_name.strip():
        return explicit_name.strip()
    return WORKER_DISPLAY_NAMES.get(worker_type, worker_type.title())
