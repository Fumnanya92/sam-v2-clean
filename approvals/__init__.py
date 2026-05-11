"""Sam v2 approvals and authority foundation."""

from .audit import AuthorityAuditTrail
from .constants import ACTION_CATEGORIES, AUTHORITY_REQUIREMENTS, describe_level
from .engine import AuthorityConfig, AuthorityDecision, AuthorityEngine, ContextRule, PerActionOverride
from .manager import ApprovalManager, ApprovalRequest

__all__ = [
    "ACTION_CATEGORIES",
    "AUTHORITY_REQUIREMENTS",
    "describe_level",
    "AuthorityAuditTrail",
    "AuthorityConfig",
    "AuthorityDecision",
    "AuthorityEngine",
    "ContextRule",
    "PerActionOverride",
    "ApprovalManager",
    "ApprovalRequest",
]
