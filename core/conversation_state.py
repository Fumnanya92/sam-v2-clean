"""Conversation state engine for goal/slot tracking and follow-up resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from diagnostics.result import SamResult


@dataclass
class ConversationState:
    active_goal: str = ""
    goal_kind: str = ""
    status: str = "idle"
    confidence: str = "low"
    slots: dict[str, str] = field(default_factory=dict)
    last_resolution: str = ""

    @property
    def is_active(self) -> bool:
        return bool(self.active_goal and self.status in {"active", "awaiting_user"})

    def to_memory(self) -> dict[str, Any]:
        return {
            "active_goal": self.active_goal,
            "goal_kind": self.goal_kind,
            "status": self.status,
            "confidence": self.confidence,
            "slots": self.slots,
            "last_resolution": self.last_resolution,
        }

    @classmethod
    def from_memory(cls, memory_block: dict[str, Any] | None) -> "ConversationState":
        if not isinstance(memory_block, dict):
            return cls()
        raw = memory_block.get("conversation_state", {})
        if isinstance(raw, dict) and "value" in raw:
            raw = raw.get("value", {})
        if not isinstance(raw, dict):
            return cls()
        slots = raw.get("slots", {})
        if isinstance(slots, dict) and "value" in slots:
            slots = slots.get("value", {})
        if not isinstance(slots, dict):
            slots = {}
        return cls(
            active_goal=str(_unwrap(raw.get("active_goal", ""))),
            goal_kind=str(_unwrap(raw.get("goal_kind", ""))),
            status=str(_unwrap(raw.get("status", "idle")) or "idle"),
            confidence=str(_unwrap(raw.get("confidence", "low")) or "low"),
            slots={str(key): str(_unwrap(value)) for key, value in slots.items() if str(_unwrap(value)).strip()},
            last_resolution=str(_unwrap(raw.get("last_resolution", ""))),
        )


def _unwrap(value: Any) -> Any:
    while isinstance(value, dict) and "value" in value:
        value = value.get("value")
    return value


class ConversationStateEngine:
    """Tracks active goals and resolves follow-ups before intent execution."""

    def apply(self, text: str, request: Any, memory_block: dict[str, Any] | None) -> tuple[Any, ConversationState]:
        state = ConversationState.from_memory(memory_block)
        lowered = text.lower().strip()
        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        last_project_name = str(_unwrap(daily_state.get("last_project_name", ""))).strip()
        last_project_id = str(_unwrap(daily_state.get("last_project_id", ""))).strip()

        project_name = self._extract_project_name(text) or state.slots.get("project_query", "")
        if project_name:
            state.slots["project_query"] = project_name

        if "project" in lowered and any(token in lowered for token in ("need", "favour", "favor", "help", "check", "work on")):
            state.active_goal = "project_assistance"
            state.goal_kind = "project_assistance"
            state.status = "awaiting_user"
            state.confidence = "medium"
            if project_name:
                state.last_resolution = f"project={project_name}"

        if request.intent in {"clarify", "chat"} and state.goal_kind == "project_assistance":
            if self._wants_open(text) and state.slots.get("project_query"):
                request.intent = "open_project_folder"
                request.parameters = {"query": state.slots["project_query"]}
                request.needs_clarification = False
                request.clarification_question = ""
                state.status = "active"
                state.confidence = "high"
            elif self._uses_pronoun_project(text):
                # Confidence gate: if reference is pronoun but no project slot, ask.
                if not state.slots.get("project_query"):
                    request.intent = "clarify"
                    request.needs_clarification = True
                    request.clarification_question = "Which project should I use?"
                    state.status = "awaiting_user"
                else:
                    state.status = "awaiting_user"

        if request.intent in {"open_project_folder", "run_project", "project_details", "inspect_project_repo", "inspect_git_state"}:
            query = str(request.parameters.get("query", "")).strip()
            if not query:
                fallback = state.slots.get("project_query") or last_project_name or last_project_id
                if fallback:
                    request.parameters["query"] = fallback
                    state.status = "active"
                    state.confidence = "high"

        if request.intent == "scaffold_project":
            state.active_goal = "scaffold_project"
            state.goal_kind = "scaffold_project"
            state.status = "active"
            state.confidence = "high"
            if request.parameters.get("name"):
                state.slots["project_name"] = str(request.parameters["name"])
            if request.parameters.get("project_type"):
                state.slots["project_type"] = str(request.parameters["project_type"])

        if request.intent == "clarify" and "project" not in lowered and state.goal_kind == "project_assistance":
            # Don't let stale project goal hijack unrelated topics.
            state = ConversationState()

        return request, state

    def writeback(self, result: SamResult, prior_state: ConversationState) -> dict[str, Any]:
        state = ConversationState(
            active_goal=prior_state.active_goal,
            goal_kind=prior_state.goal_kind,
            status=prior_state.status,
            confidence=prior_state.confidence,
            slots=dict(prior_state.slots),
            last_resolution=prior_state.last_resolution,
        )
        if result.metadata.get("intent") in {"open_project_folder", "run_project", "project_details"} and result.ok:
            state.status = "resolved"
            state.confidence = "high"
            if result.metadata.get("name"):
                state.slots["project_query"] = str(result.metadata["name"])
        if result.metadata.get("intent") == "clarify" and state.active_goal:
            state.status = "awaiting_user"
        if result.metadata.get("intent") == "scaffold_project" and result.ok:
            state.status = "resolved"
        if result.metadata.get("intent") == "discovery_workflow":
            state.active_goal = "discovery"
            state.goal_kind = "discovery"
            state.status = "active" if result.metadata.get("candidate_count", 0) else "awaiting_user"
            state.confidence = "medium"
            if result.metadata.get("search_keyword"):
                state.slots["search_keyword"] = str(result.metadata["search_keyword"])
        result.metadata["conversation_state"] = state.to_memory()
        return state.to_memory()

    @staticmethod
    def _extract_project_name(text: str) -> str:
        match = re.search(r"\b(?:in|on|for)\s+the\s+(.+?)\s+project\b", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"\bfind\s+the\s+(.+?)\s+project\b", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _wants_open(text: str) -> bool:
        lowered = text.lower()
        return "open" in lowered or "show me" in lowered

    @staticmethod
    def _uses_pronoun_project(text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in ("it", "that project", "this project"))
