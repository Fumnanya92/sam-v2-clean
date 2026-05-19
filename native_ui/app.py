"""Native PyQt desktop shell for Sam v2 — Calm Intelligence edition.

Architecture: one SamPanel window, right-anchored, full screen height.
No floating task popup. No fullscreen idle screensaver. No separate orb.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt6.QtCore import QTimer, QThread
from PyQt6.QtWidgets import QApplication

from core import SamRuntime
from diagnostics.result import SamResult
from memory.manager import load_memory
from workers import worker_monitor

from .windows import SamWindow


def _log(msg: str) -> None:
    print(f"[SAM_UI] {msg}", flush=True)


def _human_trace(item: object) -> str:
    """Convert a trace item to a user-readable milestone line.

    Returns empty string for items that should not be surfaced to the user.
    Internal routing events, gate decisions, and raw status fields are filtered.
    """
    if not isinstance(item, dict):
        return str(item).strip()
    label = str(item.get("label", "")).strip()
    action = str(item.get("action", "")).strip()
    observation = str(item.get("observation", "")).strip()

    # surface only meaningful labels and observations
    parts = []
    if label:
        parts.append(label)
    if observation and observation not in {"ok", "done", "success"}:
        parts.append(observation)
    if not parts and action:
        parts.append(action.replace("_", " ").title())
    return " — ".join(parts) if parts else ""


# ── background worker thread ───────────────────────────────────────────────────

class _RequestThread(QThread):
    def __init__(self, runtime: SamRuntime, text: str) -> None:
        super().__init__()
        self.runtime = runtime
        self.text    = text
        self.result: SamResult | None = None

    def run(self) -> None:
        _log(f"Request started: {self.text!r}")
        self.result = self.runtime.handle_text(self.text)
        if self.result:
            _log(f"Request done: status={self.result.status}")


# ── controller ─────────────────────────────────────────────────────────────────

class NativeShellController:
    def __init__(self, *, runtime: SamRuntime, app: QApplication, folder_opener=None) -> None:
        self.runtime       = runtime
        self.app           = app
        self.folder_opener = folder_opener or self._open_folder_default

        self.window = SamWindow()

        self._thread: _RequestThread | None = None
        self._seq         = 0
        self._started_at  = 0.0
        self._seen_workers: dict[str, int] = {}
        self._project: dict[str, str]      = {}

        self._poll = QTimer()
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._drain_workers)

        # wire signals
        self.window.submitted.connect(self.submit)
        self.window.path_clicked.connect(self._open_path)

        self._refresh_model_chip()

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def show(self) -> None:
        screen = self.app.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            self.window.resize(min(820, g.width()), min(680, g.height()))
            self.window.move(g.center() - self.window.rect().center())
        self.window.show()
        self.window.set_state("idle")
        self.window.load_demo_thread()

    def activate(self) -> None:
        self.window.raise_()
        self.window.set_state("listening")

    def go_idle(self) -> None:
        self.window.set_state("idle")

    # ── request handling ───────────────────────────────────────────────────────

    def submit(self, text: str) -> None:
        if self._thread and self._thread.isRunning():
            card = self.window.task_card()
            card.append_line("Sam is already working — please wait.")
            return

        self._seq       += 1
        seq              = self._seq
        self._started_at = time.time()
        self._seen_workers = {}

        _log(f"Submitting #{seq}: {text!r}")

        self.window.set_state("thinking")
        self.window.set_input_busy(True)
        self.window.add_user_message(text)

        card = self.window.task_card()
        card.set_title("Working on it")
        card.set_status("thinking")
        card.clear_log()
        card.append_line("Reading your request…")

        for delay, line in [
            (400,  "Checking active context…"),
            (900,  "Choosing the right action…"),
        ]:
            QTimer.singleShot(delay, lambda l=line, s=seq: self._timed_line(s, l))

        self._thread = _RequestThread(self.runtime, text)
        self._thread.finished.connect(self._on_done)
        self._poll.start()
        self._thread.start()

    def _timed_line(self, seq: int, line: str) -> None:
        if self._thread and seq == self._seq:
            self.window.task_card().append_line(line)

    def _on_done(self) -> None:
        if not self._thread:
            return
        self._drain_workers()
        self._poll.stop()

        result = self._thread.result or SamResult(
            status="failed", summary="Sam did not return a result.", next_action="ask_user"
        )
        _log(f"Done: status={result.status}")

        self._remember(result)
        self._remember_model(result)
        self.window.set_input_busy(False)
        self.window.set_state("idle" if result.ok else "listening")
        self.window.add_sam_message(self._format_reply(result))

        card = self.window.task_card()
        card.set_title(self._display_title(result))
        card.set_status("done" if result.ok else "failed")
        for line in self._card_lines(result):
            card.append_line(line)

        self._thread = None

    # ── result formatting ──────────────────────────────────────────────────────

    def _card_lines(self, r: SamResult) -> list[str]:
        """Human-readable milestone lines for the task card.

        Surfaces outcomes and key steps — not internal routing or gate events.
        """
        lines: list[str] = []

        # execution trace — filtered to user-meaningful steps only
        trace = r.metadata.get("execution_trace", [])
        if isinstance(trace, list):
            for item in trace[:8]:
                line = _human_trace(item)
                if line:
                    lines.append(line)

        # worker activity
        for task_info in worker_monitor.list_tasks():
            if task_info.created_at >= self._started_at and task_info.description:
                lines.append(task_info.description)

        # path opened / project root
        for key in ("root_path", "repo_root", "path"):
            if r.metadata.get(key):
                lines.append(str(r.metadata[key]))
                break

        # error
        if r.error_message:
            lines.append(f"Error: {r.error_message}")

        return lines

    def _format_reply(self, r: SamResult) -> str:  # noqa: PLR0912
        intent = str(r.metadata.get("intent", "chat"))

        # ── capabilities list ─────────────────────────────────────────────────
        if intent == "capabilities":
            caps    = r.metadata.get("available_capabilities", [])
            missing = r.metadata.get("missing_capabilities", [])
            lines   = [r.summary, "", "Available now:"]
            lines  += [f"  • {c.split(':',1)[0].replace('_',' ')}" for c in caps[:8]]
            if missing:
                lines += ["", "Not ready:"]
                lines += [f"  • {m.replace('_',' ')}" for m in missing[:5]]
            return "\n".join(lines)

        # ── directory listing ─────────────────────────────────────────────────
        if intent == "list_directory":
            entries = r.metadata.get("entries", [])
            count   = r.metadata.get("entry_count", len(entries) if isinstance(entries, list) else 0)
            path    = r.metadata.get("path", "")
            lines   = [f"{count} item(s)" + (f" in {path}" if path else "") + ":"]
            if isinstance(entries, list) and entries:
                lines += [f"  {entry}" for entry in entries[:20]]
                remaining = int(count) - len(entries) if str(count).isdigit() else 0
                if remaining > 0:
                    lines.append(f"  …and {remaining} more")
            else:
                lines.append("  (empty)")
            return "\n".join(lines)

        # ── project discovery ─────────────────────────────────────────────────
        if intent == "discovery_workflow":
            lines = [r.summary]
            candidates = r.metadata.get("candidates", [])
            if isinstance(candidates, list) and candidates:
                lines += ["", "Candidates:"]
                lines += [
                    f"  {i}. {Path(str(item)).name}"
                    for i, item in enumerate(candidates[:20], 1)
                ]
            return "\n".join(lines)

        # ── coding delegation — the main answer path ──────────────────────────
        if intent == "delegate_coding_task" or r.metadata.get("coding_model"):
            # Prefer the extracted agent answer; fall back to summary
            answer = str(r.metadata.get("coding_answer", "") or r.summary).strip()
            model  = str(r.metadata.get("coding_model", "agent")).strip()
            lines: list[str] = []

            if not r.ok:
                lines.append(f"The {model} agent ran into a problem:")
                lines.append(answer or r.error_message or "No details returned.")
                return "\n".join(lines)

            lines.append(answer)

            # Surface changed files if any
            changed = r.metadata.get("changed_files", [])
            if isinstance(changed, list) and changed:
                lines += ["", f"Files touched ({len(changed)}):"]
                lines += [f"  {f}" for f in changed[:10]]

            # Duration footnote
            secs = r.metadata.get("duration_seconds")
            if secs:
                lines.append(f"\n— {model} · {secs}s")

            return "\n".join(lines)

        # ── model switching ───────────────────────────────────────────────────
        if intent in {"set_coding_model", "show_coding_model"} and r.metadata.get("coding_models"):
            active = r.metadata.get("active_coding_model", "local")
            return f"{r.summary}\n\nActive model: {active}"

        # ── generic / conversational ──────────────────────────────────────────
        lines = [r.summary]
        if r.metadata.get("name") and intent not in {"chat", "project_details"}:
            lines.append(f"Project: {r.metadata['name']}")
        if r.metadata.get("root_path") and intent in {"scaffold_project", "run_project", "show_project_status"}:
            lines.append(f"Location: {r.metadata['root_path']}")
        if r.status == "needs_approval":
            lines += ["", "Approval required before I continue."]
        return "\n".join(lines)

    def _display_title(self, r: SamResult) -> str:
        if r.status == "needs_approval":
            return "Approval Required"
        if r.metadata.get("name"):
            return str(r.metadata["name"])
        return str(r.metadata.get("intent", "Task")).replace("_", " ").title() or "Task"

    # ── project memory ─────────────────────────────────────────────────────────

    def _remember(self, r: SamResult) -> None:
        for key in ("project_id", "name", "root_path"):
            if r.metadata.get(key):
                self._project[key] = str(r.metadata[key])

    def _remember_model(self, r: SamResult) -> None:
        model = str(r.metadata.get("active_coding_model", "") or "").strip()
        if model:
            self.window.set_model(model)

    def _refresh_model_chip(self) -> None:
        try:
            status = self.runtime.handler.router.coding_model_status()
        except Exception:
            status = {}
        self.window.set_model(str(status.get("active_coding_model", "local")))

    # ── worker drain ───────────────────────────────────────────────────────────

    def _drain_workers(self) -> None:
        if not self._thread:
            return
        card = self.window.task_card()
        for task in worker_monitor.list_tasks():
            if task.created_at < self._started_at:
                continue
            seen = self._seen_workers.get(task.task_id, -1)
            if seen == -1:
                role = task.worker_role or task.worker_type or ""
                detail = f" [{role}]" if role else ""
                if task.responsibility:
                    detail += f" — {task.responsibility}"
                card.set_status("working")
                card.set_title(task.worker_name or "Working")
                card.append_line(f"{task.worker_name}{detail}: {task.description}")
                self._seen_workers[task.task_id] = 0
                seen = 0
            for line in task.output_lines[seen:]:
                card.append_line(f"{task.worker_name}: {line}")
            self._seen_workers[task.task_id] = len(task.output_lines)

    # ── misc ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _open_folder_default(path: str) -> None:
        os.startfile(path)

    def _open_path(self, path: str) -> None:
        try:
            target = Path(path)
            if target.exists():
                os.startfile(str(target))
                self.window.task_card().append_line(f"Opened: {target}")
            else:
                self.window.task_card().append_line(f"Path not found: {path}")
        except OSError as exc:
            self.window.task_card().append_line(f"Could not open: {exc}")


# ── entry point ────────────────────────────────────────────────────────────────

def run_native_ui(*, data_dir, db_path) -> int:
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Sam")

    runtime = SamRuntime(
        db_path=db_path,
        memory_path=data_dir / "memory.json",
        session_path=data_dir / "session.json",
    )
    ctrl = NativeShellController(runtime=runtime, app=app)
    ctrl.show()

    code = app.exec()
    runtime.shutdown()
    return code
