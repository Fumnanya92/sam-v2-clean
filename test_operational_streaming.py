from __future__ import annotations

from pathlib import Path
import tempfile

from tools import CodebaseCleanupService
from workers import worker_monitor


def test_codebase_scan_streams_scope_progress_and_matches() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "lib").mkdir()
        (root / "lib" / "levies.txt").write_text("Residents May levies are due soon.\n", encoding="utf-8")

        before = {task.task_id for task in worker_monitor.list_tasks()}
        result, report = CodebaseCleanupService(root).inspect(["levies"])
        new_tasks = [task for task in worker_monitor.list_tasks() if task.task_id not in before]

        assert result.ok
        assert report.matches
        assert new_tasks
        task = new_tasks[-1]
        assert task.worker_name == "Nova"
        assert any("Root:" in line for line in task.output_lines)
        assert any("Patterns:" in line for line in task.output_lines)
        assert any("Scanning file" in line for line in task.output_lines)
        assert any("Match:" in line for line in task.output_lines)
        assert any("Found" in line for line in task.output_lines)


def test_codebase_scan_prunes_node_modules_before_scanning() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "node_modules" / "pkg" / "levies.txt").write_text("levies in dependency\n", encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "levies.txt").write_text("levies in app\n", encoding="utf-8")

        result, report = CodebaseCleanupService(root).inspect(["levies"])

        assert result.ok
        assert report.scanned_files == 1
        assert [match.path for match in report.matches] == ["src/levies.txt"]
