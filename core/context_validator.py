"""Validates request context and asks for confirmation when creating in non-empty workspaces.

This module ensures SAM doesn't assume where to create new projects.
It validates the active workspace context and prompts for confirmation
when creating projects in existing workspaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContextValidation:
    """Result of context validation."""
    is_valid: bool
    requires_confirmation: bool
    confirmation_question: str = ""
    suggested_action: str = ""  # "ask_user" or "proceed"
    context_info: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_question": self.confirmation_question,
            "suggested_action": self.suggested_action,
            "context_info": self.context_info or {},
        }


class ContextValidator:
    """Validates request context and enforces confirmation for risky operations."""

    def validate_project_creation(
        self,
        user_request: str,
        active_project_path: str | Path | None = None,
        workspace_root: str | Path | None = None,
        existing_projects: list[dict[str, str]] | None = None,
        detected_intent: str = "",
    ) -> ContextValidation:
        """Validate if project creation request is unambiguous.
        
        Returns confirmation requirement if:
        - LLM detected explicit scaffold_project intent
        - Active context is already in a project
        - Request doesn't explicitly specify location
        
        Args:
            user_request: The user's input text
            active_project_path: Current active project (if any)
            workspace_root: Root workspace directory
            existing_projects: List of existing projects with paths
            detected_intent: Intent classified by LLM (e.g., "scaffold_project", "chat")
        """
        user_text_lower = user_request.lower().strip()
        
        # Only proceed if LLM detected explicit scaffold_project intent
        # Don't use keyword matching - let the LLM decide what's actually being asked
        if detected_intent.lower().strip() != "scaffold_project":
            return ContextValidation(
                is_valid=True,
                requires_confirmation=False,
                suggested_action="proceed",
            )
        
        # Check if an explicit path is mentioned
        has_explicit_path = self._has_explicit_path(user_request)
        if has_explicit_path:
            return ContextValidation(
                is_valid=True,
                requires_confirmation=False,
                suggested_action="proceed",
                context_info={"has_explicit_path": True},
            )
        
        # If in an active project context and no explicit path, need confirmation
        if active_project_path:
            active_path = Path(active_project_path)
            return ContextValidation(
                is_valid=True,
                requires_confirmation=True,
                confirmation_question=f"Should I create this in a new workspace, or add it to the existing project at '{active_path.name}'?",
                suggested_action="ask_user",
                context_info={
                    "active_project": str(active_path),
                    "reason": "ambiguous_location",
                },
            )
        
        # If workspace has existing projects and no explicit context, warn
        if existing_projects and len(existing_projects) > 0:
            project_names = [p.get("name", "unknown") for p in existing_projects[:3]]
            return ContextValidation(
                is_valid=True,
                requires_confirmation=True,
                confirmation_question=f"Create in new workspace or add to an existing project? (Found: {', '.join(project_names)})",
                suggested_action="ask_user",
                context_info={
                    "existing_projects_count": len(existing_projects),
                    "reason": "multiple_options",
                },
            )
        
        # Safe to proceed: no active project or existing context
        return ContextValidation(
            is_valid=True,
            requires_confirmation=False,
            suggested_action="proceed",
        )

    @staticmethod
    def _has_explicit_path(text: str) -> bool:
        """Check if request contains explicit file path or directory reference."""
        import re
        
        # Windows paths
        if re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", text):
            return True
        
        # Unix/relative paths with slashes
        if re.search(r"[./][^\r\n\"']*[/\\][^\r\n\"']*", text):
            return True
        
        # Explicit directory names or path separators
        if "/" in text or "\\" in text:
            return True
        
        return False


def validate_request_context(
    user_text: str,
    active_project_path: str | Path | None = None,
    workspace_root: str | Path | None = None,
    existing_projects: list[dict[str, str]] | None = None,
    detected_intent: str = "",
) -> ContextValidation:
    """Validate request context and return confirmation requirement.
    
    This is the main entry point for context validation in SAM.
    Only validates if LLM detected explicit scaffold_project intent.
    """
    validator = ContextValidator()
    return validator.validate_project_creation(
        user_text,
        active_project_path=active_project_path,
        workspace_root=workspace_root,
        existing_projects=existing_projects,
        detected_intent=detected_intent,
    )
