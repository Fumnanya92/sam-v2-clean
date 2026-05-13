"""Runtime request hint model.

The parsed intent label is only one weak signal on this object; runtime layers
enrich and override it using state, observations, and policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IntentRequest:
    intent: str
    parameters: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    needs_clarification: bool = False
    clarification_question: str = ""
    response_text: str = ""
    confidence: str = "low"
    source: str = "llm"
