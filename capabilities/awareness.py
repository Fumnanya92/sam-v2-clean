"""Capability and self-awareness service for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.projects import ProjectRegistry
from sam_v2.upgrades import UpgradeProposalManager

from .registry import CapabilityRegistry


@dataclass
class MissingCapability:
    name: str
    description: str
    upgradeable: bool = True


class CapabilityAwarenessService:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        project_registry: ProjectRegistry,
        upgrade_manager: UpgradeProposalManager,
    ) -> None:
        self.registry = registry
        self.project_registry = project_registry
        self.upgrade_manager = upgrade_manager
        self.missing_capabilities = [
            MissingCapability("voice_input", "Real voice capture path is not migrated yet."),
            MissingCapability("react_dashboard", "React dashboard shell is not migrated yet."),
            MissingCapability("browser_worker", "Dedicated browser worker is not migrated yet."),
            MissingCapability("meeting_assistant", "Meeting assistant workflows are not migrated yet."),
            MissingCapability("remote_access", "Remote/local machine access flow is not migrated yet."),
        ]

    def describe_self(self) -> SamResult:
        project_result, projects = self.project_registry.list_projects()
        if not project_result.ok:
            return project_result

        return SamResult(
            status="success",
            summary="Capability awareness summary generated.",
            next_action="stop",
            metadata={
                "available_capabilities": [
                    f"{item.intent}: {item.description}" for item in self.registry.list_all()
                ],
                "known_projects": [project.name for project in projects],
                "missing_capabilities": [item.name for item in self.missing_capabilities],
            },
        )

    def check_request(self, text: str) -> SamResult:
        lowered = text.lower().replace("_", " ")
        for capability in self.registry.list_all():
            if capability.intent.replace("_", " ") in lowered:
                return SamResult(
                    status="success",
                    summary=f"Sam v2 currently supports {capability.intent}.",
                    next_action="stop",
                    metadata={"matched_capability": capability.intent},
                )

        for missing in self.missing_capabilities:
            if missing.name.replace("_", " ") in lowered:
                return SamResult(
                    status="failed",
                    summary=f"Sam v2 does not currently have {missing.name}.",
                    error_type=ErrorType.MISSING_CAPABILITY,
                    error_message=missing.description,
                    next_action="request_approval" if missing.upgradeable else "stop",
                    metadata={"missing_capability": missing.name, "upgradeable": missing.upgradeable},
                )

        return SamResult(
            status="failed",
            summary="Sam v2 could not determine whether that capability exists.",
            error_type=ErrorType.MISSING_CAPABILITY,
            error_message=text,
            next_action="ask_user",
        )

    def propose_upgrade(self, capability_name: str, reason: str) -> SamResult:
        result, proposal = self.upgrade_manager.propose(capability_name, reason)
        if proposal is not None:
            result.metadata.setdefault("proposal_id", proposal.proposal_id)
        return result
