"""Authority decision engine for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .constants import AUTHORITY_REQUIREMENTS, describe_level

ActionCategory = str
Effect = Literal["allow", "deny", "require_approval"]
EmergencyState = Literal["normal", "paused", "killed"]


@dataclass
class PerActionOverride:
    action: ActionCategory
    allowed: bool
    role_id: str = ""
    requires_approval: bool = False


@dataclass
class ContextRule:
    id: str
    action: ActionCategory
    condition: Literal["time_range", "tool_name", "always"]
    params: dict
    effect: Effect
    description: str


@dataclass
class AuthorityConfig:
    default_level: int = 3
    governed_categories: list[ActionCategory] = field(default_factory=list)
    overrides: list[PerActionOverride] = field(default_factory=list)
    context_rules: list[ContextRule] = field(default_factory=list)
    emergency_state: EmergencyState = "normal"


@dataclass
class AuthorityDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    action_category: ActionCategory
    context_rule: str = ""


class AuthorityEngine:
    def __init__(self, config: AuthorityConfig | None = None) -> None:
        self.config = config or AuthorityConfig()
        self._temporary_grants: dict[str, list[ActionCategory]] = {}

    def check(
        self,
        *,
        agent_id: str,
        agent_level: int,
        role_id: str,
        tool_name: str,
        action_category: ActionCategory,
    ) -> AuthorityDecision:
        if self.config.emergency_state == "killed":
            return AuthorityDecision(False, False, "System is in emergency kill state.", action_category)
        if self.config.emergency_state == "paused":
            return AuthorityDecision(False, False, "System is paused.", action_category)

        if action_category in self._temporary_grants.get(agent_id, []):
            return AuthorityDecision(True, False, "Temporarily granted.", action_category)

        override = self._find_override(action_category, role_id)
        if override is not None:
            if not override.allowed:
                return AuthorityDecision(False, False, "Explicitly denied by override.", action_category)
            if override.requires_approval:
                return AuthorityDecision(True, True, "Override requires approval.", action_category)
            return AuthorityDecision(True, False, "Explicitly allowed by override.", action_category)

        rule = self._eval_context_rules(action_category, tool_name)
        if rule is not None:
            if rule.effect == "deny":
                return AuthorityDecision(False, False, rule.description, action_category, rule.id)
            if rule.effect == "require_approval":
                return AuthorityDecision(True, True, rule.description, action_category, rule.id)
            return AuthorityDecision(True, False, rule.description, action_category, rule.id)

        effective_level = max(agent_level, self.config.default_level)
        required_level = AUTHORITY_REQUIREMENTS.get(action_category, 10)
        if effective_level < required_level:
            return AuthorityDecision(
                False,
                False,
                f"Level {effective_level} below required {required_level}.",
                action_category,
            )

        if action_category in self.config.governed_categories:
            return AuthorityDecision(
                True,
                True,
                f"{action_category} is governed and requires approval.",
                action_category,
            )

        return AuthorityDecision(
            True,
            False,
            f"Level {effective_level} meets requirement {required_level}.",
            action_category,
        )

    def grant_temporary(self, agent_id: str, categories: list[ActionCategory]) -> None:
        granted = self._temporary_grants.setdefault(agent_id, [])
        for category in categories:
            if category not in granted:
                granted.append(category)

    def revoke_temporary(self, agent_id: str) -> None:
        self._temporary_grants.pop(agent_id, None)

    def add_override(self, override: PerActionOverride) -> None:
        self.remove_override(override.action, override.role_id)
        self.config.overrides.append(override)

    def remove_override(self, action: ActionCategory, role_id: str = "") -> None:
        self.config.overrides = [
            item for item in self.config.overrides
            if not (item.action == action and item.role_id == role_id)
        ]

    def add_context_rule(self, rule: ContextRule) -> None:
        self.config.context_rules.append(rule)

    def remove_context_rule(self, rule_id: str) -> None:
        self.config.context_rules = [item for item in self.config.context_rules if item.id != rule_id]

    def set_emergency_state(self, state: EmergencyState) -> None:
        self.config.emergency_state = state

    def describe_rules(self, agent_level: int, role_id: str) -> str:
        effective_level = max(agent_level, self.config.default_level)
        lines = [f"Authority level: {effective_level}/10 - {describe_level(effective_level)}"]
        if self.config.governed_categories:
            lines.append("Governed categories:")
            for category in self.config.governed_categories:
                lines.append(f"- {category}")
        role_overrides = [item for item in self.config.overrides if not item.role_id or item.role_id == role_id]
        for override in role_overrides:
            if override.allowed and override.requires_approval:
                status = "allowed with approval"
            elif override.allowed:
                status = "allowed"
            else:
                status = "denied"
            lines.append(f"- override {override.action}: {status}")
        return "\n".join(lines)

    def _find_override(self, action: ActionCategory, role_id: str) -> PerActionOverride | None:
        for override in self.config.overrides:
            if override.action == action and override.role_id == role_id:
                return override
        for override in self.config.overrides:
            if override.action == action and not override.role_id:
                return override
        return None

    def _eval_context_rules(self, action: ActionCategory, tool_name: str) -> ContextRule | None:
        for rule in self.config.context_rules:
            if rule.action != action:
                continue
            if rule.condition == "always":
                return rule
            if rule.condition == "time_range":
                hour = datetime.now().hour
                start = int(rule.params.get("start_hour", 0))
                end = int(rule.params.get("end_hour", 24))
                if start <= hour < end:
                    return rule
            if rule.condition == "tool_name":
                pattern = str(rule.params.get("tool_name", ""))
                if pattern and (tool_name == pattern or tool_name.startswith(pattern)):
                    return rule
        return None
