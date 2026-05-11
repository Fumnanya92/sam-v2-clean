"""Project awareness helpers for Sam v2."""

from .diff_summary import DiffFileSummary, DiffSummary, DiffSummaryService
from .failure_analysis import CommandFailureAnalysis, FailureAnalysisService, resolve_flutter_command
from .inspector import ProjectInspection, ProjectInspector, inspection_metadata
from .planning import ProjectExecutionRequest, ProjectPlanRequest, ProjectPlanner
from .registry import ProjectRecord, ProjectRegistry
from .scaffolding import ProjectScaffoldRequest, ProjectScaffolder

__all__ = [
    "DiffFileSummary",
    "DiffSummary",
    "DiffSummaryService",
    "CommandFailureAnalysis",
    "FailureAnalysisService",
    "ProjectExecutionRequest",
    "ProjectInspection",
    "ProjectInspector",
    "ProjectPlanRequest",
    "ProjectPlanner",
    "ProjectRecord",
    "ProjectRegistry",
    "ProjectScaffoldRequest",
    "ProjectScaffolder",
    "inspection_metadata",
    "resolve_flutter_command",
]
