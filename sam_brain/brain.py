"""
SamBrain — reasoning-first brain.

Flow (every message):
  1. _think()  — LLM reads message + history + memory, returns structured intent
  2. act       — execute the intent (chat / find / code / run / remember / query)
  3. escalate  — if Sam can't finish, delegate to coding agent instead of asking the user

No keyword routing. No hardcoded confirm-word lists. The LLM decides everything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ── Think prompt ──────────────────────────────────────────────────────────────

_THINK_PROMPT = """You are Sam's reasoning core. A user sent you a message. Your job: understand what they ACTUALLY want.

Sam lives at: {sam_root}
Sam's key folders: sam_brain/ (reasoning), llm/ (language model), tools/ (utilities), workspace/ (built projects), native_ui/ (desktop UI)
Sam's memory file: {memory_path}

{skills_section}

Actions Sam can take:
  chat     - talk, explain, advise, answer questions, recall from conversation or memory
  find     - search this Windows machine for a file, folder, or project
  open     - open a folder or file in Windows Explorer
  code     - write, fix, edit, or review code in a project (via coding agent)
  run      - execute a command, run tests, or start an app within a project
  execute  - run a one-shot desktop task RIGHT NOW with no project context needed:
             open a URL, play/pause/resume media, take a screenshot, describe the screen,
             control the desktop with mouse/keyboard, install a package
  remember - save new information, a path, or a credential to memory
  query    - read live data from a database (Firestore, SQL, etc.)
  skill    - run an acquired skill script (use skill_name field to specify which one)

Examples (message -> action):
  "hey Sam"                                     -> chat
  "what can you do?"                            -> chat
  "where are your own files?"                   -> chat  (use Sam's known location above)
  "find the estate project"                     -> find
  "can you find my pubspec.yaml?"               -> find  (can you + specific target = directive to do it)
  "can you search for focusflow?"               -> find  (specific search = directive)
  "can you fix the login bug in estate?"        -> code  (specific task = directive)
  "can you add a dark mode toggle?"             -> code  (specific code task = directive)
  "can you open the folder?"                    -> open  (specific action = directive)
  "open the folder"                             -> open
  "add a README to my focusflow project"        -> code  (task on existing project)
  "fix the login bug"                           -> code
  "build me a web scraper"                      -> code  (new tool)
  "run the tests"                               -> run
  "open YouTube Music in Chrome"                -> execute
  "play some lofi music"                        -> execute
  "pause the music"                             -> execute
  "resume the music"                            -> execute
  "take a screenshot"                           -> execute
  "what can you see on my screen?"              -> execute
  "open chrome and go to github.com"            -> execute
  "install pyautogui"                           -> execute
  "test my flutter app"                         -> skill  (skill_name: test_flutter_ui)
  "run UI tests on focusflow"                   -> skill  (skill_name: test_flutter_ui)
  "test the estate app"                         -> skill  (skill_name: test_flutter_ui)
  "remember that my key is in Documents"        -> remember
  "what is my name?"                            -> chat  (recall from memory)
  "check your memory"                           -> chat  (recall, not storing)
  "can you fix code in general?"                -> chat  (no specific target = capability question)
  "I can not wait to test this"                 -> chat  (expressing feeling, not ordering)

Recent conversation:
{history}

Projects Sam knows about:
{known_projects}

What Sam knows about this user:
{saved_facts}

{pending_section}

User message: "{message}"

Output ONLY this JSON:
{{
  "intent": "what the user actually wants, in plain English",
  "action": "chat|find|open|code|run|execute|remember|query|skill",
  "project_hint": "project or app name if mentioned, else empty string",
  "goal": "the specific task or question",
  "skill_name": "acquired skill name if action=skill, else empty string",
  "is_confirming_pending": true or false
}}

Rules:
- "Can you X?" with a specific named target = directive to do X. Route to that action.
- "Can you X?" with no specific target = capability question = chat.
- execute = for immediate desktop actions on THIS machine, no project codebase needed.
- run/code = for actions on a software project codebase.
- is_confirming_pending: true ONLY if a pending task exists AND user is agreeing (yes, go ahead, sure, ok, do it, proceed).
- Use open only for opening folders/files in Explorer.
- Use remember ONLY when user is STORING new info. Recall = chat.
- Frustration, greetings, vague questions = chat.
- When in doubt = chat.
- Output ONLY the JSON. No extra text."""


def _worker_print(name: str, message: str) -> None:
    """Emit a worker status line to the activity feed AND the terminal."""
    try:
        from sam_brain.activity import feed
        feed.emit(name, message)
    except Exception:
        pass
    print(f"[{name}] {message}", flush=True)


class SamBrain:
    """
    Sam's new brain. Simple, transparent, block-by-block.

    Each block is added on top of the previous one. Block 1 is the minimum
    viable Sam: he can talk to you. Everything else comes after that works.
    """

    def __init__(
        self,
        *,
        llm_client=None,
        memory_path: str | Path | None = None,
    ) -> None:
        # Lazy-import to avoid circular imports and keep startup fast
        from llm.ollama import OllamaClient

        self.llm = llm_client or OllamaClient()
        self.memory_path = Path(memory_path) if memory_path else None

        # Coding models settings path (same dir as memory)
        if self.memory_path:
            self._coding_settings = self.memory_path.parent / "coding_models.json"
        else:
            self._coding_settings = None

        self._active_blocks = {"conversational", "router", "discovery", "coding_agent", "memory"}

        # Conversation history — keeps the last 20 turns so Sam knows what was just said
        self._history: list[dict] = []

        # Track the action _think() chose — used by the auditor in _record_and_return
        self._last_thought_action: str = "chat"

        # Pending music like — set after a play, checked on the next message
        self._pending_music_like: dict | None = None
        self._last_music_title: str = ""

        # Clear any stale pending task from a previous session so it doesn't leak in
        if self.memory_path:
            try:
                from sam_brain.coding_agent import clear_pending
                clear_pending(self.memory_path)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public entry point — this is what SamRuntime calls
    # ------------------------------------------------------------------

    def handle(self, message: str) -> str:
        """
        Reasoning-first handle loop:
          1. Think  — LLM understands intent, action, project, confirmation
          2. Number selection — if user is picking from a list
          3. Pending confirmation — LLM-judged, not keyword-matched
          4. Act — execute the intent
        """
        message = message.strip()
        if not message:
            return "Hey — what's on your mind?"

        self._history.append({"role": "user", "content": message})
        if len(self._history) > 40:
            self._history = self._history[-40:]

        print(f"\n[YOU] {message}", flush=True)

        if not self.llm.is_available():
            response = (
                "Hmm, I can't reach my language model right now. "
                "Is Ollama running? Try: ollama serve"
            )
            self._history.append({"role": "assistant", "content": response})
            return response

        # ── Diagnostic shortcuts (bypass LLM for meta-queries) ────────────────
        _msg_lower = message.lower()

        # ── Skill installation shortcut ───────────────────────────────────────
        _install_patterns = [
            "learn to ", "learn how to ", "install skill", "add skill",
            "teach yourself", "save this as a skill", "remember how to ",
        ]
        if any(p in _msg_lower for p in _install_patterns):
            return self._record_and_return(self._handle_learn_skill(message))
        if any(p in _msg_lower for p in [
            "error queue", "repair queue", "what's broken", "whats broken",
            "check your errors", "check your repair", "what errors do you have",
        ]):
            try:
                from sam_brain.self_repair import format_queue_for_claude
                return self._record_and_return(format_queue_for_claude(self.memory_path))
            except Exception:
                pass

        if any(p in _msg_lower for p in [
            "conversation issues", "conversation problems", "why did you ask that",
            "bad responses", "check your behaviour", "check your behavior",
            "audit", "are you behaving", "what went wrong",
        ]):
            try:
                from sam_brain.conversation_audit import format_issues_for_review
                return self._record_and_return(format_issues_for_review(self.memory_path))
            except Exception:
                pass

        try:
            memory = self._load_memory()
            from sam_brain.coding_agent import load_pending, clear_pending, save_pending as _sp
            from sam_brain.memory import log_task, remember_project
            from sam_brain.planner import ProjectPlan
            import os

            pending = load_pending(self.memory_path)

            # ── Step 1: Number selection (user picking from a numbered list) ──
            if pending and pending.get("type") == "project_selection":
                num_search = re.search(r'\b([1-9])\b', message)
                if num_search:
                    options = pending.get("options", [])
                    idx = int(num_search.group(1)) - 1
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        clear_pending(self.memory_path)
                        goal      = pending.get("goal", "")
                        task_type = pending.get("task_type", "code")
                        from workers.names import resolve_worker_identity
                        forge = resolve_worker_identity("code")

                        if task_type == "find":
                            # User just wanted to locate this — show path, done.
                            from sam_brain.memory import remember_project as _rp, touch_project as _tp
                            _rp(selected["name"], selected["path"], "", self.memory_path)
                            _tp(selected["path"], self.memory_path)
                            return self._record_and_return(
                                f"Found it.\n\n"
                                f"  {selected['name']}\n"
                                f"  {selected['path']}\n\n"
                                f"Want me to open the folder or do something with it?"
                            )
                        if task_type == "query":
                            pending_with_key = {**pending, "key_path": pending.get("key_path", "")}
                            return self._record_and_return(
                                self._confirm_query(goal, selected["name"], selected["path"], pending_with_key)
                            )
                        _sp(goal, selected["path"], selected["name"], self.memory_path)
                        return self._record_and_return(
                            f"Got it — {selected['name']}.\n\n"
                            f"  Path: {selected['path']}\n"
                            f"  Task: {goal}\n\n"
                            f"Say yes and {forge.name} will get on it."
                        )
                    return self._record_and_return(
                        "That number isn't on the list — try 1 through " + str(len(options)) + "."
                    )

            # ── Step 2: Think — one LLM call to reason about everything ───────
            thought = self._think(message, pending)
            action   = thought.get("action", "chat")
            goal     = thought.get("goal", message)
            project_hint = thought.get("project_hint", "")
            is_confirming = thought.get("is_confirming_pending", False)
            self._last_thought_action = action  # captured for post-response audit

            print(f"Route  [{action}]  {message[:60]!r}", flush=True)

            # ── Step 3: Pending confirmation (LLM-judged) ─────────────────────
            if pending and is_confirming:
                clear_pending(self.memory_path)
                pending_type = pending.get("type", "existing_project")

                if pending_type == "firestore_query":
                    return self._record_and_return(self._run_firestore_query(pending))

                elif pending_type == "new_project":
                    from sam_brain.runner import supervisor_loop
                    from workers.names import resolve_worker_identity
                    plan = ProjectPlan.from_dict(pending["plan"])
                    project_path = pending["project_path"]
                    os.makedirs(project_path, exist_ok=True)
                    build_goal = plan.format_as_codex_prompt()
                    if self._coding_settings:
                        forge = resolve_worker_identity("code")
                        _worker_print(forge.name, f"Building {plan.name}...")
                        result = supervisor_loop(
                            goal=build_goal,
                            project_path=project_path,
                            project_name=plan.name,
                            test_command=plan.test_command or None,
                            settings_path=self._coding_settings,
                        )
                        log_task(f"Build {plan.name}", plan.name, result[:200], self.memory_path)
                        remember_project(plan.name, project_path, plan.stack, self.memory_path)
                        return self._record_and_return(result)
                    return self._record_and_return("Coding settings not found — can't start building right now.")

                else:
                    from sam_brain.runner import supervisor_loop
                    from workers.names import resolve_worker_identity
                    if self._coding_settings:
                        forge = resolve_worker_identity("code")
                        _worker_print(forge.name, f"Working on {pending['project_name']}...")
                        result = supervisor_loop(
                            goal=pending["goal"],
                            project_path=pending["project_path"],
                            project_name=pending["project_name"],
                            test_command=pending.get("test_command"),
                            settings_path=self._coding_settings,
                        )
                        log_task(pending["goal"], pending["project_name"], result[:200], self.memory_path)
                        remember_project(pending["project_name"], pending["project_path"], "", self.memory_path)
                        return self._record_and_return(result)
                    return self._record_and_return("Coding settings not found — can't delegate right now.")

            # ── Step 4: Act ───────────────────────────────────────────────────
            history_block = self._history_as_memory_block()

            if action == "find":
                return self._record_and_return(self._handle_find(message, memory))

            elif action == "open":
                return self._record_and_return(self._handle_open(project_hint or goal))

            elif action == "execute":
                return self._record_and_return(self._handle_execute(goal))

            elif action in ("code", "run"):
                return self._record_and_return(self._handle_code_smart(goal, project_hint))

            elif action == "query":
                return self._record_and_return(self._handle_query(message, memory))

            elif action == "remember":
                return self._record_and_return(self._handle_remember(message, memory))

            elif action == "skill":
                skill_name = thought.get("skill_name", "")
                return self._record_and_return(self._handle_skill(skill_name, goal))

            else:  # chat — default
                history_block = self._history_as_memory_block(query=message)
                response = self.llm.generate_conversational_response(
                    message,
                    memory_block=history_block,
                )
                return self._record_and_return(response)

        except Exception as exc:
            from sam_brain.logger import log_error as _log_err, log_file_path
            _log_err("SamBrain.handle", exc)

            try:
                from sam_brain.self_repair import attempt_fix
                _, repair_msg = attempt_fix(
                    exc,
                    task=message,
                    memory_path=self.memory_path,
                    llm_client=self.llm,
                )
                response = repair_msg
            except Exception:
                response = (
                    f"Something went wrong on my end. "
                    f"The full error has been saved to:\n  {log_file_path()}\n\nShort: {exc}"
                )

            self._history.append({"role": "assistant", "content": response})
            return response

    def _record_and_return(self, response: str) -> str:
        """Record Sam's response to history, check pending music like, audit quality."""
        print(f"[SAM] {response[:120]}{'…' if len(response) > 120 else ''}", flush=True)
        self._history.append({"role": "assistant", "content": response})

        # ── Music like detection — LLM judges, not keywords ──────────────────
        if self._pending_music_like:
            pending = self._pending_music_like
            self._pending_music_like = None
            recent_user = next(
                (t["content"] for t in reversed(self._history[:-1]) if t["role"] == "user"),
                "",
            )
            try:
                if not self._llm_judge_music_change(pending["title"], recent_user):
                    from sam_brain.memory import mark_music_liked
                    mark_music_liked(pending["title"], self.memory_path)
            except Exception:
                pass

        # ── Post-response quality audit + passive learning ────────────────────
        try:
            user_msg = next(
                (t["content"] for t in reversed(self._history[:-1]) if t["role"] == "user"),
                "",
            )
            from sam_brain.conversation_audit import evaluate_turn
            evaluate_turn(
                user_msg=user_msg,
                sam_response=response,
                action_taken=self._last_thought_action,
                history=self._history,
                memory_path=self.memory_path,
            )
            from sam_brain.passive_learner import save_learnings
            save_learnings(user_msg, self.memory_path, llm_client=self.llm)
        except Exception:
            pass

        return response

    # ------------------------------------------------------------------
    # _think — LLM reasons about intent before Sam acts
    # ------------------------------------------------------------------

    def _think(self, message: str, pending: dict | None) -> dict:
        """
        Ask the LLM to reason about what the user wants.
        Returns a dict: {intent, action, project_hint, goal, is_confirming_pending}
        """
        # Build history snippet (last 6 turns)
        recent = self._history[-7:-1]  # exclude current message
        history_lines = "\n".join(
            f"  {'User' if m['role'] == 'user' else 'Sam'}: {m['content'][:120]}"
            for m in recent
        ) or "  (start of conversation)"

        # Known projects and facts from memory — prioritized and relevant
        known_projects = "(none saved yet)"
        saved_facts = "(none saved yet)"
        try:
            from sam_brain.memory import build_context_block
            ctx = build_context_block(self.memory_path, query=message)
            if ctx["projects"]:
                known_projects = "\n".join(
                    f"  - {p['name']} at {p['path']}" for p in ctx["projects"]
                )
            if ctx["facts"]:
                saved_facts = "\n".join(f"  - {f}" for f in ctx["facts"])
        except Exception:
            pass

        # Skills section — what Sam knows how to do (built-in + acquired)
        skills_section = ""
        try:
            from sam_brain.skills import get_skills_for_prompt
            skills_section = get_skills_for_prompt(self.memory_path)
        except Exception:
            pass

        # Pending task summary
        if pending:
            ptype = pending.get("type", "task")
            pname = pending.get("project_name", "")
            pgoal = pending.get("goal", pending.get("message", ""))
            pending_section = (
                f"Pending task (waiting for user confirmation):\n"
                f"  Type: {ptype}\n"
                f"  Project: {pname}\n"
                f"  Task: {pgoal[:120]}"
            )
        else:
            pending_section = "Pending task: none"

        sam_root = str(Path(__file__).parent.parent)
        mem_display = str(self.memory_path) if self.memory_path else "(not set)"

        prompt = _THINK_PROMPT.format(
            sam_root=sam_root,
            memory_path=mem_display,
            skills_section=skills_section,
            history=history_lines,
            known_projects=known_projects,
            saved_facts=saved_facts,
            pending_section=pending_section,
            message=message,
        )

        try:
            import json as _j
            from urllib import request as _req
            payload = _j.dumps({
                "model": self.llm.resolve_model(),
                "prompt": prompt,
                "stream": False,
            }).encode()
            req = _req.Request(
                f"{self.llm.settings.base_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=15) as resp:
                body = _j.loads(resp.read())
                raw = str(body.get("response", "")).strip()

            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                parsed = _j.loads(raw[start:end])
                # Validate action field
                valid_actions = {"chat", "find", "open", "code", "run", "execute", "remember", "query", "skill"}
                if parsed.get("action") not in valid_actions:
                    parsed["action"] = "chat"
                return parsed
        except Exception:
            pass

        # Fallback — safe default
        return {
            "intent": message,
            "action": "chat",
            "project_hint": "",
            "goal": message,
            "skill_name": "",
            "is_confirming_pending": False,
        }

    # ------------------------------------------------------------------
    # _handle_code_smart — find project intelligently before delegating
    # ------------------------------------------------------------------

    def _handle_code_smart(self, goal: str, project_hint: str) -> str:
        """
        Find the right project then delegate.
        Order: memory → filesystem search → ask user ONCE.
        Never asks the user to rephrase — tries everything first.
        """
        from sam_brain.coding_agent import save_pending
        from sam_brain.discovery import search
        from sam_brain.memory import list_projects
        from sam_brain.planner import is_new_project_request, generate_plan
        from workers.names import resolve_worker_identity
        nova  = resolve_worker_identity("search")
        forge = resolve_worker_identity("code")

        # Is this a brand-new project to build?
        if is_new_project_request(goal, project_hint=project_hint):
            return self._handle_new_project(goal)

        # ── 1. Check saved memory first ───────────────────────────────────────
        known = list_projects(self.memory_path)
        if project_hint and known:
            hint_lower = project_hint.lower()
            matches = [p for p in known if hint_lower in p["name"].lower() or hint_lower in p["path"].lower()]
            if len(matches) == 1:
                p = matches[0]
                save_pending(goal, p["path"], p["name"], self.memory_path)
                return (
                    f"I have {p['name']} in memory.\n\n"
                    f"  Path: {p['path']}\n"
                    f"  Task: {goal}\n\n"
                    f"Say yes and {forge.name} will get on it."
                )

        # ── 2. Search the filesystem ──────────────────────────────────────────
        search_term = project_hint or goal
        _worker_print(nova.name, f"Searching for: {search_term}")
        results = search(search_term) if project_hint else []

        if not results and not project_hint:
            # No hint and nothing to search — check if there's only one known project
            if len(known) == 1:
                p = known[0]
                save_pending(goal, p["path"], p["name"], self.memory_path)
                return (
                    f"I'll work on {p['name']} — it's the project I know about.\n\n"
                    f"  Path: {p['path']}\n"
                    f"  Task: {goal}\n\n"
                    f"Say yes and {forge.name} will get on it."
                )
            return (
                f"Got the task: \"{goal}\"\n\n"
                f"Which project should {forge.name} work on? Give me a name and I'll find it."
            )

        if len(results) == 1:
            p = results[0]
            from sam_brain.memory import remember_project
            remember_project(p.name, str(p.path), p.project_type, self.memory_path)
            save_pending(goal, str(p.path), p.name, self.memory_path)
            return (
                f"{nova.name} found it.\n\n"
                f"  Project: {p.name}\n"
                f"  Path:    {p.path}\n"
                f"  Task:    {goal}\n\n"
                f"Say yes and {forge.name} will get on it."
            )

        if results:
            # Multiple results — try exact name match first
            if project_hint:
                exact = [r for r in results if r.name.lower() == project_hint.lower()]
                if exact:
                    p = exact[0]
                    from sam_brain.memory import remember_project
                    remember_project(p.name, str(p.path), p.project_type, self.memory_path)
                    save_pending(goal, str(p.path), p.name, self.memory_path)
                    return (
                        f"Found {p.name}.\n\n"
                        f"  Path: {p.path}\n"
                        f"  Task: {goal}\n\n"
                        f"Say yes and {forge.name} will get on it."
                    )

            top = results[:5]
            self._save_selection_pending(goal, top, task_type="code")
            return (
                f"I found {len(results)} matches for \"{project_hint}\". Which one?\n\n"
                + "\n\n".join(p.display(i + 1) for i, p in enumerate(top))
                + "\n\nJust reply with the number."
            )

        # Nothing found anywhere
        return (
            f"{nova.name} searched but couldn't find \"{project_hint}\" on this machine.\n\n"
            f"Can you tell me where it lives? Give me the folder path and I'll take it from there."
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _handle_find(self, message: str, memory: dict | None) -> str:
        """Block 3: Real file-system search across the machine."""
        from sam_brain.discovery import extract_keyword, search, format_results
        from sam_brain.coding_agent import clear_pending
        from workers.names import resolve_worker_identity
        nova = resolve_worker_identity("search")

        keyword = extract_keyword(message, self.llm)
        if not keyword:
            return (
                "I want to search for that, but I'm not sure what name to look for. "
                "Can you tell me the project or folder name?"
            )

        _worker_print(nova.name, f"Searching for: {keyword}")
        results = search(keyword)

        from sam_brain.memory import remember_project
        for p in results[:3]:
            remember_project(p.name, str(p.path), p.project_type, self.memory_path)

        # If multiple results, save as selection pending so user can pick by number.
        # Clear any stale pending first so old tasks don't interfere.
        clear_pending(self.memory_path)
        if len(results) > 1:
            top = results[:5]
            self._save_selection_pending(message, top, task_type="find")

        return format_results(keyword, results)

    def _handle_open(self, hint: str) -> str:
        """Open a folder in Windows Explorer. Tries memory, then searches."""
        import os
        from pathlib import Path as _Path

        # 1. Check if hint is already a path
        candidate = _Path(hint)
        if candidate.exists():
            os.startfile(str(candidate))
            return f"Opened {candidate.name} in Explorer."

        # 2. Check memory for a matching project
        try:
            from sam_brain.memory import list_projects
            known = list_projects(self.memory_path)
            hint_lower = hint.lower()
            for p in known:
                if hint_lower in p["name"].lower() or hint_lower in p["path"].lower():
                    target = _Path(p["path"])
                    if target.exists():
                        os.startfile(str(target))
                        return f"Opened {p['name']} in Explorer.\n  {target}"
        except Exception:
            pass

        # 3. Look in recent conversation for a path
        for turn in reversed(self._history[-10:]):
            import re as _re
            m = _re.search(r'[A-Za-z]:\\[^\s\'"<>\n]+', turn["content"])
            if m:
                path_str = m.group(0).rstrip(".,;)")
                target = _Path(path_str)
                if target.exists():
                    os.startfile(str(target))
                    return f"Opened {target.name} in Explorer."

        return (
            f"I couldn't find a folder matching \"{hint}\" to open. "
            f"Can you give me the exact path?"
        )

    def _handle_execute(self, goal: str) -> str:
        """
        Execute a one-shot desktop task immediately.
        The LLM classifies the goal — no hardcoded keyword routing here.
        Order: classify → check skills → reuse/patch/write → auto-fix → save.
        """
        from sam_brain.executor import (
            _classify_execute_goal,
            check_skills,
            run_skill_script,
            patch_skill,
            llm_write_script,
            run_and_learn,
            desktop_act,
            increment_run_count,
            _describe_with_ocr,
            _capture_screen,
        )
        from sam_brain.memory import save_music_pref, get_liked_music
        from workers.names import resolve_worker_identity
        import tempfile

        worker = resolve_worker_identity("execute")
        _worker_print(worker.name, f"Execute: {goal[:60]}")

        # ── Classify the goal (LLM decides everything) ────────────────────────
        classification = _classify_execute_goal(goal, self.llm)
        task_type = classification["task_type"]
        music_title = classification.get("music_title", "")
        is_unprompted_play = classification.get("is_unprompted_play", False)

        # ── Unprompted play: use liked playlist ───────────────────────────────
        if task_type == "music_play" and is_unprompted_play:
            liked = get_liked_music(self.memory_path)
            if liked:
                top = liked[0]
                skill_match = check_skills(top["title"], self.llm)
                if skill_match and not skill_match.needs_patch:
                    ok, output = run_skill_script(skill_match.file_path)
                    if ok:
                        increment_run_count(skill_match.slug)
                        save_music_pref(top["title"], skill_match.slug, self.memory_path)
                        self._last_music_title = top["title"]
                        self._pending_music_like = {"title": top["title"], "skill": skill_match.slug}
                        return f"Playing {top['title']} — your most played."

        # ── Vision: screenshot + OCR describe ────────────────────────────────
        if task_type == "vision":
            tmp = Path(tempfile.gettempdir()) / "sam_screen.png"
            _capture_screen(tmp)
            description = _describe_with_ocr(tmp)
            return f"Here's what I can see:\n\n{description}"

        # ── Desktop act: vision loop for click/press tasks ────────────────────
        if task_type == "act":
            ok, msg = desktop_act(goal, self.llm)
            if ok:
                if music_title:
                    save_music_pref(music_title, "", self.memory_path)
                return msg
            # Fall through to script approach if act failed

        # ── Check skills library ──────────────────────────────────────────────
        match = check_skills(goal, self.llm)

        if match and not match.needs_patch:
            ok, output = run_skill_script(match.file_path)
            if ok:
                increment_run_count(match.slug)
                if task_type == "music_play":
                    from sam_brain.executor import _ensure_media_playing
                    _ensure_media_playing()
                    if music_title:
                        save_music_pref(music_title, match.slug, self.memory_path)
                        self._last_music_title = music_title
                        self._pending_music_like = {"title": music_title, "skill": match.slug}
                return f"Done. {output[:200]}" if output else "Done."
            _worker_print(worker.name, f"Skill {match.slug} failed, writing fresh script")

        if match and match.needs_patch:
            try:
                base_code = match.file_path.read_text(encoding="utf-8")
                patched = patch_skill(base_code, goal, self.llm)
                ok, output = run_and_learn(patched, goal, self.llm)
                if ok:
                    if task_type == "music_play" and music_title:
                        new_match = check_skills(goal, self.llm)
                        save_music_pref(music_title, new_match.slug if new_match else "", self.memory_path)
                        self._last_music_title = music_title
                        self._pending_music_like = {"title": music_title, "skill": ""}
                    return f"Done. {output[:200]}" if output else "Done."
            except Exception:
                pass

        # ── No match: write fresh script via LLM ──────────────────────────────
        _worker_print(worker.name, "Writing script...")
        script = llm_write_script(goal, self.llm)
        if not script:
            return "I couldn't figure out how to do that. Can you give me more detail?"

        ok, output = run_and_learn(script, goal, self.llm)
        if ok:
            if task_type == "music_play" and music_title:
                new_match = check_skills(goal, self.llm)
                save_music_pref(music_title, new_match.slug if new_match else "", self.memory_path)
                self._last_music_title = music_title
                self._pending_music_like = {"title": music_title, "skill": ""}
            return f"Done. {output[:200]}" if output else "Done."

        return f"I tried but something went wrong: {output[:300]}"

    def _llm_judge_music_change(self, title: str, user_message: str) -> bool:
        """
        Ask the LLM whether the user's message indicates they want to change or stop the music.
        Returns True if they want to change/stop, False if they're happy with it.
        Safe default on LLM failure: False (don't mark as liked if unsure).
        """
        if not title or not user_message:
            return False
        prompt = (
            f'Sam just played music: "{title}"\n'
            f'User\'s next message: "{user_message}"\n\n'
            'Did the user want to change or stop the music? '
            'Reply with only "yes" or "no".'
        )
        try:
            import json as _j
            from urllib import request as _req
            payload = _j.dumps({
                "model": self.llm.resolve_model(),
                "prompt": prompt,
                "stream": False,
            }).encode()
            req = _req.Request(
                f"{self.llm.settings.base_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=10) as resp:
                body = _j.loads(resp.read())
                answer = str(body.get("response", "")).strip().lower()
                return answer.startswith("yes")
        except Exception:
            return False

    def _handle_learn_skill(self, message: str) -> str:
        """
        User wants Sam to learn a new skill.
        Uses Codex to write a Python script for it, then saves it to the registry.
        """
        from sam_brain.skills import save_acquired_skill, find_skill
        from sam_brain.runner import supervisor_loop
        from workers.names import resolve_worker_identity
        forge = resolve_worker_identity("code")

        # Extract what to learn
        import re as _re
        goal = _re.sub(
            r'^(learn\s+(to|how\s+to)\s+|install\s+skill\s+(for\s+)?|'
            r'add\s+skill\s+(for\s+)?|teach\s+yourself\s+(to\s+)?|'
            r'save\s+this\s+as\s+a\s+skill[:\s]*|remember\s+how\s+to\s+)',
            '', message.strip(), flags=_re.IGNORECASE
        ).strip()

        if not goal or len(goal) < 5:
            return "What should I learn? Tell me: 'learn to send WhatsApp messages' for example."

        # Check if we already have this skill
        existing = find_skill(goal[:40], self.memory_path)
        if existing and existing.get("status") == "active":
            return (
                f"I already know how to do that — it's saved as the **{existing['name']}** skill.\n"
                f"Say 'run {existing['name']}' to use it."
            )

        _worker_print(forge.name, f"Building skill: {goal[:50]}...")

        # Use the coding agent to write a standalone Python script
        script_goal = (
            f"Write a standalone Python script (no arguments needed) that does this:\n\n"
            f"{goal}\n\n"
            f"Requirements:\n"
            f"- Must run on Windows with Python 3.10+\n"
            f"- Must be fully self-contained (handle its own imports)\n"
            f"- Print a clear result or confirmation when done\n"
            f"- Handle errors gracefully and print what went wrong\n"
            f"- No user input needed — use environment variables (SKILL_*) for any dynamic values\n\n"
            f"Output ONLY the Python script. No explanation."
        )

        if not self._coding_settings:
            return "I need coding settings configured to build new skills — ask me to set up Codex first."

        try:
            result = supervisor_loop(
                goal=script_goal,
                project_path=str(re.sub(r'[^a-z0-9]', '_', goal.lower()[:20])),
                project_name=goal[:30],
                test_command=None,
                settings_path=self._coding_settings,
            )
        except Exception as e:
            return f"I tried to build the skill but ran into a problem: {e}"

        # Extract Python code from the result
        code_match = re.search(r'```python\s*([\s\S]+?)```', result)
        script = code_match.group(1).strip() if code_match else result.strip()

        if not script or not script.startswith(("import", "#", "from", "def ", "class ")):
            return (
                f"I worked on building '{goal}' but the result doesn't look like a clean script. "
                f"Try describing it more specifically and I'll try again."
            )

        # Save it
        import re as _re2
        slug = _re2.sub(r'[^a-z0-9]+', '_', goal.lower().strip())[:40].strip('_')
        tags = [w for w in goal.lower().split() if len(w) > 3][:5]
        save_acquired_skill(slug, goal, script, tags, self.memory_path)

        return (
            f"Learned and saved: **{slug}**\n\n"
            f"Description: {goal}\n\n"
            f"Say 'run {slug}' or 'use the {slug} skill' any time to run it.\n"
            f"I've saved the script to sam_skills/{slug}.py"
        )

    def _handle_skill(self, skill_name: str, goal: str) -> str:
        """Run a built-in or acquired skill."""
        from sam_brain.skills import find_skill, run_skill, list_acquired
        from workers.names import resolve_worker_identity
        forge = resolve_worker_identity("code")

        if not skill_name:
            # User asked about skills in general
            acquired = list_acquired(self.memory_path)
            if not acquired:
                return (
                    "I don't have any acquired skills yet — I learn new ones as we work together. "
                    "When I successfully automate something new, I'll save it as a skill so I can do it again."
                )
            lines = [f"Here are my {len(acquired)} acquired skill(s):\n"]
            for s in acquired:
                lines.append(f"  {s['name']}: {s['description']}")
                if s.get("run_count"):
                    lines.append(f"    (used {s['run_count']} time(s))")
            return "\n".join(lines)

        # ── Built-in: Flutter UI tester ───────────────────────────────────────
        if "flutter" in skill_name.lower() or "ui_test" in skill_name.lower() or "test" in skill_name.lower():
            return self._handle_flutter_test(goal)

        # ── Acquired skill ────────────────────────────────────────────────────
        skill = find_skill(skill_name, self.memory_path)
        if not skill:
            return (
                f"I don't have a skill called \"{skill_name}\" yet.\n\n"
                f"If you show me how to do it once, I can save it as a skill for next time."
            )

        if skill.get("status") == "planned":
            return self._handle_flutter_test(goal) if "flutter" in skill.get("name", "") else (
                f"I know about \"{skill_name}\" but haven't built that capability yet. "
                f"Ask me to build it and {forge.name} can implement it."
            )

        _worker_print(forge.name, f"Running skill: {skill_name}...")
        output = run_skill(skill["name"], self.memory_path)
        return f"Ran **{skill_name}**:\n\n{output}"

    def _handle_flutter_test(self, goal: str) -> str:
        """Find the Flutter project, load per-app credentials, and run the UI test suite."""
        from sam_brain.discovery import search
        from sam_brain.memory import list_projects, get_app_credential, save_app_credential
        from sam_brain.flutter_tester import run_flutter_ui_test, _has_login_in_code
        from workers.names import resolve_worker_identity
        nova     = resolve_worker_identity("search")
        sentinel = resolve_worker_identity("test")

        # ── Parse credential hints from the goal message ─────────────────────
        # "test with admin@x.com / pass123" or "test as admin using admin@x.com / pass"
        import re as _re
        inline_email = ""
        inline_pass = ""
        inline_role = ""

        cred_match = _re.search(
            r'(?:with|using|as\s+\w+\s+using)?\s*([\w.+-]+@[\w.-]+)\s*[/|]\s*(\S+)',
            goal, _re.IGNORECASE
        )
        if cred_match:
            inline_email = cred_match.group(1)
            inline_pass = cred_match.group(2).rstrip(".,;)")

        role_match = _re.search(r'\bas\s+(admin|resident|user|owner|guest|manager)\b', goal, _re.IGNORECASE)
        if role_match:
            inline_role = role_match.group(1).lower()

        # ── Find the project ─────────────────────────────────────────────────
        project_path = None
        project_name = None

        path_match = _re.search(r'[A-Za-z]:\\[^\s\'"<>]+', goal)
        if path_match:
            project_path = path_match.group(0).rstrip(".,;)")
            from pathlib import Path as _Path
            project_name = _Path(project_path).name

        if not project_path:
            known = list_projects(self.memory_path)
            flutter_projects = [p for p in known if "flutter" in p.get("type", "").lower()]
            if flutter_projects:
                p = flutter_projects[0]
                project_path, project_name = p["path"], p["name"]
            elif len(known) == 1:
                project_path, project_name = known[0]["path"], known[0]["name"]

        if not project_path:
            # Extract name from goal and search filesystem
            stop = {"test", "flutter", "app", "the", "run", "my", "our", "ui", "with", "using"}
            words = [w for w in goal.lower().split() if len(w) > 3 and w not in stop]
            hint = words[0] if words else ""
            if hint:
                _worker_print(nova.name, f"Searching for project: {hint}")
                results = search(hint)
                if results:
                    project_path = str(results[0].path)
                    project_name = results[0].name

        if not project_path:
            return (
                "Which project should I test? Give me the name or path — "
                "e.g. 'test my focusflow app' or 'test C:\\Projects\\focusflow'"
            )

        # ── Credentials: inline → memory → ask ───────────────────────────────
        email, password, role = inline_email, inline_pass, inline_role

        if not email:
            saved = get_app_credential(project_name, self.memory_path, role=role)
            if saved:
                email = saved.get("email", "")
                password = saved.get("password", "")
                role = saved.get("role", role)
                print(f"[TESTER] Using saved credentials for {project_name} ({role}): {email}", flush=True)

        # Save any inline credentials for next time
        if inline_email and inline_pass:
            save_app_credential(project_name, inline_role or "default", inline_email, inline_pass, self.memory_path)
            print(f"[TESTER] Saved {inline_role or 'default'} credentials for {project_name}", flush=True)

        # Check if login is even needed
        needs_login = _has_login_in_code(project_path)
        if needs_login and not email:
            return (
                f"Ready to test **{project_name}**.\n"
                f"  Path: {project_path}\n\n"
                f"I found login code in the project. What are the test credentials?\n\n"
                f"Say:  `test with email@example.com / password`\n"
                f"Or:   `test as admin with admin@x.com / adminpass`\n\n"
                f"I'll save them for {project_name} so you never have to give them again. "
                f"Different roles (admin, resident, etc.) are stored separately."
            )

        # ── Run ───────────────────────────────────────────────────────────────
        _worker_print(sentinel.name, f"Starting UI test — {project_name} ({role or 'default'})")
        report = run_flutter_ui_test(
            project_path,
            email=email,
            password=password,
            role=role,
            memory_path=self.memory_path,
        )
        return report

    def _handle_new_project(self, message: str) -> str:
        """Block 6: Plan a brand-new project, present it, wait for go-ahead."""
        from pathlib import Path as _Path
        from sam_brain.planner import generate_plan
        from sam_brain.coding_agent import save_pending
        from workers.names import resolve_worker_identity
        vector = resolve_worker_identity("plan")

        workspace = str(_Path(__file__).parent.parent / "workspace")

        _worker_print(vector.name, "Planning new project from request...")
        plan = generate_plan(message, workspace, self.llm)
        if plan is None:
            return (
                f"{vector.name} couldn't put together a clean plan for that. "
                "Can you give me a bit more detail about what you want to build?"
            )

        slug = re.sub(r'[^a-z0-9]+', '-', plan.name.lower()).strip('-')
        project_path = f"{workspace}\\{slug}"
        plan.workspace_path = project_path

        if self.memory_path:
            pending_path = _Path(self.memory_path).parent / "sam_brain_pending.json"
            pending_path.write_text(
                json.dumps({
                    "type": "new_project",
                    "plan": plan.to_dict(),
                    "project_path": project_path,
                    "project_name": plan.name,
                }, indent=2),
                encoding="utf-8",
            )

        return plan.format_for_user()


    # ------------------------------------------------------------------
    # Query handler — read live data from Firestore / databases
    # ------------------------------------------------------------------

    def _handle_query(self, message: str, memory: dict | None) -> str:
        """Route: read data from Firestore or other live source."""
        from sam_brain.discovery import search
        from sam_brain.memory import recall_all_keys
        from workers.names import resolve_worker_identity
        atlas = resolve_worker_identity("research")
        nova  = resolve_worker_identity("search")

        # Extract project hint and data question from message
        import re as _re
        # Check if a path was given directly in the message
        path_match = _re.search(r'[A-Za-z]:\\[^\s\'"<>]+', message)

        project_path = None
        project_name = None

        if path_match:
            from pathlib import Path as _Path
            project_path = path_match.group(0).rstrip(".,;)")
            project_name = _Path(project_path).name

        # Find the Firebase key early — we need it regardless of project count
        _worker_print(atlas.name, "Looking for Firebase key...")
        saved_keys = recall_all_keys(self.memory_path)
        key_path = None
        if saved_keys:
            key_path = list(saved_keys.values())[0]
        if not key_path:
            key_path = self._search_for_firebase_key()

        if not project_path:
            # Try to find the project by name
            from sam_brain.coding_agent import extract_code_task
            extracted = extract_code_task(message, self.llm)
            hint = extracted.get("project_hint", "")
            if hint:
                _worker_print(nova.name, f"Searching for project: {hint}")
                results = search(hint)
                if not results:
                    return f"I searched for \"{hint}\" but couldn't find it. Can you give me the folder path?"
                if len(results) == 1:
                    project_path = str(results[0].path)
                    project_name = results[0].name
                else:
                    top = results[:5]
                    # Save key_path in pending so number selection can pass it through
                    self._save_selection_pending(message, top, task_type="query", key_path=key_path)
                    return (
                        f"I found {len(results)} matches. Which one?\n\n"
                        + "\n\n".join(p.display(i + 1) for i, p in enumerate(top))
                        + "\n\nJust reply with the number."
                    )
            else:
                # Before giving up, check if we have a known project in memory
                from sam_brain.memory import list_projects as _list_projects, recall_facts as _recall_facts
                known = _list_projects(self.memory_path)
                if len(known) == 1:
                    project_path = known[0]["path"]
                    project_name = known[0]["name"]
                elif len(known) > 1:
                    top = known[:5]
                    import types as _types
                    ns_options = [_types.SimpleNamespace(name=p["name"], path=p["path"]) for p in top]
                    self._save_selection_pending(message, ns_options, task_type="query", key_path=key_path)
                    return (
                        "I have a few projects saved. Which one?\n\n"
                        + "\n".join(f"{i+1}. {p['name']} — {p['path']}" for i, p in enumerate(top))
                        + "\n\nJust reply with the number."
                    )
                else:
                    # Last resort: scan saved facts for a path
                    facts = _recall_facts(self.memory_path)
                    import re as _re2
                    for fact_text in reversed(facts):
                        m = _re2.search(r'[A-Za-z]:\\[^\s\'"<>]+|/[^\s\'"<>]+', fact_text)
                        if m:
                            raw = m.group(0).rstrip(".,;)")
                            from pathlib import Path as _Path2
                            project_path = raw
                            project_name = _Path2(raw).name
                            break
                    if not project_path:
                        return "Which project or app should I check? Give me a name or path."

        if not key_path:
            return (
                f"I found {project_name} at {project_path}, but I don't have a Firebase key to connect.\n\n"
                f"Can you give me the path to your service account key file? "
                f"I'll ask before using it and only store the path."
            )

        # Save as pending — ask before using the key
        if self.memory_path:
            import json as _json
            from pathlib import Path as _Path
            pending_data = {
                "type": "firestore_query",
                "message": message,
                "project_name": project_name,
                "project_path": project_path,
                "key_path": key_path,
            }
            pending_path = _Path(self.memory_path).parent / "sam_brain_pending.json"
            pending_path.write_text(_json.dumps(pending_data, indent=2), encoding="utf-8")

        return (
            f"I found {project_name} and a Firebase key.\n\n"
            f"  Project: {project_path}\n"
            f"  Key:     {key_path}\n\n"
            f"Can I use this key to query Firestore? Say yes and I'll get the data."
        )

    def _confirm_query(self, goal: str, project_name: str, project_path: str, pending: dict) -> str:
        """Show the user what we found and ask to confirm before querying."""
        key_path = pending.get("key_path", "")
        if self.memory_path:
            import json as _json
            from pathlib import Path as _Path
            pending_data = {
                "type": "firestore_query",
                "message": goal,
                "project_name": project_name,
                "project_path": project_path,
                "key_path": key_path,
            }
            pending_path = _Path(self.memory_path).parent / "sam_brain_pending.json"
            pending_path.write_text(_json.dumps(pending_data, indent=2), encoding="utf-8")
        return (
            f"Got it — {project_name}.\n\n"
            f"I'll need a Firebase key to query Firestore. "
            f"Say yes to confirm I can proceed, or give me the key path if I don't have it."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_selection_pending(self, goal: str, results: list, task_type: str = "code", key_path: str | None = None) -> None:
        """Save a numbered list of project options so user can pick by number."""
        if not self.memory_path:
            return
        import json as _json
        from pathlib import Path as _Path
        data = {
            "type": "project_selection",
            "goal": goal,
            "task_type": task_type,
            "key_path": key_path or "",
            "options": [{"name": p.name, "path": str(p.path)} for p in results],
        }
        pending_path = _Path(self.memory_path).parent / "sam_brain_pending.json"
        pending_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")

    def _run_firestore_query(self, pending: dict) -> str:
        """User confirmed — write and run a Firestore query, return results in plain English."""
        import sys, tempfile, subprocess, json as _json
        from pathlib import Path as _Path
        from workers.names import resolve_worker_identity
        from sam_brain.memory import store_key

        atlas    = resolve_worker_identity("research")
        sentinel = resolve_worker_identity("test")
        forge    = resolve_worker_identity("code")

        key_path     = pending.get("key_path", "")
        project_name = pending.get("project_name", "")
        question     = pending.get("message", "")

        if not key_path or not _Path(key_path).exists():
            return (
                f"The key file I found no longer exists at that path.\n"
                f"Can you give me the correct path to your Firebase service account key?"
            )

        # Save key to memory so Sam remembers it next time
        store_key("firebase_admin", key_path, self.memory_path)
        _worker_print(atlas.name, "Saved Firebase key path to memory.")

        # Build a Python script to query Firestore and return human-readable data
        # Ask Forge (via LLM) to write the right query based on the user's question
        _worker_print(forge.name, "Writing Firestore query script...")
        query_prompt = f"""\
Write a Python script that:
1. Connects to Firebase Firestore using the Admin SDK service account at: {key_path}
2. Answers this question: {question}
3. Prints the result as plain readable text (not JSON, not code — just the answer)

Rules:
- Use firebase_admin and google.cloud.firestore
- Initialize only if not already initialized
- Print results clearly — names, dates, amounts
- If a date/timestamp field exists, format it as a readable date
- Keep it short — just print the answer

Output ONLY the Python script, no explanation.
"""
        try:
            payload = _json.dumps({
                "model": self.llm.resolve_model(),
                "prompt": query_prompt,
                "stream": False,
            }).encode()
            from urllib import request as _req
            req = _req.Request(
                f"{self.llm.settings.base_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=30) as resp:
                body = _json.loads(resp.read())
                script_raw = str(body.get("response", "")).strip()

            # Extract the Python code block
            import re as _re
            code_match = _re.search(r'```python\s*([\s\S]+?)```', script_raw)
            if code_match:
                script = code_match.group(1).strip()
            elif script_raw.startswith("import") or script_raw.startswith("#"):
                script = script_raw
            else:
                # Fallback: basic residents query
                script = f"""\
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.Certificate(r"{key_path}")
    firebase_admin.initialize_app(cred)

db = firestore.client()
docs = db.collection('residents').stream()
found = False
for doc in docs:
    data = doc.to_dict()
    found = True
    name = data.get('name', data.get('fullName', doc.id))
    levy = data.get('levyDueDate', data.get('levy_due', data.get('nextPayment', 'N/A')))
    print(f"{{name}}: levy due {{levy}}")
if not found:
    print("No resident records found.")
"""

        except Exception as exc:
            script = f"""\
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.Certificate(r"{key_path}")
    firebase_admin.initialize_app(cred)

db = firestore.client()
docs = db.collection('residents').stream()
for doc in docs:
    data = doc.to_dict()
    name = data.get('name', doc.id)
    print(f"{{name}}: {{data}}")
"""

        # Write and run the script
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as f:
            f.write(script)
            tmp = f.name

        _worker_print(sentinel.name, "Running Firestore query...")
        try:
            result = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            output = (result.stdout + result.stderr).strip()
        except subprocess.TimeoutExpired:
            output = "Query timed out after 30 seconds."
        except Exception as exc:
            output = f"Error running query: {exc}"
        finally:
            _Path(tmp).unlink(missing_ok=True)

        if not output:
            return (
                f"{sentinel.name} ran the query but got no output back. "
                f"The collection might be empty or the field names differ. "
                f"Want me to try a different collection name?"
            )

        # firebase-admin not installed — install it and retry once
        if "No module named 'firebase_admin'" in output:
            _worker_print(sentinel.name, "firebase-admin not installed — installing now...")
            try:
                install = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "firebase-admin"],
                    capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace",
                )
                if install.returncode != 0:
                    return (
                        f"I tried to install firebase-admin but it failed:\n\n"
                        f"{install.stderr[:400]}\n\n"
                        f"Try running `pip install firebase-admin` in your terminal, then ask me again."
                    )
                _worker_print(sentinel.name, "firebase-admin installed — retrying query...")
                retry = subprocess.run(
                    [sys.executable, tmp],
                    capture_output=True, text=True, timeout=30,
                    encoding="utf-8", errors="replace",
                )
                output = (retry.stdout + retry.stderr).strip()
                if not output:
                    return "Installed firebase-admin and ran the query, but got no output. The collection might be empty."
            except Exception as exc:
                return f"Tried to auto-install firebase-admin but hit an error: {exc}"

        return f"Here's what I found:\n\n{output}"

    def _search_for_firebase_key(self) -> str | None:
        """Search common locations for a Firebase Admin SDK key file."""
        import glob
        import os
        from pathlib import Path as _Path
        home = _Path.home()
        search_dirs = [
            str(home / "Documents"),
            str(home / "Desktop"),
            str(home / "Downloads"),
            str(home),
        ]
        patterns = ["*firebase*adminsdk*.json", "*service-account*.json", "*serviceAccount*.json"]
        for d in search_dirs:
            for pat in patterns:
                matches = glob.glob(str(_Path(d) / "**" / pat), recursive=True)
                if matches:
                    return matches[0]
        return None

    # ------------------------------------------------------------------
    # Block 5: Memory (stub — wires up in Block 5)
    # ------------------------------------------------------------------

    def _handle_remember(self, message: str, memory: dict | None) -> str:
        """Block 5: Extract and save what the user wants remembered."""
        from sam_brain.memory import remember_fact, remember_project, store_key, recall_key, recall_all_keys

        msg_lower = message.lower()

        # Print conversation history to terminal
        if any(p in msg_lower for p in ["print our conversation", "show conversation", "print conversation", "show history"]):
            self.print_conversation()
            return "Done — full conversation printed to the terminal."

        # Detect key/credential questions — check memory FIRST before saying "I don't have it"
        key_indicators = ["key", "firebase", "service account", "api key", "secret", "credential", "token", "sdk"]
        if any(kw in msg_lower for kw in key_indicators):
            # Check if the user is ASKING whether Sam has it (vs. giving a new one)
            asking_indicators = ["do you have", "do you still have", "did you save", "have you got", "still have"]
            if any(phrase in msg_lower for phrase in asking_indicators):
                # Check memory first
                saved = recall_all_keys(self.memory_path)
                if saved:
                    names = list(saved.keys())
                    parts = [f"  {name}: {path}" for name, path in saved.items()]
                    return (
                        f"Yes — I have {len(names)} key(s) saved:\n\n" + "\n".join(parts) +
                        "\n\nWant me to use one of these?"
                    )
                # Not in memory — search the filesystem
                key_path = self._search_for_firebase_key()
                if key_path:
                    return (
                        f"I don't have it saved yet, but I found a key file at:\n\n"
                        f"  {key_path}\n\n"
                        f"Should I save this path to memory so I can use it when needed? "
                        f"(I'll only store the path, never the key contents.)"
                    )
                return (
                    "I don't have any keys saved yet. If you give me the file path, "
                    "I'll save it to memory — just the path, not the key itself."
                )
            # Extract the path if mentioned
            import re
            path_match = re.search(r'[A-Za-z]:\\[^\s\'"]+|/[^\s\'"]+', message)
            if path_match:
                key_path = path_match.group(0)
                # Ask user before storing — safety first
                return (
                    f"I see this looks like a sensitive key reference.\n\n"
                    f"Path I found: {key_path}\n\n"
                    f"Should I save this to my secure vault? I'll only store the file path, "
                    f"not the key contents themselves. Reply 'yes save key' to confirm."
                )
            return (
                "Looks like you want me to remember a key or credential. "
                "Can you tell me the exact file path? I'll store just the path (not the key itself) "
                "and always ask before using it."
            )

        # General fact — strip the "remember that / save this / note that" preamble
        import re
        fact = re.sub(
            r'^(remember\s+(that\s+)?|save\s+this[:\s]*|note\s+that\s+|don\'t\s+forget\s+(that\s+)?|keep\s+note\s+that\s+)',
            '',
            message.strip(),
            flags=re.IGNORECASE,
        ).strip()
        if not fact:
            fact = message.strip()
        remember_fact(fact, self.memory_path)

        # If the message contains a file-system path, also index it as a known project
        # so query/code handlers can find it without asking again.
        path_in_msg = re.search(r'[A-Za-z]:\\[^\s\'"<>]+|/[^\s\'"<>]+', message)
        if path_in_msg:
            raw_path = path_in_msg.group(0).rstrip(".,;)")
            from pathlib import Path as _Path
            p = _Path(raw_path)
            remember_project(p.name, raw_path, "", self.memory_path)

        return f"Got it, I'll remember: \"{fact}\""

    # ------------------------------------------------------------------
    # Memory helpers (Block 1 — read-only, best effort)
    # ------------------------------------------------------------------

    def _history_as_memory_block(self, query: str = "") -> dict:
        """
        Build the memory block for the chat LLM.
        Uses build_context_block for clean, prioritized, deduplicated data.
        """
        recent = self._history[-16:]
        turns = [{"role": m["role"], "message": m["content"]} for m in recent]

        try:
            from sam_brain.memory import build_context_block
            ctx = build_context_block(self.memory_path, query=query)
            known_projects = [f"{p['name']} at {p['path']}" for p in ctx["projects"]]
            saved_facts = ctx["facts"]
        except Exception:
            known_projects = []
            saved_facts = []

        return {
            "long_term": {
                "value": {
                    "recent_conversation": turns,
                    "relevant_facts": saved_facts,
                    "relevant_lessons": [],
                    "known_projects": known_projects,
                }
            }
        }

    def print_conversation(self) -> None:
        """Print the full conversation history to the terminal."""
        print("\n" + "="*60, flush=True)
        print("CONVERSATION HISTORY", flush=True)
        print("="*60, flush=True)
        for turn in self._history:
            role = "YOU" if turn["role"] == "user" else "SAM"
            print(f"\n[{role}] {turn['content']}", flush=True)
        print("\n" + "="*60, flush=True)

    def _load_memory(self) -> dict[str, Any] | None:
        """Read memory.json if it exists. Returns None on any failure."""
        if not self.memory_path or not self.memory_path.exists():
            return None
        try:
            return json.loads(self.memory_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Block status (for diagnostics / future use)
    # ------------------------------------------------------------------

    def active_blocks(self) -> set[str]:
        return set(self._active_blocks)

    def __repr__(self) -> str:
        return f"SamBrain(blocks={sorted(self._active_blocks)})"
