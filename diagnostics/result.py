"""Structured result model for Sam v2."""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from .error_types import ErrorType


@dataclass
class SamResult:
    status: str
    summary: str
    error_type: Optional[ErrorType] = None
    error_message: Optional[str] = None
    next_action: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "success"
