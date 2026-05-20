"""
Sam's memory layer.

Design principles:
  - Projects deduplicated by PATH (not name) — same folder = same project
  - Facts deduplicated by keyword overlap — similar facts update, not duplicate
  - Everything sorted by recency — most relevant rises to the top
  - Recall returns structured, prioritized data — not a raw dump
  - Vault stores credential PATHS only, never contents

Storage: sam_brain_memory.json alongside memory.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Internal load / save
# ---------------------------------------------------------------------------

def _brain_memory_path(memory_path: Path | None) -> Path | None:
    if memory_path is None:
        return None
    return Path(memory_path).parent / "sam_brain_memory.json"


def _load(memory_path: Path | None) -> dict:
    path = _brain_memory_path(memory_path)
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict, memory_path: Path | None) -> None:
    path = _brain_memory_path(memory_path)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Projects — deduplicated by path, sorted by last_used
# ---------------------------------------------------------------------------

def remember_project(name: str, path: str, project_type: str, memory_path: Path | None) -> None:
    """
    Save or update a project.
    Deduplication: same path = same project (update name/type, bump last_used).
    """
    if not name or not path:
        return
    data = _load(memory_path)
    projects: dict = data.setdefault("projects", {})

    path_norm = str(path).rstrip("\\/").lower()

    # Find by path first (path is the stable identity)
    existing_key = None
    for key, proj in projects.items():
        if str(proj.get("path", "")).rstrip("\\/").lower() == path_norm:
            existing_key = key
            break

    key = existing_key or name.lower()
    existing = projects.get(key, {})

    projects[key] = {
        "name": name,
        "path": path,
        "type": project_type or existing.get("type", ""),
        "saved_at": existing.get("saved_at") or _now(),
        "last_used": _now(),
    }

    # Remove any duplicate entry that was previously stored under a different key
    if existing_key and existing_key != name.lower():
        # Keep using existing_key — don't create a second entry
        pass

    _save(data, memory_path)


def recall_project(name: str, memory_path: Path | None) -> dict | None:
    """Look up a project by name (fuzzy — substring match)."""
    data = _load(memory_path)
    name_lower = name.lower()
    projects = data.get("projects", {})

    # Exact key match first
    if name_lower in projects:
        return projects[name_lower]

    # Substring match
    for proj in projects.values():
        if name_lower in proj.get("name", "").lower() or name_lower in proj.get("path", "").lower():
            return proj
    return None


def list_projects(memory_path: Path | None) -> list[dict]:
    """Return all projects sorted by most recently used."""
    data = _load(memory_path)
    projects = list(data.get("projects", {}).values())
    projects.sort(
        key=lambda p: p.get("last_used") or p.get("saved_at", ""),
        reverse=True,
    )
    return projects


def touch_project(path: str, memory_path: Path | None) -> None:
    """Update last_used for a project when Sam accesses it."""
    data = _load(memory_path)
    path_norm = str(path).rstrip("\\/").lower()
    changed = False
    for proj in data.get("projects", {}).values():
        if str(proj.get("path", "")).rstrip("\\/").lower() == path_norm:
            proj["last_used"] = _now()
            changed = True
            break
    if changed:
        _save(data, memory_path)


# ---------------------------------------------------------------------------
# Facts — fuzzy dedup, recency sorted, capped at 60
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "my", "i", "me", "we", "you", "he", "she", "it", "they",
    "at", "in", "of", "to", "for", "on", "by", "with", "and", "or",
    "that", "this", "have", "has", "had", "do", "did", "will",
}

_MAX_FACTS = 60


def _keyword_overlap(a: str, b: str) -> float:
    """Fraction of keyword overlap between two strings (0–1)."""
    words_a = set(a.lower().split()) - _STOP_WORDS
    words_b = set(b.lower().split()) - _STOP_WORDS
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / max(len(words_a), len(words_b))


def remember_fact(fact: str, memory_path: Path | None) -> None:
    """
    Save a fact.
    - Exact duplicate → silently skip
    - High keyword overlap (>60%) → update existing entry instead of adding
    - New → append; trim list to MAX_FACTS (oldest removed)
    """
    fact = fact.strip()
    if not fact:
        return

    data = _load(memory_path)
    facts: list[dict] = data.setdefault("facts", [])

    fact_lower = fact.lower()

    for entry in facts:
        existing = entry.get("fact", "")
        # Exact match
        if existing.lower() == fact_lower:
            return
        # Semantically similar — update in place
        if _keyword_overlap(fact, existing) > 0.6:
            entry["fact"] = fact
            entry["updated"] = _now()
            _save(data, memory_path)
            return

    facts.append({"fact": fact, "date": _now()})

    # Keep only most recent MAX_FACTS
    if len(facts) > _MAX_FACTS:
        data["facts"] = facts[-_MAX_FACTS:]

    _save(data, memory_path)


def recall_facts(memory_path: Path | None, limit: int = 20) -> list[str]:
    """Return facts sorted by recency (most recent first), up to limit."""
    data = _load(memory_path)
    facts = data.get("facts", [])
    facts_sorted = sorted(
        facts,
        key=lambda f: f.get("updated") or f.get("date", ""),
        reverse=True,
    )
    return [f["fact"] for f in facts_sorted[:limit]]


def recall_relevant_facts(query: str, memory_path: Path | None, limit: int = 8) -> list[str]:
    """Return facts most relevant to the query, ranked by keyword overlap then recency."""
    data = _load(memory_path)
    facts = data.get("facts", [])

    scored = []
    for entry in facts:
        text = entry.get("fact", "")
        score = _keyword_overlap(query, text)
        recency = entry.get("updated") or entry.get("date", "")
        scored.append((score, recency, text))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [text for _, _, text in scored[:limit]]


# ---------------------------------------------------------------------------
# Sensitive key vault — stores paths, never contents
# ---------------------------------------------------------------------------

def store_key(key_name: str, key_path: str, memory_path: Path | None) -> None:
    data = _load(memory_path)
    vault = data.setdefault("vault", {})
    vault[key_name] = {
        "path": key_path,
        "stored_at": _now(),
    }
    _save(data, memory_path)


def recall_key(key_name: str, memory_path: Path | None) -> str | None:
    data = _load(memory_path)
    entry = data.get("vault", {}).get(key_name)
    if entry and isinstance(entry, dict):
        return entry.get("path")
    return None


def list_keys(memory_path: Path | None) -> list[str]:
    data = _load(memory_path)
    return list(data.get("vault", {}).keys())


def recall_all_keys(memory_path: Path | None) -> dict[str, str]:
    data = _load(memory_path)
    return {
        name: entry["path"]
        for name, entry in data.get("vault", {}).items()
        if isinstance(entry, dict) and entry.get("path")
    }


# ---------------------------------------------------------------------------
# App credentials — per-app, per-role login storage
# Format: {"app_credentials": {"focusflow": {"admin": {"email": ..., "password": ...}}}}
# ---------------------------------------------------------------------------

def save_app_credential(
    app_name: str,
    role: str,
    email: str,
    password: str,
    memory_path: Path | None,
) -> None:
    """
    Save login credentials for an app + role pair.
    Role is normalised to lowercase (admin, resident, user, etc.)
    """
    if not app_name or not email:
        return
    data = _load(memory_path)
    creds = data.setdefault("app_credentials", {})
    app_key = app_name.lower().strip()
    role_key = (role or "default").lower().strip()
    creds.setdefault(app_key, {})[role_key] = {
        "email": email,
        "password": password,
        "saved_at": _now(),
    }
    _save(data, memory_path)


def get_app_credential(
    app_name: str,
    memory_path: Path | None,
    *,
    role: str = "",
) -> dict | None:
    """
    Retrieve credentials for an app.
    If role is given, return that role's creds.
    If not, return the first saved creds for the app (usually admin/default).
    Returns {"email": ..., "password": ..., "role": ...} or None.
    """
    data = _load(memory_path)
    app_key = app_name.lower().strip()
    app_creds = data.get("app_credentials", {}).get(app_key)
    if not app_creds:
        # Fuzzy match on app name
        for key, val in data.get("app_credentials", {}).items():
            if app_key in key or key in app_key:
                app_creds = val
                break
    if not app_creds:
        return None

    role_key = role.lower().strip() if role else ""
    if role_key and role_key in app_creds:
        entry = app_creds[role_key]
        return {**entry, "role": role_key}

    # Return first available credential
    first_role = next(iter(app_creds))
    entry = app_creds[first_role]
    return {**entry, "role": first_role}


def list_app_credentials(app_name: str, memory_path: Path | None) -> list[dict]:
    """List all saved roles for an app."""
    data = _load(memory_path)
    app_key = app_name.lower().strip()
    app_creds = data.get("app_credentials", {}).get(app_key, {})
    return [
        {"role": role, "email": entry.get("email", ""), "saved_at": entry.get("saved_at", "")}
        for role, entry in app_creds.items()
    ]


def list_all_app_credentials(memory_path: Path | None) -> dict:
    """Return all saved app credentials (emails only, no passwords for display)."""
    data = _load(memory_path)
    result = {}
    for app, roles in data.get("app_credentials", {}).items():
        result[app] = {role: entry.get("email", "") for role, entry in roles.items()}
    return result


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def set_preference(key: str, value: Any, memory_path: Path | None) -> None:
    data = _load(memory_path)
    data.setdefault("preferences", {})[key] = value
    _save(data, memory_path)


def get_preference(key: str, memory_path: Path | None, default: Any = None) -> Any:
    data = _load(memory_path)
    return data.get("preferences", {}).get(key, default)


# ---------------------------------------------------------------------------
# Task history
# ---------------------------------------------------------------------------

def log_task(task: str, project: str, result: str, memory_path: Path | None) -> None:
    data = _load(memory_path)
    history: list = data.setdefault("task_history", [])
    history.append({
        "date": _now(),
        "task": task,
        "project": project,
        "result": result[:300],
    })
    if len(history) > 50:
        data["task_history"] = history[-50:]
    _save(data, memory_path)


def recent_tasks(memory_path: Path | None, n: int = 5) -> list[dict]:
    data = _load(memory_path)
    return data.get("task_history", [])[-n:]


# ---------------------------------------------------------------------------
# Clean context snapshot — structured, prioritized, concise
# ---------------------------------------------------------------------------

def build_context_block(memory_path: Path | None, query: str = "") -> dict:
    """
    Build a clean context dict for injecting into LLM prompts.
    Structured and prioritized — not a raw dump.

    Returns:
      {
        "projects": [{"name": ..., "path": ..., "type": ...}, ...],  # up to 8, most recent first
        "facts": [...],   # up to 10, relevant to query if given
        "keys": [...],    # names only, never paths
        "recent_work": [{"date": ..., "project": ..., "task": ...}],  # last 3
      }
    """
    projects = list_projects(memory_path)[:8]
    facts = (
        recall_relevant_facts(query, memory_path, limit=10)
        if query
        else recall_facts(memory_path, limit=10)
    )
    keys = list_keys(memory_path)
    tasks = recent_tasks(memory_path, n=3)

    return {
        "projects": [
            {"name": p["name"], "path": p["path"], "type": p.get("type", "")}
            for p in projects
        ],
        "facts": facts,
        "keys": keys,
        "recent_work": [
            {"date": t["date"], "project": t["project"], "task": t["task"]}
            for t in tasks
        ],
    }


def session_context_summary(memory_path: Path | None) -> str:
    """Plain-text summary of what Sam knows — for diagnostics."""
    ctx = build_context_block(memory_path)
    lines: list[str] = []

    if ctx["projects"]:
        lines.append("Projects Sam knows:")
        for p in ctx["projects"]:
            lines.append(f"  - {p['name']} at {p['path']}")

    if ctx["keys"]:
        lines.append(f"Stored keys: {', '.join(ctx['keys'])}")

    if ctx["facts"]:
        lines.append("Saved facts:")
        for f in ctx["facts"]:
            lines.append(f"  - {f}")

    if ctx["recent_work"]:
        lines.append("Recent work:")
        for t in ctx["recent_work"]:
            lines.append(f"  [{t['date']}] {t['project']}: {t['task']}")

    return "\n".join(lines) if lines else "(nothing saved yet)"


# ---------------------------------------------------------------------------
# Music preferences & playlist
# ---------------------------------------------------------------------------

def get_music_prefs(memory_path: Path | None) -> dict:
    """Return the full music_preferences block. Creates empty structure if missing."""
    data = _load(memory_path)
    return data.get("music_preferences", {"playlist": [], "liked": [], "disliked": []})


def save_music_pref(title: str, skill_slug: str, memory_path: Path | None) -> None:
    """
    Record that a track was played. Increments play_count if already exists.
    Noop if title is empty.
    """
    if not title:
        return
    data = _load(memory_path)
    prefs = data.setdefault("music_preferences", {"playlist": [], "liked": [], "disliked": []})
    playlist: list[dict] = prefs.setdefault("playlist", [])

    for entry in playlist:
        if entry.get("title", "").lower() == title.lower():
            entry["play_count"] = int(entry.get("play_count", 0)) + 1
            entry["last_played"] = _now()
            entry["skill"] = skill_slug
            _save(data, memory_path)
            return

    playlist.append({
        "title": title,
        "skill": skill_slug,
        "play_count": 1,
        "last_played": _now(),
        "liked": False,
    })
    _save(data, memory_path)


def mark_music_liked(title: str, memory_path: Path | None) -> None:
    """
    Mark a track as liked. Updates the playlist entry and the liked list.
    Idempotent — calling twice has no extra effect.
    """
    if not title:
        return
    data = _load(memory_path)
    prefs = data.setdefault("music_preferences", {"playlist": [], "liked": [], "disliked": []})

    for entry in prefs.get("playlist", []):
        if entry.get("title", "").lower() == title.lower():
            entry["liked"] = True
            break

    liked: list[str] = prefs.setdefault("liked", [])
    if title not in liked:
        liked.append(title)

    _save(data, memory_path)


def get_liked_music(memory_path: Path | None) -> list[dict]:
    """Return liked playlist entries sorted by play_count descending."""
    prefs = get_music_prefs(memory_path)
    liked_titles = {t.lower() for t in prefs.get("liked", [])}
    liked = [
        p for p in prefs.get("playlist", [])
        if p.get("title", "").lower() in liked_titles
    ]
    liked.sort(key=lambda p: int(p.get("play_count", 0)), reverse=True)
    return liked
