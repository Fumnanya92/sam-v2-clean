"""Centralized LLM-based action gate for Sam v2.

This module answers: "Should Sam take external action now, or should Sam respond conversationally?"

The action gate wraps the existing classify_request() LLM method (from llm/ollama.py)
and maps intents to execution decisions. It is the single authority preventing 
false-positive execution triggers from scattered keyword matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm import OllamaClient


@dataclass(frozen=True)
class ActionGateDecision:
    """Result of action gate judgment."""
    should_act: bool
    action_type: str
    reason: str
    confidence: float
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "should_act": self.should_act,
            "action_type": self.action_type,
            "reason": self.reason,
            "confidence": self.confidence,
        }


class ActionGate:
    """Single authority for deciding if user is requesting external action.
    
    Uses the existing classify_request() method from OllamaClient to intelligently
    classify user intent, then maps that intent to an action/conversational decision.
    """
    
    # Intents that require external action (execution)
    EXECUTION_INTENTS = {
        "autonomous_request",
        "delegate_coding_task",
        "scaffold_project",
        "run_project",
        "inspect_project_repo",
        "inspect_git_state",
        "read_file",
        "open_file",
        "list_directory",
        "open_folder",
        "open_project_folder",
        "check_python_syntax",
        "inspect_recent_changes",
        "scan_codebase_patterns",
        "create_goal",
        "create_task",
        "update_task",
        "push_changes",
        "cleanup_workspace_duplicates",
        "inspect_repo",
        "execute_project_task",
    }
    
    # Intents that are conversational only
    CONVERSATIONAL_INTENTS = {
        "chat",
        "clarify",
        "capabilities",
        "awareness_check",
        "propose_upgrade",
        "list_goals",
        "list_tasks",
        "list_approvals",
        "create_draft",
        "list_workflows",
        "list_projects",
        "project_details",
        "plan_project",
        "show_delegation",
        "show_project_progress",
        "show_project_status",
        "list_executor_tools",
        "list_worker_tasks",
        "show_coding_model",
        "plan_request",
    }

    ACTION_TYPES = {
        "none",
        "read_only",
        "code_change",
        "test_run",
        "browser",
        "desktop",
        "deploy",
        "git",
        "other",
    }
    
    def __init__(self, *, model_client: OllamaClient | None = None):
        self.model_client = model_client or OllamaClient()
    
    def decide(
        self,
        user_text: str,
        memory_block: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        known_projects: list[dict[str, str]] | None = None,
        workspace_root: str = "",
    ) -> ActionGateDecision:
        """Judge if user is clearly requesting external action.
        
        Uses classify_request() to intelligently classify intent, then maps
        the intent to should_act=true/false based on whether it requires action.
        
        Default: should_act=false (conversational) for any unknown or ambiguous case.
        """
        if not user_text or not user_text.strip():
            return ActionGateDecision(
                should_act=False,
                action_type="none",
                reason="Empty input",
                confidence=1.0,
            )
        
        text_lower = user_text.lower().strip()
        
        # Safety control commands always pass
        if text_lower in {"stop", "cancel", "abort"}:
            return ActionGateDecision(
                should_act=True,
                action_type="other",
                reason="User-initiated stop command",
                confidence=1.0,
            )
        
        # Explicit slash commands always pass
        if text_lower.startswith("/"):
            return ActionGateDecision(
                should_act=True,
                action_type="other",
                reason="Explicit slash command",
                confidence=1.0,
            )
        
        llm_decision = self._decide_with_action_gate_llm(
            user_text=user_text,
            memory_block=memory_block,
            capabilities=capabilities,
            known_projects=known_projects,
            workspace_root=workspace_root,
        )
        if llm_decision is not None:
            return llm_decision

        # Backward-compatible fallback for older test doubles or model clients
        # that do not expose the dedicated action-gate method.
        try:
            intent_result = self.model_client.classify_request(
                user_text,
                capabilities=capabilities or [],
                memory_block=memory_block,
                known_projects=known_projects,
                workspace_root=workspace_root,
            )
            
            intent = intent_result.intent.lower().strip()
            
            # Map intent to action decision
            if intent in self.EXECUTION_INTENTS:
                action_type = self._map_intent_to_action_type(intent)
                return ActionGateDecision(
                    should_act=True,
                    action_type=action_type,
                    reason=f"Intent classified as {intent}: requires external action",
                    confidence=self._confidence_to_float(intent_result.confidence),
                )
            
            if intent in self.CONVERSATIONAL_INTENTS:
                return ActionGateDecision(
                    should_act=False,
                    action_type="none",
                    reason=f"Intent classified as {intent}: conversational only",
                    confidence=self._confidence_to_float(intent_result.confidence),
                )
            
            # Unknown intent: default to conversational (safe)
            return ActionGateDecision(
                should_act=False,
                action_type="none",
                reason=f"Unknown intent '{intent}': defaulting to conversational",
                confidence=0.5,
            )
        
        except Exception as e:
            # If classification fails, default to conversational (safe)
            return ActionGateDecision(
                should_act=False,
                action_type="none",
                reason=f"Classification error: {type(e).__name__}, defaulting to conversational",
                confidence=0.3,
            )

    def _decide_with_action_gate_llm(
        self,
        *,
        user_text: str,
        memory_block: dict[str, Any] | None,
        capabilities: list[str] | None,
        known_projects: list[dict[str, str]] | None,
        workspace_root: str,
    ) -> ActionGateDecision | None:
        if not hasattr(self.model_client, "decide_action_gate"):
            return None
        try:
            raw = self.model_client.decide_action_gate(
                user_text,
                memory_block=memory_block,
                capabilities=capabilities or [],
                known_projects=known_projects,
                workspace_root=workspace_root,
            )
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None

        should_act = bool(raw.get("should_act", False))
        action_type = str(raw.get("action_type", "none") or "none").strip().lower()
        if action_type not in self.ACTION_TYPES:
            action_type = "other" if should_act else "none"
        if not should_act:
            action_type = "none"
        elif action_type == "none":
            action_type = "other"

        return ActionGateDecision(
            should_act=should_act,
            action_type=action_type,
            reason=str(raw.get("reason", "") or "LLM action gate decision"),
            confidence=self._confidence_to_float(raw.get("confidence", 0.5)),
        )
    
    @staticmethod
    def _map_intent_to_action_type(intent: str) -> str:
        """Map intent to action_type category."""
        intent = intent.lower()
        
        if "read" in intent or "inspect" in intent or "list" in intent or "scan" in intent or "check" in intent:
            return "read_only"
        elif "code" in intent or "edit" in intent or "implement" in intent or "scaffold" in intent or "delegate" in intent:
            return "code_change"
        elif "test" in intent or "run" in intent or "execute" in intent:
            return "test_run"
        elif "push" in intent or "deploy" in intent or "git" in intent:
            return "deploy"
        elif "open" in intent:
            return "browser"
        elif "cleanup" in intent or "delete" in intent:
            return "delete"
        else:
            return "other"
    
    @staticmethod
    def _confidence_to_float(confidence: str | float) -> float:
        """Convert confidence string or value to float."""
        if isinstance(confidence, float) or isinstance(confidence, int):
            return float(confidence)
        
        conf_str = str(confidence).lower().strip()
        if conf_str == "high":
            return 0.9
        elif conf_str == "medium":
            return 0.7
        elif conf_str == "low":
            return 0.5
        else:
            try:
                return float(conf_str)
            except (ValueError, TypeError):
                return 0.5
