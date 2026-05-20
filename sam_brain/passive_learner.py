"""
Sam's passive learning extractor.

Called silently after every conversation turn.
Extracts facts, preferences, names, and context from what the user says
and saves them to memory — without the user having to say "remember this".

The goal: the more you talk to Sam, the more he knows about you.
He absorbs your name, your style, your projects, your preferences,
your workflows — all automatically, just from normal conversation.

Saved to sam_brain_memory.json as facts (same as remember_fact),
so they're available in every future session.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Trigger check — fast, no LLM
# ---------------------------------------------------------------------------

# Words that signal the user is sharing personal/learnable information
_PERSONAL_SIGNALS = {
    "my name", "i'm called", "call me", "i go by",
    "i prefer", "i like", "i always", "i usually", "i tend to",
    "i work", "i build", "i develop", "i use", "i deploy",
    "my style", "my preference", "my project", "my app", "my company",
    "my team", "my stack", "i'm a ", "i am a ", "i work at",
    "remember that i", "you should know", "important:", "note:",
    "my key", "my token", "my api", "my firebase",
    "i hate", "i don't like", "never do", "always do",
}

_STOP_SIGNALS = {
    "can you", "could you", "please", "do you", "what is",
    "how do", "find ", "open ", "run ", "fix ", "build ",
}


def _has_learnable_content(message: str) -> bool:
    """
    Fast check: does this message contain something worth learning?
    True when personal signals are present and it's not just a command.
    """
    lower = message.lower()

    # Must have a personal signal
    has_signal = any(sig in lower for sig in _PERSONAL_SIGNALS)
    if not has_signal:
        return False

    # Skip if it's clearly a command
    is_command = any(lower.strip().startswith(stop) for stop in _STOP_SIGNALS)
    if is_command and len(message) < 60:
        return False

    return True


# ---------------------------------------------------------------------------
# Fast regex extractors — no LLM needed for these
# ---------------------------------------------------------------------------

def _extract_name(message: str) -> str | None:
    """Extract a name from "my name is X", "I'm X", "call me X"."""
    patterns = [
        r"my name is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"call me ([A-Z][a-z]+)",
        r"i'm called ([A-Z][a-z]+)",
        r"i go by ([A-Z][a-z]+)",
        r"i'm ([A-Z][a-z]+)(?:\s*,|\s+and|\s*$)",
    ]
    for pat in patterns:
        m = re.search(pat, message, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            # Filter out common false positives
            if name.lower() not in {"a", "an", "the", "not", "just", "also", "trying"}:
                return name
    return None


def _extract_role(message: str) -> str | None:
    """Extract role/background: 'I'm a Flutter developer', 'I work at...'"""
    patterns = [
        r"i'?m\s+a\s+([\w\s]+ developer|[\w\s]+ engineer|[\w\s]+ designer|[\w\s]+ founder|[\w\s]+ freelancer)",
        r"i\s+work\s+(?:at|for|with)\s+([A-Z][\w\s]+?)(?:\.|,|$)",
        r"i\s+build\s+([\w\s]+(?:apps?|software|websites?|tools?))",
    ]
    for pat in patterns:
        m = re.search(pat, message, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _extract_preferences(message: str) -> list[str]:
    """Extract preference statements."""
    prefs = []
    patterns = [
        r"i\s+prefer\s+(.{10,80}?)(?:\.|,|$)",
        r"i\s+always\s+(.{10,80}?)(?:\.|,|$)",
        r"i\s+usually\s+(.{10,80}?)(?:\.|,|$)",
        r"i\s+(?:really\s+)?(?:like|love|hate|dislike)\s+(.{10,80}?)(?:\.|,|$)",
        r"(?:never|always)\s+(.{10,80}?)(?:\.|,|$)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, message, re.IGNORECASE):
            pref = m.group(1).strip().rstrip(".,;")
            if 8 < len(pref) < 120:
                prefs.append(pref)
    return prefs


# ---------------------------------------------------------------------------
# LLM extractor — for richer/ambiguous content
# ---------------------------------------------------------------------------

_LLM_EXTRACT_PROMPT = """\
A user said this to their AI assistant Sam. Extract any facts worth remembering about the user.

User said: "{message}"

Return a JSON array of short fact strings (max 20 words each). Only facts about the USER — their name, preferences, role, skills, style, tools they use, projects they work on, things they like or dislike.

Return an empty array [] if there's nothing learnable.

Rules:
- Facts must be concrete and reusable in future conversations
- Do NOT include commands, questions, or task descriptions
- Format: ["User is a Flutter developer", "User prefers dark mode", ...]
- Output ONLY the JSON array. Nothing else."""


def _extract_with_llm(message: str, llm_client: Any) -> list[str]:
    """Use LLM to extract facts from longer/ambiguous messages."""
    try:
        import json
        from urllib import request as _req

        prompt = _LLM_EXTRACT_PROMPT.format(message=message[:400])
        payload = json.dumps({
            "model": llm_client.resolve_model(),
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = _req.Request(
            f"{llm_client.settings.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _req.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            raw = str(body.get("response", "")).strip()

        # Parse the JSON array
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start != -1 and end > start:
            facts = json.loads(raw[start:end])
            if isinstance(facts, list):
                return [f for f in facts if isinstance(f, str) and 5 < len(f) < 200]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def save_learnings(
    user_msg: str,
    memory_path: Path | None,
    *,
    llm_client: Any = None,
) -> list[str]:
    """
    Extract learnable facts from the user's message and save them.
    Returns the list of facts saved (empty if nothing learnable found).
    Called silently after every handle() turn.
    """
    if not memory_path or not user_msg.strip():
        return []

    if not _has_learnable_content(user_msg):
        return []

    from sam_brain.memory import remember_fact

    saved: list[str] = []

    # ── Fast extractors ────────────────────────────────────────────────────────
    name = _extract_name(user_msg)
    if name:
        fact = f"User's name is {name}"
        remember_fact(fact, memory_path)
        saved.append(fact)

    role = _extract_role(user_msg)
    if role:
        fact = f"User is a {role}"
        remember_fact(fact, memory_path)
        saved.append(fact)

    for pref in _extract_preferences(user_msg):
        fact = f"User preference: {pref}"
        remember_fact(fact, memory_path)
        saved.append(fact)

    # ── LLM extractor for richer content ──────────────────────────────────────
    if llm_client and len(user_msg) > 40 and not saved:
        # Only call LLM if fast extractors found nothing — avoid double-saving
        llm_facts = _extract_with_llm(user_msg, llm_client)
        for fact in llm_facts[:4]:  # cap at 4 facts per turn
            remember_fact(fact, memory_path)
            saved.append(fact)

    if saved:
        print(f"[LEARN] Saved {len(saved)} fact(s) from this turn.", flush=True)

    # ── Behavioral correction detector ────────────────────────────────────────
    if llm_client:
        correction = _extract_correction(user_msg, llm_client)
        if correction:
            from sam_brain.memory import save_behavior_note
            save_behavior_note(correction, memory_path)
            print(f"[LEARN] Saved behavioral note: {correction}", flush=True)
            saved.append(correction)

    return saved


# ---------------------------------------------------------------------------
# Correction/teaching extractor — captures how the user wants Sam to behave
# ---------------------------------------------------------------------------

_CORRECTION_EXTRACT_PROMPT = """\
A user is talking to their AI assistant Sam. Decide if this message is teaching Sam HOW to do something — a correction, instruction, or preference about Sam's behavior.

User said: "{message}"

If yes, extract the rule as a short, concrete statement Sam should follow (max 30 words).
Focus on: how to do a task, which tool to use, what NOT to do, approach preferences.

Examples of corrections worth saving:
- "don't use space, use playpause" → "Use pyautogui.press('playpause') for media control, not space"
- "use playwright not pyautogui for browser tasks" → "Use playwright for browser interaction, not pyautogui"
- "never open a search page when playing music, open a direct URL" → "Open a direct playable URL for music, never a search results page"

If the message is NOT a correction (it's a task request, greeting, question about code, etc.), output null.

Output ONLY JSON: {{"rule": "..."}} or null"""


def _extract_correction(message: str, llm_client: Any) -> str | None:
    """Use LLM to detect if the user is correcting Sam's behavior and extract the rule."""
    if len(message.strip()) < 15:
        return None
    try:
        import json
        from urllib import request as _req

        prompt = _CORRECTION_EXTRACT_PROMPT.format(message=message[:400])
        payload = json.dumps({
            "model": llm_client.resolve_model(),
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = _req.Request(
            f"{llm_client.settings.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _req.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        raw = str(body.get("response", "")).strip()
        if raw.lower() == "null" or not raw or raw.lower().startswith("null"):
            return None
        m = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            rule = str(data.get("rule", "")).strip()
            if rule and len(rule) > 10:
                return rule
    except Exception:
        pass
    return None
