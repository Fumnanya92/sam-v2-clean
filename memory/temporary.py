"""Session-only temporary memory for Sam v2."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class TemporaryMemory:
    """Short-lived state for multi-step interactions."""

    def __init__(self, max_history: int = 5) -> None:
        self.max_history = max_history
        self.reset()

    def reset(self) -> None:
        self.pending_intent: str | None = None
        self.parameters: dict[str, Any] = {}
        self.current_question: str | None = None
        self.last_user_text: str | None = None
        self.last_ai_response: str | None = None
        self.last_search: dict[str, str] | None = None
        self.last_opened_app: str | None = None
        self.conversation_history: list[dict[str, str]] = []
        self.session_log: list[dict[str, str]] = []

    def set_pending_intent(self, intent: str) -> None:
        self.pending_intent = intent

    def clear_pending_intent(self) -> None:
        self.pending_intent = None
        self.parameters = {}
        self.current_question = None

    def update_parameters(self, new_params: dict[str, Any]) -> None:
        for key, value in new_params.items():
            if value not in (None, ""):
                self.parameters[key] = value

    def get_parameters(self) -> dict[str, Any]:
        return self.parameters.copy()

    def set_current_question(self, param_name: str) -> None:
        self.current_question = param_name

    def set_last_user_text(self, text: str) -> None:
        self.last_user_text = text
        self._add_to_history("user", text)

    def set_last_ai_response(self, text: str) -> None:
        self.last_ai_response = text
        self._add_to_history("ai", text)

    def set_last_search(self, query: str, answer: str) -> None:
        self.last_search = {"query": query, "answer": answer}

    def set_open_app(self, app_name: str) -> None:
        self.last_opened_app = app_name

    def get_history_for_prompt(self) -> str:
        return "\n".join(
            f"{entry['role'].capitalize()}: {entry['text']}"
            for entry in self.conversation_history
        )

    def get_context_summary(self) -> dict[str, Any]:
        return {
            "pending_intent": self.pending_intent,
            "parameters": self.parameters.copy(),
            "current_question": self.current_question,
            "last_search": self.last_search,
            "last_opened_app": self.last_opened_app,
            "last_user_text": self.last_user_text,
            "last_ai_response": self.last_ai_response,
        }

    def _add_to_history(self, role: str, text: str) -> None:
        self.conversation_history.append({"role": role, "text": text})
        if len(self.conversation_history) > self.max_history:
            self.conversation_history.pop(0)

        self.session_log.append(
            {
                "role": role,
                "text": text,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
        )
