"""Project planning, progress, and status reporting helpers for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from sys import executable as python_executable

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.workers import CommandSpec, FileEditSpec, FileWriteSpec, ToolingWorker

from .inspector import ProjectInspector, inspection_metadata
from .registry import ProjectRecord, ProjectRegistry


IMPLEMENTATION_PLAN = """# Implementation Plan

## Project

{project_name}

## Build slices

1. Keep `index.html` as the shell only.
2. Keep `styles.css` responsible for presentation only.
3. Keep `app.js` responsible for game logic only.
4. Keep `run_project.py --check` as the local validation command.

## Next implementation steps

1. Add score tracking and rematch flow.
2. Extract win-check logic into smaller helpers if complexity grows.
3. Add accessibility labels and keyboard support.
"""

TESTING_PLAN = """# Testing Plan

## Project

{project_name}

## Current checks

1. `python run_project.py --check` confirms the modular scaffold is wired correctly.

## Next testing steps

1. Add lightweight rule validation for win detection.
2. Add a browser smoke check for reset behavior.
3. Add regression checks for draw handling.
"""

DELEGATION_REPORT = """# Delegation Report

## Project

{project_name}

## Worker ownership

- Mason (`code`): owns `IMPLEMENTATION_PLAN.md`, `index.html`, `styles.css`, and `app.js`
- Beacon (`test`): owns `TESTING_PLAN.md` and future validation coverage
- Pilot (`dev`): owns `DELEGATION.md`, `run_project.py`, and project run verification

## Completed planning actions

1. Mason wrote the implementation plan.
2. Beacon wrote the testing plan.
3. Pilot wrote this delegation report.
"""


@dataclass
class ProjectPlanRequest:
    query: str


@dataclass
class ProjectExecutionRequest:
    query: str
    task_name: str


class ProjectPlanner:
    def __init__(
        self,
        *,
        project_registry: ProjectRegistry,
        tooling_worker: ToolingWorker,
        project_inspector: ProjectInspector | None = None,
    ) -> None:
        self.project_registry = project_registry
        self.tooling_worker = tooling_worker
        self.project_inspector = project_inspector

    def plan(self, request: ProjectPlanRequest) -> SamResult:
        project_result, project = self.project_registry.find_project(request.query)
        if not project_result.ok or project is None:
            return project_result

        project_root = Path(project.root_path)
        if not project_root.exists():
            return SamResult(
                status="failed",
                summary="Project root does not exist on disk.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=project.root_path,
                next_action="ask_user",
            )

        writes = [
            (
                "IMPLEMENTATION_PLAN.md",
                IMPLEMENTATION_PLAN.format(project_name=project.name),
                "Mason",
                "code",
                "Write the implementation plan for the project.",
            ),
            (
                "TESTING_PLAN.md",
                TESTING_PLAN.format(project_name=project.name),
                "Beacon",
                "test",
                "Write the testing plan for the project.",
            ),
            (
                "DELEGATION.md",
                DELEGATION_REPORT.format(project_name=project.name),
                "Pilot",
                "dev",
                "Write the delegation report for the project.",
            ),
        ]

        delegation: list[dict[str, str]] = []
        for filename, content, worker_name, worker_type, description in writes:
            write_result, task = self.tooling_worker.execute_write(
                FileWriteSpec(
                    name=f"plan_{project.project_id}_{filename.replace('.', '_')}",
                    worker_type=worker_type,
                    worker_name=worker_name,
                    target_path=project_root / filename,
                    content=content,
                    description=description,
                    overwrite=True,
                )
            )
            if not write_result.ok:
                write_result.metadata.setdefault("project_id", project.project_id)
                write_result.metadata.setdefault("name", project.name)
                write_result.metadata.setdefault("root_path", project.root_path)
                write_result.metadata.setdefault("delegation", delegation)
                return write_result
            delegation.append(
                {
                    "task_id": task.task_id,
                    "worker_name": task.worker_name,
                    "worker_type": task.worker_type,
                    "artifact": filename,
                    "status": task.status,
                }
            )

        self._update_project_files(project, [item[0] for item in writes])
        return SamResult(
            status="success",
            summary=(
                f"Planned {project.name} with named worker ownership. "
                "Mason owns implementation, Beacon owns testing, and Pilot owns coordination."
            ),
            next_action="stop",
            metadata={
                "project_id": project.project_id,
                "name": project.name,
                "root_path": project.root_path,
                "delegation": delegation,
                "plan_files": [item[0] for item in writes],
            },
        )

    def show_delegation(self, query: str) -> SamResult:
        project_result, project = self.project_registry.find_project(query)
        if not project_result.ok or project is None:
            return project_result

        report_path = Path(project.root_path) / "DELEGATION.md"
        if not report_path.exists():
            return SamResult(
                status="failed",
                summary="Delegation report not found for this project.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(report_path),
                next_action="ask_user",
            )

        try:
            report_text = report_path.read_text(encoding="utf-8")
        except OSError as exc:
            return SamResult(
                status="failed",
                summary="Delegation report could not be read.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )

        return SamResult(
            status="success",
            summary="Delegation is tracked for this project.",
            next_action="stop",
            metadata={
                "project_id": project.project_id,
                "name": project.name,
                "root_path": project.root_path,
                "delegation_report": report_text,
                "workers": ["Mason", "Beacon", "Pilot"],
            },
        )

    def show_progress(self, query: str) -> SamResult:
        project_result, project = self.project_registry.find_project(query)
        if not project_result.ok or project is None:
            return project_result

        project_root = Path(project.root_path)
        implementation_path = project_root / "IMPLEMENTATION_PLAN.md"
        testing_path = project_root / "TESTING_PLAN.md"
        delegation_path = project_root / "DELEGATION.md"

        try:
            implementation_text = implementation_path.read_text(encoding="utf-8")
            testing_text = testing_path.read_text(encoding="utf-8")
            delegation_text = delegation_path.read_text(encoding="utf-8")
        except OSError as exc:
            return SamResult(
                status="failed",
                summary="Project progress files could not be read.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )

        implementation_steps = self._collect_numbered_lines(implementation_text, "## Next implementation steps")
        completed_items = [line for line in implementation_steps if "Completed:" in line]
        next_items = [line for line in implementation_steps if "Completed:" not in line]
        testing_items = self._collect_numbered_lines(testing_text, "## Next testing steps")
        worker_updates = [
            line.strip()
            for line in delegation_text.splitlines()
            if line.strip().startswith(("4.", "5.", "6."))
        ]

        return SamResult(
            status="success",
            summary=(
                f"{project.name} has {len(completed_items)} completed implementation milestone(s) "
                f"and {len(next_items)} next implementation item(s)."
            ),
            next_action="stop",
            metadata={
                "project_id": project.project_id,
                "name": project.name,
                "root_path": project.root_path,
                "completed_items": completed_items,
                "next_items": next_items,
                "testing_items": testing_items,
                "worker_updates": worker_updates,
            },
        )

    def show_status(self, query: str) -> SamResult:
        if self.project_inspector is None:
            return SamResult(
                status="failed",
                summary="Project status inspection is not configured.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message="missing project inspector",
                next_action="ask_user",
            )

        progress_result = self.show_progress(query)
        if not progress_result.ok:
            return progress_result

        inspect_result, inspection = self.project_inspector.inspect(query)
        if not inspect_result.ok or inspection is None:
            return inspect_result

        metadata = inspection_metadata(inspection)
        metadata.update(
            {
                "completed_items": progress_result.metadata.get("completed_items", []),
                "next_items": progress_result.metadata.get("next_items", []),
                "testing_items": progress_result.metadata.get("testing_items", []),
                "worker_updates": progress_result.metadata.get("worker_updates", []),
            }
        )
        changed_summary = (
            "clean working tree"
            if inspection.is_clean
            else f"{len(inspection.changed_files)} changed file(s)"
        )
        return SamResult(
            status="success",
            summary=(
                f"{inspection.name} is on branch {inspection.branch or 'unknown'} with a {changed_summary}, "
                f"{len(metadata['completed_items'])} completed implementation milestone(s), "
                f"and {len(metadata['next_items'])} next implementation item(s)."
            ),
            next_action="stop",
            metadata=metadata,
        )

    def execute_task(self, request: ProjectExecutionRequest) -> SamResult:
        project_result, project = self.project_registry.find_project(request.query)
        if not project_result.ok or project is None:
            return project_result

        task_name = request.task_name.strip().lower()
        if task_name != "add score tracking":
            return SamResult(
                status="failed",
                summary="That planned task is not executable yet.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=request.task_name,
                next_action="ask_user",
            )

        project_root = Path(project.root_path)
        delegation: list[dict[str, str]] = []

        score_markup_search = '<section class="game-card">\n      <p id="status">Player X\'s turn</p>'
        score_markup_replace = (
            '<section class="game-card">\n'
            '      <div class="scoreboard" aria-label="scoreboard">\n'
            '        <p>X wins: <span id="score-x">0</span></p>\n'
            '        <p>O wins: <span id="score-o">0</span></p>\n'
            '      </div>\n'
            '      <p id="status">Player X\'s turn</p>'
        )
        index_result, index_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_scoreboard_markup",
                worker_type="code",
                worker_name="Mason",
                target_path=project_root / "index.html",
                search_text=score_markup_search,
                replace_text=score_markup_replace,
                description="Add score tracking markup to the project shell.",
            )
        )
        if not index_result.ok:
            return index_result
        delegation.append(self._task_entry(index_task, "index.html"))

        styles_search = "#status {\n  font-weight: 700;\n  margin-bottom: 16px;\n}\n"
        styles_replace = (
            ".scoreboard {\n"
            "  display: flex;\n"
            "  gap: 18px;\n"
            "  margin-bottom: 16px;\n"
            "  font-weight: 700;\n"
            "}\n\n"
            "#status {\n"
            "  font-weight: 700;\n"
            "  margin-bottom: 16px;\n"
            "}\n"
        )
        styles_result, styles_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_scoreboard_styles",
                worker_type="code",
                worker_name="Mason",
                target_path=project_root / "styles.css",
                search_text=styles_search,
                replace_text=styles_replace,
                description="Add score tracking styles to the project.",
            )
        )
        if not styles_result.ok:
            return styles_result
        delegation.append(self._task_entry(styles_task, "styles.css"))

        app_search = (
            'const board = document.getElementById("board");\n'
            'const statusText = document.getElementById("status");\n'
            'const resetButton = document.getElementById("reset");\n'
        )
        app_replace = (
            'const board = document.getElementById("board");\n'
            'const statusText = document.getElementById("status");\n'
            'const resetButton = document.getElementById("reset");\n'
            'const scoreX = document.getElementById("score-x");\n'
            'const scoreO = document.getElementById("score-o");\n'
        )
        app_result, app_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_scoreboard_logic_a",
                worker_type="code",
                worker_name="Mason",
                target_path=project_root / "app.js",
                search_text=app_search,
                replace_text=app_replace,
                description="Add scoreboard element references to the project logic.",
            )
        )
        if not app_result.ok:
            return app_result
        delegation.append(self._task_entry(app_task, "app.js"))

        score_state_search = 'let cells = Array(9).fill("");\nlet player = "X";\nlet winner = "";\n'
        score_state_replace = (
            'let cells = Array(9).fill("");\n'
            'let player = "X";\n'
            'let winner = "";\n'
            'const scores = { X: 0, O: 0 };\n'
        )
        app_state_result, app_state_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_scoreboard_logic_b",
                worker_type="code",
                worker_name="Mason",
                target_path=project_root / "app.js",
                search_text=score_state_search,
                replace_text=score_state_replace,
                description="Add scoreboard state to the project logic.",
            )
        )
        if not app_state_result.ok:
            return app_state_result
        delegation.append(self._task_entry(app_state_task, "app.js"))

        render_search = "  statusText.textContent = winner || `Player ${player}'s turn`;\n}\n"
        render_replace = (
            "  scoreX.textContent = String(scores.X);\n"
            "  scoreO.textContent = String(scores.O);\n"
            "  statusText.textContent = winner || `Player ${player}'s turn`;\n"
            "}\n"
        )
        app_render_result, app_render_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_scoreboard_logic_c",
                worker_type="code",
                worker_name="Mason",
                target_path=project_root / "app.js",
                search_text=render_search,
                replace_text=render_replace,
                description="Render score values in the project logic.",
            )
        )
        if not app_render_result.ok:
            return app_render_result
        delegation.append(self._task_entry(app_render_task, "app.js"))

        winner_search = '  if (line) {\n    winner = `Player ${player} wins!`;\n'
        winner_replace = (
            "  if (line) {\n"
            "    scores[player] += 1;\n"
            "    winner = `Player ${player} wins!`;\n"
        )
        app_win_result, app_win_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_scoreboard_logic_d",
                worker_type="code",
                worker_name="Mason",
                target_path=project_root / "app.js",
                search_text=winner_search,
                replace_text=winner_replace,
                description="Increase the scoreboard when a player wins.",
            )
        )
        if not app_win_result.ok:
            return app_win_result
        delegation.append(self._task_entry(app_win_task, "app.js"))

        runner_search = (
            '    assert "function playTurn(index)" in app_text\n'
            '    assert ".board {" in styles_text\n'
            "    return index_path\n"
        )
        runner_replace = (
            '    assert "function playTurn(index)" in app_text\n'
            '    assert "scores = { X: 0, O: 0 }" in app_text\n'
            '    assert \'id="score-x"\' in index_text\n'
            '    assert ".scoreboard {" in styles_text\n'
            "    return index_path\n"
        )
        runner_result, runner_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_scoreboard_runner",
                worker_type="test",
                worker_name="Beacon",
                target_path=project_root / "run_project.py",
                search_text=runner_search,
                replace_text=runner_replace,
                description="Extend the local validation command to cover score tracking.",
            )
        )
        if not runner_result.ok:
            return runner_result
        delegation.append(self._task_entry(runner_task, "run_project.py"))

        run_result, run_task = self.tooling_worker.execute(
            CommandSpec(
                name=f"validate_{project.project_id}_score_tracking",
                worker_type="test",
                worker_name="Beacon",
                command=[python_executable, "run_project.py", "--check"],
                description="Validate the project after adding score tracking.",
                cwd=project.root_path,
                timeout_seconds=30,
            )
        )
        if not run_result.ok:
            return run_result
        delegation.append(self._task_entry(run_task, "run_project.py"))

        report_path = project_root / "DELEGATION.md"
        report_text = report_path.read_text(encoding="utf-8")
        report_search = "3. Pilot wrote this delegation report.\n"
        report_replace = (
            "3. Pilot wrote this delegation report.\n"
            "4. Mason completed the `add score tracking` implementation task.\n"
            "5. Beacon validated the updated project with `python run_project.py --check`.\n"
            "6. Pilot refreshed this delegation report after execution.\n"
        )
        report_result, report_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_delegation_report",
                worker_type="dev",
                worker_name="Pilot",
                target_path=report_path,
                search_text=report_search,
                replace_text=report_replace,
                description="Update the delegation report after executing the planned task.",
            )
        )
        if not report_result.ok:
            return report_result
        delegation.append(self._task_entry(report_task, "DELEGATION.md"))

        impl_path = project_root / "IMPLEMENTATION_PLAN.md"
        impl_text = impl_path.read_text(encoding="utf-8")
        impl_search = "1. Add score tracking and rematch flow.\n"
        impl_replace = "1. Add score tracking and rematch flow. Completed: score tracking shipped.\n"
        impl_result, impl_task = self.tooling_worker.execute_edit(
            FileEditSpec(
                name=f"execute_{project.project_id}_implementation_report",
                worker_type="dev",
                worker_name="Pilot",
                target_path=impl_path,
                search_text=impl_search,
                replace_text=impl_replace,
                description="Mark the completed implementation step in the project plan.",
            )
        )
        if not impl_result.ok:
            return impl_result
        delegation.append(self._task_entry(impl_task, "IMPLEMENTATION_PLAN.md"))

        self._update_project_files(project, ["IMPLEMENTATION_PLAN.md", "TESTING_PLAN.md", "DELEGATION.md", "run_project.py"])
        return SamResult(
            status="success",
            summary="Executed the planned score-tracking task with Mason, Beacon, and Pilot.",
            next_action="stop",
            metadata={
                "project_id": project.project_id,
                "name": project.name,
                "root_path": project.root_path,
                "task_name": request.task_name,
                "delegation": delegation,
                "validation_stdout": run_result.metadata.get("stdout", ""),
            },
        )

    def _update_project_files(self, project: ProjectRecord, extra_files: list[str]) -> None:
        merged = list(dict.fromkeys((project.important_files or []) + extra_files))
        self.project_registry.register(
            ProjectRecord(
                project_id=project.project_id,
                name=project.name,
                root_path=project.root_path,
                stack=project.stack,
                test_command=project.test_command,
                build_command=project.build_command,
                run_command=project.run_command,
                deployment_method=project.deployment_method,
                risk_level=project.risk_level,
                active_branch=project.active_branch,
                important_files=merged,
            )
        )

    @staticmethod
    def _task_entry(task, artifact: str) -> dict[str, str]:
        return {
            "task_id": task.task_id,
            "worker_name": task.worker_name,
            "worker_type": task.worker_type,
            "artifact": artifact,
            "status": task.status,
        }

    @staticmethod
    def _collect_numbered_lines(text: str, section_heading: str) -> list[str]:
        lines = text.splitlines()
        in_section = False
        collected: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if line == section_heading:
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section and line.startswith(("1.", "2.", "3.", "4.", "5.", "6.")):
                collected.append(line)
        return collected
