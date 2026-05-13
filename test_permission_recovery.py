from __future__ import annotations

from pathlib import Path
import shutil
from uuid import uuid4

from diagnostics.error_types import ErrorType
from diagnostics.permission_errors import file_error_type, is_permission_error
from tools import CodebaseCleanupService


def test_windows_permission_errors_are_classified_as_permission_blocks() -> None:
    exc = PermissionError(13, "Access is denied", "blocked.txt")

    assert is_permission_error(exc)
    assert file_error_type(exc) == ErrorType.MISSING_PERMISSION
    assert is_permission_error("Access is denied")


def test_codebase_scan_reports_permission_blocked_files(monkeypatch) -> None:
    root = Path.cwd() / f"tmp_permission_test_{uuid4().hex}"
    root.mkdir()
    try:
        allowed = root / "allowed.txt"
        blocked = root / "blocked.txt"
        allowed.write_text("needle is here\n", encoding="utf-8")
        blocked.write_text("needle is hidden\n", encoding="utf-8")
        original_read_text = Path.read_text

        def guarded_read_text(path: Path, *args, **kwargs):
            if path.name == "blocked.txt":
                raise PermissionError(13, "Access is denied", str(path))
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded_read_text)

        result, report = CodebaseCleanupService(root).inspect(["needle"])

        assert result.ok, result
        assert result.metadata["match_count"] == 1
        assert result.metadata["permission_blocked_count"] == 1
        assert report.skipped_paths[0]["path"] == "blocked.txt"
        assert report.skipped_paths[0]["reason"] == "permission denied"
    finally:
        shutil.rmtree(root, ignore_errors=True)
