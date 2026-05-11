"""Native PyQt desktop shell for Sam v2."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QRect, QThread, QTimer, Qt
from PyQt6.QtWidgets import QApplication

from sam_v2.core import SamRuntime
from sam_v2.diagnostics.result import SamResult
from sam_v2.memory.manager import load_memory
from sam_v2.workers import worker_monitor

from .orb import OrbWindow
from .windows import DashboardWindow, IdleSceneWindow, TaskPopupWindow


def _log(msg: str) -> None:
    print(f"[SAM_UI] {msg}", flush=True)


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
            _log(f"Request done: status={self.result.status} summary={self.result.summary!r}")


# ── controller ────────────────────────────────────────────────────────────────

class NativeShellController:
    def __init__(self, *, runtime: SamRuntime, app: QApplication, folder_opener=None) -> None:
        self.runtime        = runtime
        self.app            = app
        self.folder_opener  = folder_opener or self._open_folder_default

        self.idle_window = IdleSceneWindow()
        self.dashboard   = DashboardWindow()
        self.task_popup  = TaskPopupWindow()
        self.orb         = OrbWindow()

        self._thread: _RequestThread | None = None
        self._active        = False
        self._seq           = 0
        self._started_at    = 0.0
        self._seen_workers: dict[str, int] = {}
        self._project: dict[str, str]      = {}

        self._poll = QTimer()
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._drain_workers)

        # signals
        self.idle_window.activated.connect(self.activate)
        self.orb.clicked.connect(self.activate)
        self.dashboard.submitted.connect(self.submit)
        self.dashboard.idle_requested.connect(self.go_idle)
        self.dashboard.close_requested.connect(self.go_idle)
        self.task_popup.close_requested.connect(self.task_popup.hide)

        self._layout(initial=True)
        self.dashboard.hide()
        self.task_popup.hide()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def show(self) -> None:
        self.idle_window.showFullScreen()
        self.orb.show()

    def activate(self) -> None:
        if self._active:
            self.dashboard.raise_()
            self.task_popup.raise_()
            self.orb.raise_()
            return
        self._active = True
        self.orb.set_state("listening")
        self.idle_window.hide()
        self._layout(initial=False)
        self.dashboard.show()
        self.task_popup.show()
        self.dashboard.raise_()
        self.task_popup.raise_()
        self.orb.raise_()
        self.dashboard.set_state("Ready")
        self.task_popup.set_task("Ready", "Idle", ["Waiting for your next instruction."])

    def go_idle(self) -> None:
        self._active = False
        self.dashboard.hide()
        self.task_popup.hide()
        self._layout(initial=True)
        self.orb.set_state("idle")
        self.idle_window.showFullScreen()
        self.orb.raise_()

    # ── request handling ──────────────────────────────────────────────────────

    def submit(self, text: str) -> None:
        if self._thread and self._thread.isRunning():
            self.task_popup.append_line("Sam is already working — please wait.")
            return

        self._seq        += 1
        seq               = self._seq
        self._started_at  = time.time()
        self._seen_workers = {}

        _log(f"Submitting #{seq}: {text!r}")
        self.orb.set_state("thinking")
        self.dashboard.set_state("Thinking")
        self.dashboard.append_chat_message("You", text)
        self.task_popup.set_task(
            title="Active Task",
            status="Working",
            lines=["Request received.", "Routing through the runtime\u2026"],
        )

        for delay, line in [
            (240,  "Intent layer classifying request\u2026"),
            (620,  "Workers standing by for execution\u2026"),
            (1100, "Preparing next safe action\u2026"),
        ]:
            QTimer.singleShot(delay, lambda l=line, s=seq: self._timed_line(s, l))

        self._thread = _RequestThread(self.runtime, text)
        self._thread.finished.connect(self._on_done)
        self._poll.start()
        self._thread.start()

    def _timed_line(self, seq: int, line: str) -> None:
        if self._thread and seq == self._seq:
            self.task_popup.append_line(line)

    def _on_done(self) -> None:
        if not self._thread:
            return
        self._drain_workers()
        self._poll.stop()

        result = self._thread.result or SamResult(
            status="failed", summary="Sam did not return a result.", next_action="ask_user"
        )
        _log(f"Done: status={result.status} intent={result.metadata.get('intent','')!r}")

        self._remember(result)
        self.orb.set_state("idle" if result.ok else "listening")
        self.dashboard.set_state(result.status.upper())
        self.dashboard.append_chat_message("Sam", self._format_reply(result))
        self.task_popup.set_title(self._display_title(result))
        self.task_popup.set_status(result.status)
        self.task_popup.append_line("Done.")
        for line in self._task_lines(result):
            self.task_popup.append_line(line)
        self._thread = None

    # ── result formatting ─────────────────────────────────────────────────────

    def _task_lines(self, r: SamResult) -> list[str]:
        out = [r.summary, f"Next: {r.next_action or 'stop'}"]
        for key, prefix in [
            ("worker_updates", ""),
            ("worker_name",    "Worker: "),
        ]:
            val = r.metadata.get(key)
            if isinstance(val, list):
                out.extend(val)
            elif val:
                out.append(f"{prefix}{val}")
        if r.metadata.get("run_command"):
            out.append("Command: " + " ".join(r.metadata["run_command"]))
        if r.metadata.get("stdout"):
            lines = str(r.metadata["stdout"]).strip().splitlines()
            if lines:
                out.append(f"Output: {lines[-1]}")
        if r.error_message:
            out.append(f"Error: {r.error_message}")
        return out

    def _format_reply(self, r: SamResult) -> str:
        intent = str(r.metadata.get("intent", "chat"))
        if intent == "capabilities":
            caps    = r.metadata.get("available_capabilities", [])
            missing = r.metadata.get("missing_capabilities", [])
            lines   = [r.summary, "", "What I can do right now:"]
            lines  += [f"- {c.split(':',1)[0].replace('_',' ')}" for c in caps[:8]]
            if missing:
                lines += ["", "Not ready yet:"]
                lines += [f"- {m.replace('_',' ')}" for m in missing[:5]]
            return "\n".join(lines)

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

    # ── project memory ────────────────────────────────────────────────────────

    def _remember(self, r: SamResult) -> None:
        for key in ("project_id", "name", "root_path"):
            if r.metadata.get(key):
                self._project[key] = str(r.metadata[key])

    def _project_name(self) -> str:
        if not self._project:
            self._load_project()
        return self._project.get("name", "")

    def _project_root(self) -> str:
        if not self._project:
            self._load_project()
        return self._project.get("root_path", "")

    def _load_project(self) -> None:
        res, mem = load_memory(self.runtime.memory_path)
        if not res.ok:
            return
        ds = mem.get("daily_state", {})
        for k, mk in [("project_id","last_project_id"),("name","last_project_name"),("root_path","last_project_root_path")]:
            v = str(ds.get(mk, {}).get("value", "")).strip()
            if v:
                self._project[k] = v

    # ── worker drain ──────────────────────────────────────────────────────────

    def _drain_workers(self) -> None:
        if not self._thread:
            return
        for task in worker_monitor.list_tasks():
            if task.created_at < self._started_at:
                continue
            seen = self._seen_workers.get(task.task_id, -1)
            if seen == -1:
                self.task_popup.append_line(
                    f"{task.worker_name} [{task.worker_type}] \u2192 {task.description}"
                )
                self._seen_workers[task.task_id] = 0
                seen = 0
            for line in task.output_lines[seen:]:
                self.task_popup.append_line(line)
            self._seen_workers[task.task_id] = len(task.output_lines)
            if task.status == "done" and seen < len(task.output_lines):
                self.task_popup.append_line(f"{task.worker_name} finished.")
            elif task.status == "failed" and seen < len(task.output_lines):
                self.task_popup.append_line(f"{task.worker_name} failed: {task.error_message}")

    # ── window layout ─────────────────────────────────────────────────────────

    def _layout(self, *, initial: bool) -> None:
        screen = self.app.primaryScreen()
        if not screen:
            return
        g = screen.availableGeometry()

        ORB_SZ   = 210
        DASH_W   = DashboardWindow._W
        DASH_H   = DashboardWindow._H
        POPUP_W  = TaskPopupWindow._W
        POPUP_H  = TaskPopupWindow._H
        MARGIN   = 24

        orb_idle = QRect(
            g.left() + (g.width()  - ORB_SZ) // 2,
            g.top()  + (g.height() - ORB_SZ) // 2,
            ORB_SZ, ORB_SZ,
        )
        orb_active = QRect(
            g.right() - ORB_SZ - MARGIN,
            g.bottom() - ORB_SZ - MARGIN,
            ORB_SZ, ORB_SZ,
        )
        dash_rect = QRect(
            g.right() - DASH_W - MARGIN,
            g.top() + MARGIN + 32,
            DASH_W, DASH_H,
        )
        popup_rect = QRect(
            g.right() - DASH_W - POPUP_W - MARGIN * 2,
            g.top() + MARGIN + 100,
            POPUP_W, POPUP_H,
        )

        if initial:
            self.orb.setGeometry(orb_idle)
        else:
            self.orb.animate_to(orb_active)
            # dashboard slides in from the right
            self.dashboard.setGeometry(QRect(g.right(), dash_rect.y(), DASH_W, DASH_H))
            self.dashboard.animate_to(dash_rect)
            # popup drops in from above
            self.task_popup.setGeometry(QRect(popup_rect.x(), g.top() - POPUP_H, POPUP_W, POPUP_H))
            self.task_popup.animate_to(popup_rect)

    # ── misc ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _open_folder_default(path: str) -> None:
        os.startfile(path)


# ── entry point ───────────────────────────────────────────────────────────────

def run_native_ui(*, data_dir: Path, db_path: Path) -> int:
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
