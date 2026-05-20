"""
Block 7: Sam's runner block.

Sam doesn't just delegate and hope. He runs the code himself,
reads the output, and loops until it passes — or tells you honestly
what went wrong after trying his best.

Worker roles:
  Forge    — builds and fixes code
  Sentinel — runs tests, checks, and verifications
"""

from __future__ import annotations

import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Low-level command runner
# ---------------------------------------------------------------------------

def run_command(cmd: str, cwd: str | Path, timeout: int = 180) -> dict:
    """
    Run a shell command inside a directory.
    Returns {success: bool, output: str, returncode: int}.
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = (stdout + "\n" + stderr).strip()
        return {
            "success": result.returncode == 0,
            "output": output,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": f"Timed out after {timeout}s — the command may still be running.",
            "returncode": -1,
        }
    except Exception as exc:
        return {"success": False, "output": str(exc), "returncode": -1}


# ---------------------------------------------------------------------------
# Sentinel check — runs one verification command, returns plain-English result
# ---------------------------------------------------------------------------

def sentinel_check(cmd: str, cwd: str | Path, timeout: int = 180) -> tuple[bool, str]:
    """
    Sentinel runs a test/check command.
    Returns (passed: bool, plain_english_summary: str).
    """
    from workers.names import resolve_worker_identity
    sentinel = resolve_worker_identity("test")

    print(f"[{sentinel.name}] Running: {cmd}", flush=True)
    result = run_command(cmd, cwd=cwd, timeout=timeout)

    if result["success"]:
        lines = result["output"].split("\n")
        # Find the most informative summary line
        summary_candidates = [
            l for l in lines if any(
                kw in l.lower() for kw in
                ("passed", "ok", "success", "0 issues", "no issues", "all good")
            )
        ]
        summary = summary_candidates[-1].strip() if summary_candidates else "All checks passed."
        return True, summary
    else:
        # Return the last meaningful error lines (not empty)
        lines = [l.strip() for l in result["output"].split("\n") if l.strip()]
        # Prefer lines that look like errors
        error_lines = [l for l in lines if any(
            kw in l.lower() for kw in ("error", "fail", "exception", "warning", "issue")
        )]
        relevant = error_lines[-6:] if error_lines else lines[-6:]
        return False, "\n".join(relevant)


# ---------------------------------------------------------------------------
# Supervisor loop — the full Forge → Sentinel → Forge cycle
# ---------------------------------------------------------------------------

def supervisor_loop(
    goal: str,
    project_path: str,
    project_name: str,
    *,
    test_command: str | None = None,
    settings_path: Path | None = None,
    max_fix_attempts: int = 2,
) -> str:
    """
    Full autonomous build + verify loop:

      1. Forge builds / edits the code
      2. Sentinel runs the test/check command
      3. If it fails → Forge fixes based on the error output
      4. Sentinel retests
      5. Repeat up to max_fix_attempts times
      6. Report the result in plain English

    If no test_command is given, just return the build result.
    """
    from sam_brain.coding_agent import delegate_to_coding_model
    from workers.names import resolve_worker_identity

    forge    = resolve_worker_identity("code")
    sentinel = resolve_worker_identity("test")

    if not settings_path:
        return (
            f"Coding settings not found — {forge.name} can't start right now."
        )

    # ── Step 1: Forge builds ──────────────────────────────────────────────────
    print(f"[{forge.name}] Starting work on {project_name}: {goal[:80]}", flush=True)
    build_result = delegate_to_coding_model(
        goal=goal,
        project_path=project_path,
        project_name=project_name,
        settings_path=settings_path,
    )

    # If no verification command, just return what Forge did
    if not test_command:
        return build_result

    # ── Step 2+: Sentinel checks, Forge fixes ─────────────────────────────────
    fix_attempt = 0
    last_error  = ""

    while True:
        passed, check_summary = sentinel_check(test_command, cwd=project_path)

        if passed:
            # Clean pass — report success
            lines = [f"Done. {forge.name} built {project_name} and {sentinel.name} gave it the green light."]
            lines.append("")
            lines.append(f"Check result: {check_summary}")
            if fix_attempt > 0:
                word = "fix" if fix_attempt == 1 else "fixes"
                lines.append(
                    f"{forge.name} needed {fix_attempt} {word} along the way — "
                    f"{sentinel.name} confirmed clean after that."
                )
            lines.append("")
            lines.append("Want me to do anything else with it?")
            return "\n".join(lines)

        # Failed
        last_error = check_summary
        fix_attempt += 1

        if fix_attempt > max_fix_attempts:
            break  # Give up after max attempts

        # Forge fixes
        print(
            f"[{forge.name}] {sentinel.name} found issues — fixing (attempt {fix_attempt})...",
            flush=True,
        )
        fix_goal = (
            f"Running `{test_command}` in the project produced these errors:\n\n"
            f"{last_error}\n\n"
            f"Please fix all errors so that running `{test_command}` passes cleanly.\n"
            f"Do not change behaviour — only fix what's broken."
        )
        delegate_to_coding_model(
            goal=fix_goal,
            project_path=project_path,
            project_name=project_name,
            settings_path=settings_path,
        )

    # Still failing after all attempts
    short_error = (last_error[:400] + "…") if len(last_error) > 400 else last_error
    return (
        f"{forge.name} built {project_name} and {sentinel.name} ran the checks, "
        f"but couldn't get everything passing after {max_fix_attempts} attempts.\n\n"
        f"Last issue {sentinel.name} saw:\n{short_error}\n\n"
        f"Want me to try a different approach, or shall we look at it together?"
    )
