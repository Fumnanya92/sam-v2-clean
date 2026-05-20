"""Native PyQt desktop shell for Sam — connected to sam_brain."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QTimer, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core import SamRuntime
from diagnostics.result import SamResult
from workers import worker_monitor

from .windows import LiveActivityCard, SamWindow


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
        self.result = self.runtime.handle_text(self.text)


# ── controller ─────────────────────────────────────────────────────────────────

class NativeShellController:
    def __init__(self, *, runtime: SamRuntime, app: QApplication, folder_opener=None) -> None:
        self.runtime       = runtime
        self.app           = app
        self.folder_opener = folder_opener or self._open_folder_default

        self.window = SamWindow()

        self._thread: _RequestThread | None = None
        self._seq          = 0
        self._started_at   = 0.0
        self._seen_workers: dict[str, int] = {}
        self._live_lines:   list[str]      = []

        # Live activity card — created per request, collapsed on done
        self._live_card: LiveActivityCard | None = None
        self._activity_cursor: int = 0

        # Conversation thread persistence
        self._threads_path = Path(runtime.memory_path).parent / "threads.json"
        self._current_thread: dict = self._new_thread()

        # Poll worker output every 150ms while Sam is working
        self._poll = QTimer()
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._drain_workers)

        # Wire signals
        self.window.submitted.connect(self.submit)
        self.window.path_clicked.connect(self._open_path)
        self.window._sidebar.thread_selected.connect(self._on_thread_selected)

        # Wire the approval card "go ahead" button to auto-submit "yes"
        self.window.approved.connect(self._on_approved)
        self.window.declined.connect(self._on_declined)

        self._refresh_model_chip()

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def show(self) -> None:
        screen = self.app.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            self.window.resize(min(900, g.width()), min(700, g.height()))
            self.window.move(g.center() - self.window.rect().center())
        self.window.show()
        self.window.set_state("idle")
        self._load_history_into_sidebar()

    # ── request handling ───────────────────────────────────────────────────────

    def submit(self, text: str) -> None:
        if self._thread and self._thread.isRunning():
            return  # Ignore while busy — the input is already locked

        self._seq         += 1
        self._started_at   = time.time()
        self._seen_workers = {}
        self._live_lines   = []
        self._live_card    = None

        # Reset activity feed cursor so we only see lines from this request
        try:
            from sam_brain.activity import feed as _activity_feed
            _, self._activity_cursor = _activity_feed.drain(0)
            # drain(0) returns all existing lines — cursor now points past them
            _, self._activity_cursor = _activity_feed.drain(self._activity_cursor)
        except Exception:
            self._activity_cursor = 0

        self.window.set_state("thinking")
        self.window.set_input_busy(True)
        self.window.add_user_message(text)
        self.window.show_thinking()

        # Record this message in the current thread
        self._record_message("user", text)

        self._thread = _RequestThread(self.runtime, text)
        self._thread.finished.connect(self._on_done)
        self._poll.start()
        self._thread.start()

    def _on_approved(self) -> None:
        """User clicked 'go ahead' on an approval card — send yes to Sam."""
        self.submit("yes")

    def _on_declined(self) -> None:
        """User clicked 'not yet' on an approval card."""
        # Clear any pending plan so Sam doesn't act on it
        self._clear_pending()

    def _on_done(self) -> None:
        self._drain_workers()
        self._poll.stop()

        result = self._thread.result if self._thread else None
        if result is None:
            result = SamResult(status="failed", summary="Something went wrong — try again.")

        self.window.set_input_busy(False)
        self.window.set_state("idle" if result.ok else "listening")

        reply = result.summary or ""

        # Collapse the live activity card before showing Sam's reply
        if self._live_card is not None:
            self.window._conv.finish_live_activity(self._live_card)
            self._live_card = None

        # Check if Sam just produced a plan that needs approval
        pending = self._load_pending()
        if pending and pending.get("type") == "new_project":
            plan = pending.get("plan", {})
            self._show_plan_approval(reply, plan)
        else:
            self.window.add_sam_message(reply)

        # Persist Sam's reply and save the thread
        self._record_message("sam", reply)
        self._save_current_thread()

        self._thread = None

    # ── live activity feed ─────────────────────────────────────────────────────

    def _drain_workers(self) -> None:
        """Poll worker_monitor and activity feed; stream lines into the live card."""
        if not self._thread:
            return

        new_lines: list[str] = []

        # Source 1: worker monitor (for tooling workers)
        for task in worker_monitor.list_tasks():
            if task.created_at < self._started_at:
                continue
            seen = self._seen_workers.get(task.task_id, 0)
            fresh = task.output_lines[seen:]
            for raw in fresh:
                line = raw.strip()
                if line and not _is_noise(line):
                    new_lines.append(line)
            self._seen_workers[task.task_id] = len(task.output_lines)

        # Source 2: sam_brain activity feed (for brain worker prints)
        try:
            from sam_brain.activity import feed as _activity_feed
            new_activity, self._activity_cursor = _activity_feed.drain(self._activity_cursor)
            for item in new_activity:
                # Promote to live card on first activity line
                if self._live_card is None:
                    self._live_card = self.window._conv.start_live_activity()
                self._live_card.add_line(item.worker, item.message)
                new_lines.append(f"[{item.worker}] {item.message}")
        except Exception:
            pass

        if new_lines:
            self._live_lines.extend(new_lines)
            last = self._live_lines[-1]
            self.window.set_live_status(last[:90])

    # ── plan approval card ─────────────────────────────────────────────────────

    def _show_plan_approval(self, plan_text: str, plan_dict: dict) -> None:
        """Show Sam's plan as a message + an approval card below it."""
        phases = plan_dict.get("phases", [])
        actions = []
        for phase in phases[:4]:
            actions.append({
                "verb": "create",
                "target": phase.get("title", ""),
                "note": f"{len(phase.get('tasks', []))} tasks",
            })
        if plan_dict.get("run_command"):
            actions.append({
                "verb": "run",
                "target": plan_dict["run_command"],
                "note": "verify it works",
            })

        approval = {
            "kind": "new_project",
            "summary": f"Build {plan_dict.get('name', 'this project')} — shall I go ahead?",
            "actions": actions,
        }
        self.window.add_sam_message(plan_text, approval=approval)

    # ── pending task helpers ───────────────────────────────────────────────────

    def _pending_path(self) -> Path | None:
        mem_path = getattr(self.runtime, "memory_path", None)
        if mem_path:
            return Path(mem_path).parent / "sam_brain_pending.json"
        return None

    def _load_pending(self) -> dict | None:
        path = self._pending_path()
        if path and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def _clear_pending(self) -> None:
        path = self._pending_path()
        if path and path.exists():
            path.unlink(missing_ok=True)

    # ── model chip ────────────────────────────────────────────────────────────

    def _refresh_model_chip(self) -> None:
        """Read the active coding model from settings and update the chip."""
        try:
            mem_path = getattr(self.runtime, "memory_path", None)
            if mem_path:
                settings = Path(mem_path).parent / "coding_models.json"
                if settings.exists():
                    data = json.loads(settings.read_text(encoding="utf-8"))
                    self.window.set_model(str(data.get("active_model", "codex")))
                    return
        except Exception:
            pass
        self.window.set_model("codex")

    # ── path opening ──────────────────────────────────────────────────────────

    def _open_path(self, path: str) -> None:
        try:
            target = Path(path)
            if target.exists():
                os.startfile(str(target))
        except OSError:
            pass

    @staticmethod
    def _open_folder_default(path: str) -> None:
        os.startfile(path)

    # ── conversation thread persistence ───────────────────────────────────────

    def _new_thread(self) -> dict:
        return {
            "id": str(uuid4())[:8],
            "title": "",
            "started_at": datetime.now().isoformat(),
            "messages": [],
        }

    def _record_message(self, role: str, content: str) -> None:
        if not self._current_thread["title"] and role == "user":
            self._current_thread["title"] = (content[:52] + "…") if len(content) > 52 else content
        self._current_thread["messages"].append({
            "role": role,
            "content": content,
            "at": datetime.now().strftime("%H:%M"),
        })

    def _save_current_thread(self) -> None:
        try:
            threads = self._load_all_threads()
            # Update or insert current thread
            ids = [t["id"] for t in threads]
            if self._current_thread["id"] in ids:
                idx = ids.index(self._current_thread["id"])
                threads[idx] = self._current_thread
            else:
                threads.insert(0, self._current_thread)
            # Keep last 100 threads
            threads = threads[:100]
            self._threads_path.write_text(
                json.dumps({"threads": threads}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_all_threads(self) -> list[dict]:
        try:
            if self._threads_path.exists():
                data = json.loads(self._threads_path.read_text(encoding="utf-8"))
                return data.get("threads", [])
        except Exception:
            pass
        return []

    def _load_history_into_sidebar(self) -> None:
        threads = self._load_all_threads()
        if not threads:
            return
        today = datetime.now().date()
        today_items, older_items = [], []
        for t in threads[:30]:
            try:
                started = datetime.fromisoformat(t["started_at"]).date()
                is_today = started == today
            except Exception:
                is_today = False
            item = {
                "id": t["id"],
                "title": t.get("title") or "Conversation",
                "preview": t["messages"][0]["content"][:60] if t.get("messages") else "",
                "at": datetime.fromisoformat(t["started_at"]).strftime("%H:%M")
                      if "started_at" in t else "",
            }
            if is_today:
                today_items.append(item)
            else:
                older_items.append(item)

        groups = []
        if today_items:
            groups.append({"name": "Today", "items": today_items})
        if older_items:
            groups.append({"name": "Earlier", "items": older_items})
        if groups:
            self.window.load_sidebar_history(groups)

    def _on_thread_selected(self, tid: str) -> None:
        threads = self._load_all_threads()
        match = next((t for t in threads if t["id"] == tid), None)
        if not match:
            return
        # Save any ongoing thread before switching
        if self._current_thread["messages"]:
            self._save_current_thread()
        # Load selected thread into view
        self.window._conv.clear()
        title = match.get("title") or "Conversation"
        self.window.set_thread_title(title)
        for msg in match.get("messages", []):
            if msg["role"] == "user":
                self.window._conv.add_user_turn(msg["content"], at=msg.get("at"))
            else:
                self.window._conv.add_sam_turn(msg["content"], at=msg.get("at"))
        # Mark this as a read-only view — new messages start a new thread
        self._current_thread = self._new_thread()


# ── noise filter for live feed ─────────────────────────────────────────────────

_NOISE_FRAGMENTS = (
    "tokens used", "eval_count", "done_reason", "total_duration",
    "[SAM_UI]", "Request started", "Request done", "Provider:", "Folder:",
)

def _is_noise(line: str) -> bool:
    lower = line.lower()
    return any(frag.lower() in lower for frag in _NOISE_FRAGMENTS)


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
