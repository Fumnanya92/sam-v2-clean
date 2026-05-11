"""Data models for Sam v2 storage foundation."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuditEvent:
    event_type: str
    actor: str
    summary: str
    metadata_json: str = "{}"
    created_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class TaskRecord:
    title: str
    status: str = "pending"
    priority: str = "medium"
    notes: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    id: Optional[int] = None
