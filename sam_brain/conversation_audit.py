"""
Sam's conversation quality auditor.

After every response, Sam evaluates himself:
  - Did I actually do what the user asked, or did I just ask a question back?
  - Am I repeating the same question I already asked?
  - Am I evading action when the user gave me a clear task?

When a failure is detected it's logged to sam_conversation_audit.json.

The user (or Claude Code) can then read that file and patch the bug
causing Sam to misbehave — without the user having to copy-paste anything.

Sam surfaces this automatically:
  "do you have conversation issues?" → format_issues_for_review()
  "why did you ask that?" → last_issue_summary()
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _audit_path(memory_path: Path | None) -> Path | None:
    if memory_path is None:
        return None
    return Path(memory_path).parent / "sam_conversation_audit.json"


def _load(memory_path: Path | None) -> dict:
    path = _audit_path(memory_path)
    if path is None or not path.exists():
        return {"issues": [], "consecutive_failures": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"issues": [], "consecutive_failures": 0}


def _save(data: dict, memory_path: Path | None) -> None:
    path = _audit_path(memory_path)
    if path is None:
        return
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Sam is supposed to act but instead asks or refuses
_EVASION_PHRASES = [
    "can you tell me", "which project", "could you clarify", "i need more information",
    "i need more info", "i'm not sure which", "can you give me more", "please provide",
    "please tell me", "what project", "which one", "what folder", "which folder",
    "i need you to tell me", "could you tell me more",
]

_REFUSAL_PHRASES = [
    "i can't do that", "i cannot do that", "i'm not able to", "i'm unable to",
    "i don't know how to", "i don't have access to",
]

# These are GOOD "holding" responses — Sam found the project and is asking confirm, not evading
_LEGITIMATE_HOLD_PHRASES = [
    "say yes", "say go", "ready to", "want me to open", "should i",
    "found it", "i have it", "i found", "path:", "task:",
]

# Action-type routes — Sam is expected to produce a tangible result
_ACTION_ROUTES = {"code", "run", "find", "query"}


# ---------------------------------------------------------------------------
# Heuristic checks (fast — no LLM)
# ---------------------------------------------------------------------------

def _is_evasion(response: str) -> bool:
    r = response.lower()
    has_evasion = any(p in r for p in _EVASION_PHRASES)
    has_refusal = any(p in r for p in _REFUSAL_PHRASES)
    is_legit_hold = any(p in r for p in _LEGITIMATE_HOLD_PHRASES)
    return (has_evasion or has_refusal) and not is_legit_hold


def _extract_questions(text: str) -> list[str]:
    return re.findall(r'[^.!?\n]{10,}\?', text)


def _question_overlap(q1: str, q2: str) -> float:
    stop = {"i", "you", "the", "a", "an", "is", "it", "that", "me", "my", "your", "do", "can", "will", "to"}
    w1 = set(q1.lower().split()) - stop
    w2 = set(q2.lower().split()) - stop
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / max(len(w1), len(w2))


def _is_repeated_question(response: str, history: list[dict]) -> bool:
    current_questions = _extract_questions(response)
    if not current_questions:
        return False
    # Check last 6 Sam turns (excluding current)
    sam_turns = [t["content"] for t in history[-12:] if t.get("role") == "assistant"]
    for prev in sam_turns[:-1]:
        for cq in current_questions:
            for pq in _extract_questions(prev):
                if _question_overlap(cq, pq) > 0.55:
                    return True
    return False


def _quick_check(
    user_msg: str,
    sam_response: str,
    action_taken: str,
    history: list[dict],
) -> str | None:
    """
    Fast heuristic scan. Returns pattern name if issue found, else None.
    Called on every turn — must be cheap.
    """
    # Pattern 1: action route but Sam is evading instead of acting
    if action_taken in _ACTION_ROUTES:
        if _is_evasion(sam_response):
            return "action_not_taken"

    # Pattern 2: Sam is asking the same question again
    if _is_repeated_question(sam_response, history):
        return "repeated_question"

    # Pattern 3: user gave a very specific path/name but Sam says it can't find it
    # (when that path/name was already in Sam's memory or history)
    path_in_user = re.search(r'[A-Za-z]:\\[^\s\'"<>]+|/[^\s\'"<>]+', user_msg)
    if path_in_user:
        r_lower = sam_response.lower()
        if "couldn't find" in r_lower or "can't find" in r_lower or "not find" in r_lower:
            return "ignored_explicit_path"

    return None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_issue(
    user_msg: str,
    sam_response: str,
    action_taken: str,
    pattern: str,
    history: list[dict],
    memory_path: Path | None,
) -> str:
    """Write a detected issue to the audit queue. Returns the issue ID."""
    issue_id = f"audit_{int(time.time())}"
    data = _load(memory_path)

    ctx = [
        {"role": t["role"], "content": t["content"][:300]}
        for t in history[-6:]
    ]

    data["issues"].append({
        "id": issue_id,
        "timestamp": time.strftime("%Y-%m-%d %H:%M"),
        "status": "pending_review",
        "pattern": pattern,
        "action_intended": action_taken,
        "user_message": user_msg[:300],
        "sam_response": sam_response[:500],
        "conversation_context": ctx,
    })
    data["issues"] = data["issues"][-30:]
    data["consecutive_failures"] = data.get("consecutive_failures", 0) + 1
    _save(data, memory_path)
    return issue_id


def mark_reviewed(issue_id: str, note: str, memory_path: Path | None) -> None:
    data = _load(memory_path)
    for issue in data.get("issues", []):
        if issue["id"] == issue_id:
            issue["status"] = "reviewed"
            issue["review_note"] = note
            issue["reviewed_at"] = time.strftime("%Y-%m-%d %H:%M")
            break
    _save(data, memory_path)


def reset_consecutive(memory_path: Path | None) -> None:
    """Call after a successful action turn to reset the failure streak."""
    data = _load(memory_path)
    if data.get("consecutive_failures", 0) != 0:
        data["consecutive_failures"] = 0
        _save(data, memory_path)


def consecutive_failures(memory_path: Path | None) -> int:
    return _load(memory_path).get("consecutive_failures", 0)


def pending_issues(memory_path: Path | None) -> list[dict]:
    return [i for i in _load(memory_path).get("issues", []) if i.get("status") == "pending_review"]


# ---------------------------------------------------------------------------
# Readable report for Claude / user
# ---------------------------------------------------------------------------

def format_issues_for_review(memory_path: Path | None) -> str:
    issues = pending_issues(memory_path)
    if not issues:
        return "No conversation issues flagged — I've been behaving."

    lines = [f"I have {len(issues)} conversation issue(s) flagged for review:\n"]
    for issue in issues[-5:]:
        lines.append(f"ID: {issue['id']}  [{issue['timestamp']}]")
        lines.append(f"  Pattern: {issue['pattern']}")
        lines.append(f"  I routed to: {issue['action_intended']}")
        lines.append(f"  User said: {issue['user_message']}")
        lines.append(f"  I replied: {issue['sam_response'][:200]}")
        lines.append("  Conversation context:")
        for turn in issue.get("conversation_context", []):
            role = "    User" if turn["role"] == "user" else "    Sam"
            lines.append(f"{role}: {turn['content'][:120]}")
        lines.append("")
    lines.append(
        "To diagnose: read sam_conversation_audit.json and the source files "
        "the patterns point to (brain.py routing, _think prompt, _handle_* methods)."
    )
    return "\n".join(lines)


def last_issue_summary(memory_path: Path | None) -> str | None:
    """Return a one-line summary of the most recent issue, or None."""
    issues = pending_issues(memory_path)
    if not issues:
        return None
    last = issues[-1]
    return (
        f"[{last['timestamp']}] {last['pattern']} "
        f"(intended: {last['action_intended']}) — "
        f"user said: \"{last['user_message'][:80]}\""
    )


# ---------------------------------------------------------------------------
# Main evaluator — called after every handle() turn
# ---------------------------------------------------------------------------

def evaluate_turn(
    user_msg: str,
    sam_response: str,
    action_taken: str,
    history: list[dict],
    memory_path: Path | None,
) -> None:
    """
    Evaluate whether Sam did what the user asked.
    Fast — heuristics only. Logs to audit queue if a problem is found.
    Should be called at the end of every handle() turn.
    """
    try:
        pattern = _quick_check(user_msg, sam_response, action_taken, history)

        if pattern:
            issue_id = log_issue(
                user_msg, sam_response, action_taken, pattern, history, memory_path
            )
            print(f"[AUDIT] Issue flagged — {pattern} (ref: {issue_id})", flush=True)
        else:
            # Good turn on an action route — reset failure streak
            if action_taken in _ACTION_ROUTES:
                reset_consecutive(memory_path)

    except Exception:
        pass  # audit must never crash Sam
