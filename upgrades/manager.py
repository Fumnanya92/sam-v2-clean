"""Controlled self-improvement proposal manager for Sam v2."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult


@dataclass
class UpgradeProposal:
    proposal_id: str
    capability_name: str
    reason: str
    status: str
    created_at: str


class UpgradeProposalManager:
    def __init__(self, proposals_path: str | Path) -> None:
        self.proposals_path = Path(proposals_path)

    def propose(self, capability_name: str, reason: str) -> tuple[SamResult, UpgradeProposal | None]:
        result, proposals = self._load_all()
        if not result.ok:
            return result, None

        proposal = UpgradeProposal(
            proposal_id=str(uuid4()),
            capability_name=capability_name,
            reason=reason,
            status="pending_approval",
            created_at=datetime.utcnow().isoformat() + "Z",
        )
        proposals.append(proposal)
        save_result = self._save_all(proposals)
        if not save_result.ok:
            return save_result, None
        return (
            SamResult(
                status="needs_approval",
                summary="Upgrade proposal recorded and awaiting approval.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=reason,
                next_action="request_approval",
                metadata={"proposal_id": proposal.proposal_id, "capability_name": capability_name},
            ),
            proposal,
        )

    def list_proposals(self) -> tuple[SamResult, list[UpgradeProposal]]:
        return self._load_all()

    def _load_all(self) -> tuple[SamResult, list[UpgradeProposal]]:
        if not self.proposals_path.exists():
            return (
                SamResult(
                    status="success",
                    summary="Upgrade proposal store not found; using empty store.",
                    next_action="stop",
                    metadata={"created_default": True},
                ),
                [],
            )
        try:
            raw = json.loads(self.proposals_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Upgrade proposal store must contain a list.")
            return (
                SamResult(status="success", summary="Upgrade proposals loaded.", next_action="stop"),
                [UpgradeProposal(**item) for item in raw],
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Upgrade proposal store is invalid.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="ask_user",
                ),
                [],
            )
        except OSError as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to read upgrade proposal store.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                [],
            )

    def _save_all(self, proposals: list[UpgradeProposal]) -> SamResult:
        try:
            self.proposals_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [asdict(item) for item in proposals]
            self.proposals_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return SamResult(status="success", summary="Upgrade proposals saved.", next_action="stop")
        except OSError as exc:
            return SamResult(
                status="failed",
                summary="Failed to save upgrade proposals.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )
