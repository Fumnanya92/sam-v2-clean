# Sam `execute` Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a direct `execute` action to Sam's brain so he can run one-shot desktop tasks immediately — open Chrome, control media, take screenshots, describe the screen — without asking "which project?".

**Architecture:** A new `sam_brain/executor.py` module handles the full lifecycle: check the skills library first, reuse or duplicate+patch an existing skill if close enough, write a fresh script via LLM if not, run it with subprocess, auto-fix once on failure, save on success. Music plays are tracked in memory so Sam builds a liked-playlist over time. Desktop vision (screenshot → Ollama → pyautogui) gives Sam real eyes on the screen.

**Tech Stack:** Python 3.10+, `mss` (screenshots), `pyautogui` (desktop control), `Pillow` (image encode), `subprocess` (script execution), Ollama `/api/generate` with `images` field (vision), existing `sam_brain/memory.py` pattern for storage.

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `sam_brain/executor.py` | **Create** | All execution helpers: skill index, matching, script run, auto-fix, LLM write, vision |
| `sam_brain/memory.py` | **Modify** | Add music preference functions |
| `sam_brain/brain.py` | **Modify** | Add `execute` to routing, `_handle_execute()`, like-detection |
| `sam_skills/system/screenshot.py` | **Create** | Seed screenshot script (mss capture) |
| `tests/test_executor.py` | **Create** | Unit tests for executor |
| `tests/test_music_memory.py` | **Create** | Unit tests for music prefs |

---

## Task 1: Executor Foundation — index load/save + slug helpers

**Files:**
- Create: `sam_brain/executor.py`
- Create: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_executor.py
from __future__ import annotations
import json
import pytest
from pathlib import Path
from sam_brain.executor import (
    SKILLS_ROOT,
    _slug_from_goal,
    _domain_from_goal,
    _tags_from_goal,
    load_index,
    save_index,
)


def test_slug_from_goal_basic():
    assert _slug_from_goal("open YouTube Music") == "open_youtube_music"


def test_slug_from_goal_strips_special_chars():
    assert _slug_from_goal("play lofi hip-hop!!") == "play_lofi_hip_hop"


def test_slug_from_goal_max_length():
    long = "this is a very long goal that should be truncated at some point"
    assert len(_slug_from_goal(long)) <= 50


def test_domain_from_goal_music():
    assert _domain_from_goal("play youtube music") == "music"
    assert _domain_from_goal("pause the song") == "music"
    assert _domain_from_goal("resume music") == "music"


def test_domain_from_goal_browser():
    assert _domain_from_goal("open chrome") == "browser"
    assert _domain_from_goal("navigate to github.com") == "browser"


def test_domain_from_goal_system():
    assert _domain_from_goal("take a screenshot") == "system"
    assert _domain_from_goal("what can you see") == "system"


def test_domain_from_goal_default():
    assert _domain_from_goal("do something random") == "general"


def test_tags_from_goal():
    tags = _tags_from_goal("play lofi music on youtube")
    assert "music" in tags
    assert "youtube" in tags


def test_load_index_missing(tmp_path):
    root = tmp_path / "sam_skills"
    assert load_index(root) == {}


def test_save_and_load_index(tmp_path):
    root = tmp_path / "sam_skills"
    entry = {
        "slug": "play_yt",
        "description": "Play YouTube Music",
        "domain": "music",
        "tags": ["youtube", "music"],
        "file": "music/play_yt.py",
        "run_count": 0,
        "last_run": "",
        "status": "active",
    }
    save_index({"play_yt": entry}, root)
    loaded = load_index(root)
    assert loaded["play_yt"]["description"] == "Play YouTube Music"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd C:\Users\DELL.COM\Desktop\Darey\sam-v2-clean
python -m pytest tests/test_executor.py -v 2>&1 | head -30
```
Expected: `ModuleNotFoundError: No module named 'sam_brain.executor'`

- [ ] **Step 3: Create `sam_brain/executor.py` with foundation**

```python
"""
Sam's one-shot execution engine.

Lifecycle for every execute request:
  1. check_skills()     — fuzzy-match against sam_skills/index.json
  2a. score >= 0.7      — run existing skill as-is
  2b. score 0.4–0.69   — duplicate + patch + run
  2c. score < 0.4       — llm_write_script() + run
  3. on failure         — auto_fix_once() → retry once
  4. on success         — save_skill() so it exists next time
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

# Root of the skills library — sibling of sam_brain/
SKILLS_ROOT: Path = Path(__file__).parent.parent / "sam_skills"

_MUSIC_KEYWORDS = {"play", "pause", "resume", "music", "song", "audio", "spotify", "youtube", "lofi", "track"}
_BROWSER_KEYWORDS = {"chrome", "browser", "open", "navigate", "url", "website", "tab"}
_SYSTEM_KEYWORDS = {"screenshot", "screen", "capture", "see", "vision", "look", "display"}
_STOP = {"a", "an", "the", "is", "to", "on", "in", "for", "me", "my", "can", "you", "it", "do"}


# ---------------------------------------------------------------------------
# Slug / domain / tag helpers
# ---------------------------------------------------------------------------

def _slug_from_goal(goal: str) -> str:
    """Convert a goal description to a filesystem-safe slug, max 50 chars."""
    slug = re.sub(r"[^a-z0-9]+", "_", goal.lower().strip())
    slug = slug.strip("_")
    return slug[:50]


def _domain_from_goal(goal: str) -> str:
    """Classify a goal into a skill domain folder."""
    words = set(goal.lower().split())
    if words & _MUSIC_KEYWORDS:
        return "music"
    if words & _BROWSER_KEYWORDS:
        return "browser"
    if words & _SYSTEM_KEYWORDS:
        return "system"
    return "general"


def _tags_from_goal(goal: str) -> list[str]:
    """Extract meaningful keywords from a goal as tags."""
    words = re.findall(r"[a-z0-9]+", goal.lower())
    return [w for w in words if w not in _STOP and len(w) > 2][:8]


# ---------------------------------------------------------------------------
# Index — sam_skills/index.json
# ---------------------------------------------------------------------------

def load_index(skills_root: Path | None = None) -> dict:
    """Load the skill registry. Returns {} if missing or corrupt."""
    root = skills_root or SKILLS_ROOT
    path = root / "index.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_index(index: dict, skills_root: Path | None = None) -> None:
    """Persist the skill registry."""
    root = skills_root or SKILLS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
```

- [ ] **Step 4: Run tests — should pass**

```
python -m pytest tests/test_executor.py -v
```
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sam_brain/executor.py tests/test_executor.py
git commit -m "feat: executor foundation — slug helpers and skill index load/save"
```

---

## Task 2: Skill Matching

**Files:**
- Modify: `sam_brain/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Add tests**

```python
# append to tests/test_executor.py
from sam_brain.executor import SkillMatch, check_skills, _match_score


def _make_index(entries: list[dict]) -> dict:
    return {e["slug"]: e for e in entries}


def test_match_score_exact():
    entry = {
        "slug": "play_youtube_music",
        "description": "Play YouTube Music in Chrome",
        "tags": ["play", "youtube", "music", "chrome"],
        "domain": "music",
    }
    score = _match_score("play youtube music", entry)
    assert score >= 0.7


def test_match_score_related():
    entry = {
        "slug": "play_youtube_music",
        "description": "Play YouTube Music in Chrome",
        "tags": ["play", "youtube", "music", "chrome"],
        "domain": "music",
    }
    # "play lofi" is in same domain (music) but different detail
    score = _match_score("play lofi music", entry)
    assert 0.3 <= score < 0.8


def test_match_score_unrelated():
    entry = {
        "slug": "open_chrome",
        "description": "Open Chrome browser",
        "tags": ["chrome", "browser"],
        "domain": "browser",
    }
    score = _match_score("take a screenshot", entry)
    assert score < 0.4


def test_check_skills_exact_match(tmp_path):
    root = tmp_path / "sam_skills"
    skill_file = root / "music" / "play_youtube_music.py"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("print('playing')", encoding="utf-8")

    index = {
        "play_youtube_music": {
            "slug": "play_youtube_music",
            "description": "Play YouTube Music",
            "tags": ["play", "youtube", "music"],
            "domain": "music",
            "file": "music/play_youtube_music.py",
            "run_count": 1,
            "last_run": "2026-05-20",
            "status": "active",
        }
    }
    save_index(index, root)

    match = check_skills("play youtube music", skills_root=root)
    assert match is not None
    assert match.slug == "play_youtube_music"
    assert match.score >= 0.7
    assert match.needs_patch is False


def test_check_skills_no_match(tmp_path):
    root = tmp_path / "sam_skills"
    save_index({}, root)
    match = check_skills("take a screenshot", skills_root=root)
    assert match is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_executor.py::test_match_score_exact -v
```
Expected: `ImportError: cannot import name 'SkillMatch'`

- [ ] **Step 3: Implement matching in `sam_brain/executor.py`**

Add after the index functions:

```python
class SkillMatch(NamedTuple):
    slug: str
    score: float
    file_path: Path
    needs_patch: bool  # True when 0.4 <= score < 0.7 — duplicate + patch


def _keyword_overlap(a: str, b: str) -> float:
    """Fraction of shared keywords between two strings (0–1)."""
    wa = set(re.findall(r"[a-z0-9]+", a.lower())) - _STOP
    wb = set(re.findall(r"[a-z0-9]+", b.lower())) - _STOP
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def _match_score(goal: str, entry: dict) -> float:
    """Score a skill entry against a goal string (0–1)."""
    desc_score = _keyword_overlap(goal, entry.get("description", ""))
    tag_text = " ".join(entry.get("tags", []))
    tag_score = _keyword_overlap(goal, tag_text)
    slug_score = _keyword_overlap(goal, entry.get("slug", "").replace("_", " "))
    return max(desc_score, tag_score, slug_score)


def check_skills(goal: str, skills_root: Path | None = None) -> SkillMatch | None:
    """
    Find the best matching skill for a goal.
    Returns SkillMatch or None if nothing scores >= 0.4.
    """
    root = skills_root or SKILLS_ROOT
    index = load_index(root)
    if not index:
        return None

    best_slug = ""
    best_score = 0.0
    for slug, entry in index.items():
        if entry.get("status") != "active":
            continue
        score = _match_score(goal, entry)
        if score > best_score:
            best_score = score
            best_slug = slug

    if best_score < 0.4 or not best_slug:
        return None

    entry = index[best_slug]
    file_path = root / entry["file"]
    needs_patch = best_score < 0.7
    return SkillMatch(
        slug=best_slug,
        score=best_score,
        file_path=file_path,
        needs_patch=needs_patch,
    )
```

- [ ] **Step 4: Run tests — should all pass**

```
python -m pytest tests/test_executor.py -v
```
Expected: all 15 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sam_brain/executor.py tests/test_executor.py
git commit -m "feat: executor skill matching — SkillMatch, _match_score, check_skills"
```

---

## Task 3: Script Running + Auto-Fix

**Files:**
- Modify: `sam_brain/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Add tests**

```python
# append to tests/test_executor.py
from sam_brain.executor import _run_script, auto_fix_once, run_skill_script, save_skill


def test_run_script_success():
    code = "print('hello')"
    ok, out, err = _run_script(code, timeout=10)
    assert ok is True
    assert "hello" in out


def test_run_script_failure():
    code = "raise RuntimeError('boom')"
    ok, out, err = _run_script(code, timeout=10)
    assert ok is False
    assert "boom" in err


def test_auto_fix_missing_module():
    error = "ModuleNotFoundError: No module named 'nonexistent_pkg_xyz'"
    script = "import nonexistent_pkg_xyz"
    result = auto_fix_once(script, error)
    # Should detect the missing module name
    assert result is not None
    assert result["action"] == "pip_install"
    assert result["package"] == "nonexistent_pkg_xyz"


def test_auto_fix_chrome_path():
    error = "FileNotFoundError: chrome not found"
    script = 'subprocess.run(["chrome", "https://example.com"])'
    result = auto_fix_once(script, error)
    assert result is not None
    assert result["action"] == "patch_script"
    assert "Program Files" in result["patched_script"]


def test_auto_fix_unknown_error():
    result = auto_fix_once("x = 1", "SomeRandomError: unknown")
    assert result is None


def test_run_skill_script_success(tmp_path):
    script_path = tmp_path / "test_skill.py"
    script_path.write_text("print('skill ran')", encoding="utf-8")
    ok, output = run_skill_script(script_path)
    assert ok is True
    assert "skill ran" in output


def test_save_skill_creates_file(tmp_path):
    root = tmp_path / "sam_skills"
    path = save_skill(
        slug="play_yt",
        description="Play YouTube Music",
        domain="music",
        tags=["youtube", "music"],
        script_code="import subprocess\nsubprocess.run(['cmd','/c','start','chrome','https://music.youtube.com'])",
        skills_root=root,
    )
    assert path.exists()
    assert (root / "index.json").exists()
    index = load_index(root)
    assert "play_yt" in index
    assert index["play_yt"]["run_count"] == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_executor.py::test_run_script_success -v
```
Expected: `ImportError: cannot import name '_run_script'`

- [ ] **Step 3: Implement in `sam_brain/executor.py`**

Add after the matching section:

```python
# ---------------------------------------------------------------------------
# Script execution
# ---------------------------------------------------------------------------

def _run_script(code: str, timeout: int = 30) -> tuple[bool, str, str]:
    """
    Write code to a temp file and run it.
    Returns (success, stdout, stderr).
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp = Path(f.name)
    try:
        result = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Script timed out after {timeout}s"
    except Exception as exc:
        return False, "", str(exc)
    finally:
        tmp.unlink(missing_ok=True)


_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\{user}\AppData\Local\Google\Chrome\Application\chrome.exe".format(
        user=Path.home().name
    ),
]


def auto_fix_once(script: str, error_output: str) -> dict | None:
    """
    Analyse a failure and return a fix dict, or None if unfixable.

    Return shapes:
      {"action": "pip_install", "package": "mss"}
      {"action": "patch_script", "patched_script": "..."}
    """
    # Missing Python module
    m = re.search(r"No module named '([^']+)'", error_output)
    if m:
        pkg = m.group(1).split(".")[0]  # top-level package only
        return {"action": "pip_install", "package": pkg}

    # Chrome not found — patch with first existing path
    if "chrome" in error_output.lower() and (
        "not found" in error_output.lower() or "cannot find" in error_output.lower()
        or "FileNotFoundError" in error_output
    ):
        for chrome_path in _CHROME_PATHS:
            if Path(chrome_path).exists():
                patched = re.sub(
                    r'["\'](chrome|google-chrome)["\']',
                    f'"{chrome_path}"',
                    script,
                )
                return {"action": "patch_script", "patched_script": patched}
        # Fallback: use cmd /c start
        patched = re.sub(
            r'subprocess\.run\(\["chrome"',
            r'subprocess.run(["cmd", "/c", "start", "chrome"',
            script,
        )
        if patched != script:
            return {"action": "patch_script", "patched_script": patched}

    return None


def run_skill_script(skill_file: Path, timeout: int = 30) -> tuple[bool, str]:
    """Read and run a saved skill script. Returns (success, combined_output)."""
    try:
        code = skill_file.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"Could not read skill file: {exc}"
    ok, stdout, stderr = _run_script(code, timeout=timeout)
    combined = (stdout + stderr).strip()
    return ok, combined


# ---------------------------------------------------------------------------
# Save skill
# ---------------------------------------------------------------------------

def save_skill(
    slug: str,
    description: str,
    domain: str,
    tags: list[str],
    script_code: str,
    skills_root: Path | None = None,
) -> Path:
    """
    Write script_code to sam_skills/<domain>/<slug>.py and register in index.json.
    Returns the path to the saved script.
    """
    root = skills_root or SKILLS_ROOT
    skill_dir = root / domain
    skill_dir.mkdir(parents=True, exist_ok=True)

    script_path = skill_dir / f"{slug}.py"
    script_path.write_text(script_code, encoding="utf-8")

    index = load_index(root)
    relative_file = f"{domain}/{slug}.py"

    if slug in index:
        # Update existing entry — preserve run_count
        index[slug].update({
            "description": description,
            "tags": tags,
            "file": relative_file,
            "status": "active",
        })
    else:
        index[slug] = {
            "slug": slug,
            "description": description,
            "domain": domain,
            "tags": tags,
            "file": relative_file,
            "run_count": 0,
            "last_run": "",
            "status": "active",
        }

    save_index(index, root)
    return script_path
```

- [ ] **Step 4: Run tests — all pass**

```
python -m pytest tests/test_executor.py -v
```
Expected: all 22 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sam_brain/executor.py tests/test_executor.py
git commit -m "feat: executor script runner — _run_script, auto_fix_once, run_skill_script, save_skill"
```

---

## Task 4: LLM Script Writer + Patch Skill

**Files:**
- Modify: `sam_brain/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Add tests**

```python
# append to tests/test_executor.py
from unittest.mock import MagicMock
from sam_brain.executor import llm_write_script, patch_skill, run_and_learn


def _mock_llm(response_text: str):
    llm = MagicMock()
    llm.settings.base_url = "http://localhost:11434"
    llm.resolve_model.return_value = "test-model"
    return llm, response_text


def test_llm_write_script_returns_code(monkeypatch, tmp_path):
    """llm_write_script returns a non-empty string."""
    import sam_brain.executor as ex

    def fake_call(url, payload):
        return {"response": "```python\nprint('hello')\n```"}

    monkeypatch.setattr(ex, "_ollama_generate", fake_call)
    llm = MagicMock()
    llm.settings.base_url = "http://localhost:11434"
    llm.resolve_model.return_value = "llama3"
    result = llm_write_script("say hello", llm)
    assert "print" in result


def test_patch_skill_swaps_url(monkeypatch, tmp_path):
    """patch_skill asks LLM to adapt the script for a new goal."""
    import sam_brain.executor as ex

    base_code = "subprocess.run(['cmd','/c','start','chrome','https://music.youtube.com'])"

    def fake_call(url, payload):
        # Simulate LLM returning patched script with lofi URL
        return {"response": "```python\nsubprocess.run(['cmd','/c','start','chrome','https://music.youtube.com/playlist?list=lofi'])\n```"}

    monkeypatch.setattr(ex, "_ollama_generate", fake_call)
    llm = MagicMock()
    llm.settings.base_url = "http://localhost:11434"
    llm.resolve_model.return_value = "llama3"

    patched = patch_skill(base_code, "play lofi playlist on youtube music", llm)
    assert "lofi" in patched or "playlist" in patched


def test_run_and_learn_saves_on_success(monkeypatch, tmp_path):
    """run_and_learn saves the skill when execution succeeds."""
    import sam_brain.executor as ex

    monkeypatch.setattr(ex, "SKILLS_ROOT", tmp_path / "sam_skills")
    monkeypatch.setattr(ex, "_run_script", lambda code, timeout=30: (True, "done", ""))

    llm = MagicMock()
    ok, output = run_and_learn("print('done')", "open browser", llm)
    assert ok is True
    index = ex.load_index(tmp_path / "sam_skills")
    assert len(index) == 1


def test_run_and_learn_auto_fixes_pip(monkeypatch, tmp_path):
    """run_and_learn auto-installs a missing module and retries."""
    import sam_brain.executor as ex

    monkeypatch.setattr(ex, "SKILLS_ROOT", tmp_path / "sam_skills")
    calls = []

    def fake_run(code, timeout=30):
        calls.append(code)
        if len(calls) == 1:
            return False, "", "ModuleNotFoundError: No module named 'mss'"
        return True, "captured", ""

    monkeypatch.setattr(ex, "_run_script", fake_run)

    pip_calls = []
    def fake_pip(pkg):
        pip_calls.append(pkg)

    monkeypatch.setattr(ex, "_pip_install", fake_pip)

    llm = MagicMock()
    ok, output = run_and_learn("import mss\nprint('hi')", "screenshot", llm)
    assert ok is True
    assert "mss" in pip_calls
    assert len(calls) == 2  # ran twice
```

- [ ] **Step 2: Run to confirm failure**

```
python -m pytest tests/test_executor.py::test_llm_write_script_returns_code -v
```
Expected: `ImportError: cannot import name 'llm_write_script'`

- [ ] **Step 3: Implement in `sam_brain/executor.py`**

```python
# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _ollama_generate(url: str, payload: dict) -> dict:
    """Single Ollama /api/generate call. Extracted for monkeypatching in tests."""
    import json as _json
    from urllib import request as _req
    data = _json.dumps(payload).encode()
    req = _req.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _req.urlopen(req, timeout=30) as resp:
        return _json.loads(resp.read())


def _extract_python(text: str) -> str:
    """Pull Python code out of an LLM response that may include markdown fences."""
    m = re.search(r"```python\s*([\s\S]+?)```", text)
    if m:
        return m.group(1).strip()
    # If no fence, return as-is if it looks like code
    text = text.strip()
    if text.startswith(("import", "from", "subprocess", "#", "import")):
        return text
    return text


def llm_write_script(goal: str, llm_client) -> str:
    """
    Ask Ollama to write a standalone Python script that accomplishes `goal`.
    Returns the Python source as a string.
    """
    prompt = (
        f"Write a standalone Python 3 script for Windows that does this:\n\n{goal}\n\n"
        "Rules:\n"
        "- Must be fully self-contained (handle its own imports)\n"
        "- Must work on Windows 10\n"
        "- Use subprocess to launch Chrome: subprocess.run(['cmd', '/c', 'start', 'chrome', 'URL'])\n"
        "- Use pyautogui for mouse/keyboard control if needed\n"
        "- Use mss for screenshots if needed\n"
        "- Print a short confirmation message when done\n"
        "- Handle errors with try/except and print what went wrong\n"
        "- No user input — run silently and complete\n\n"
        "Output ONLY the Python script, no explanation."
    )
    try:
        body = _ollama_generate(
            f"{llm_client.settings.base_url}/api/generate",
            {"model": llm_client.resolve_model(), "prompt": prompt, "stream": False},
        )
        return _extract_python(str(body.get("response", "")).strip())
    except Exception:
        return ""


def patch_skill(base_script: str, new_goal: str, llm_client) -> str:
    """
    Ask Ollama to adapt base_script for new_goal.
    Minimal change — swap URL, song title, etc.
    Returns the patched Python source.
    """
    prompt = (
        f"Here is an existing Python script:\n\n```python\n{base_script}\n```\n\n"
        f"Adapt it minimally to accomplish this instead:\n{new_goal}\n\n"
        "Change only what is necessary (e.g. URL, song title, search term). "
        "Output ONLY the adapted Python script, no explanation."
    )
    try:
        body = _ollama_generate(
            f"{llm_client.settings.base_url}/api/generate",
            {"model": llm_client.resolve_model(), "prompt": prompt, "stream": False},
        )
        patched = _extract_python(str(body.get("response", "")).strip())
        return patched if patched else base_script
    except Exception:
        return base_script


def _pip_install(package: str) -> None:
    """Install a Python package. Extracted for monkeypatching in tests."""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", package, "--quiet"],
        capture_output=True,
        timeout=120,
    )


def increment_run_count(slug: str, skills_root: Path | None = None) -> None:
    """Bump run_count and last_run for a skill after successful execution."""
    import time
    root = skills_root or SKILLS_ROOT
    index = load_index(root)
    if slug in index:
        index[slug]["run_count"] = int(index[slug].get("run_count", 0)) + 1
        index[slug]["last_run"] = time.strftime("%Y-%m-%d")
        save_index(index, root)


def run_and_learn(
    script: str,
    goal: str,
    llm_client,
    skills_root: Path | None = None,
) -> tuple[bool, str]:
    """
    Run a script. Auto-fix once on failure. Save as skill on success.
    Returns (success, output_message).
    """
    ok, stdout, stderr = _run_script(script)
    combined = (stdout + stderr).strip()

    if not ok:
        fix = auto_fix_once(script, stderr + stdout)
        if fix:
            if fix["action"] == "pip_install":
                _pip_install(fix["package"])
                ok, stdout, stderr = _run_script(script)
                combined = (stdout + stderr).strip()
            elif fix["action"] == "patch_script":
                patched = fix["patched_script"]
                ok, stdout, stderr = _run_script(patched)
                combined = (stdout + stderr).strip()
                if ok:
                    script = patched  # save the working version

    if ok:
        slug = _slug_from_goal(goal)
        domain = _domain_from_goal(goal)
        tags = _tags_from_goal(goal)
        save_skill(slug, goal, domain, tags, script, skills_root)

    return ok, combined
```

- [ ] **Step 4: Run all tests**

```
python -m pytest tests/test_executor.py -v
```
Expected: all 26 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sam_brain/executor.py tests/test_executor.py
git commit -m "feat: executor LLM script writer — llm_write_script, patch_skill, run_and_learn"
```

---

## Task 5: Desktop Vision — Screenshot + Ollama + pyautogui Act

**Files:**
- Modify: `sam_brain/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Add tests**

```python
# append to tests/test_executor.py
from sam_brain.executor import _describe_with_ollama, desktop_act


def test_describe_with_ollama_no_vision(monkeypatch, tmp_path):
    """Falls back to local summary when Ollama returns no image support."""
    import sam_brain.executor as ex

    def fake_call(url, payload):
        return {"response": "I cannot process images", "error": ""}

    monkeypatch.setattr(ex, "_ollama_generate", fake_call)

    # Create a tiny valid PNG via Pillow
    from PIL import Image
    img_path = tmp_path / "screen.png"
    Image.new("RGB", (100, 100), color=(128, 128, 128)).save(img_path)

    llm = MagicMock()
    llm.settings.base_url = "http://localhost:11434"
    llm.resolve_model.return_value = "llama3"

    result = _describe_with_ollama(img_path, llm)
    # Should still return something — local fallback
    assert isinstance(result, str)
    assert len(result) > 0


def test_describe_with_ollama_vision_success(monkeypatch, tmp_path):
    """Returns Ollama's description when vision works."""
    import sam_brain.executor as ex

    def fake_call(url, payload):
        return {"response": "I can see a Chrome browser showing YouTube Music."}

    monkeypatch.setattr(ex, "_ollama_generate", fake_call)

    from PIL import Image
    img_path = tmp_path / "screen.png"
    Image.new("RGB", (640, 480), color=(30, 30, 30)).save(img_path)

    llm = MagicMock()
    llm.settings.base_url = "http://localhost:11434"
    llm.resolve_model.return_value = "llava"

    result = _describe_with_ollama(img_path, llm)
    assert "Chrome" in result or "YouTube" in result


def test_desktop_act_returns_result(monkeypatch, tmp_path):
    """desktop_act returns a (success, message) tuple."""
    import sam_brain.executor as ex

    # Mock screenshot
    from PIL import Image
    img_path = tmp_path / "screen.png"
    Image.new("RGB", (1920, 1080), color=(0, 0, 0)).save(img_path)
    monkeypatch.setattr(ex, "_capture_screen", lambda out: img_path)

    # Mock Ollama returning click coordinates
    def fake_call(url, payload):
        return {"response": '{"action": "click", "x": 960, "y": 540}'}

    monkeypatch.setattr(ex, "_ollama_generate", fake_call)

    # Mock pyautogui
    clicks = []
    monkeypatch.setattr(ex, "_pyautogui_click", lambda x, y: clicks.append((x, y)))

    llm = MagicMock()
    llm.settings.base_url = "http://localhost:11434"
    llm.resolve_model.return_value = "llava"

    ok, msg = desktop_act("click the pause button", llm)
    assert isinstance(ok, bool)
    assert isinstance(msg, str)
```

- [ ] **Step 2: Run to confirm failure**

```
python -m pytest tests/test_executor.py::test_describe_with_ollama_no_vision -v
```
Expected: `ImportError: cannot import name '_describe_with_ollama'`

- [ ] **Step 3: Implement in `sam_brain/executor.py`**

```python
# ---------------------------------------------------------------------------
# Vision — screenshot + Ollama describe + pyautogui act
# ---------------------------------------------------------------------------

def _capture_screen(output_path: Path) -> Path:
    """Capture the primary monitor to a PNG. Extracted for monkeypatching."""
    try:
        import mss
        from PIL import Image as _Image
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            img = _Image.frombytes("RGB", shot.size, shot.rgb)
            img.save(output_path, format="PNG")
    except ImportError:
        # Fallback: blank image if mss/PIL not installed
        try:
            from PIL import Image as _Image
            _Image.new("RGB", (1920, 1080), (50, 50, 50)).save(output_path)
        except ImportError:
            output_path.write_bytes(b"")
    return output_path


def _describe_with_ollama(image_path: Path, llm_client) -> str:
    """
    Pass a screenshot to Ollama's vision endpoint and return the description.
    Falls back to a local size/colour summary if vision is unavailable.
    """
    import base64

    try:
        img_bytes = image_path.read_bytes()
        b64 = base64.b64encode(img_bytes).decode("ascii")
    except Exception as exc:
        return f"Could not read screenshot: {exc}"

    prompt = (
        "Describe this screenshot in detail. "
        "What application is open? What text is visible? What is happening on screen?"
    )

    try:
        body = _ollama_generate(
            f"{llm_client.settings.base_url}/api/generate",
            {
                "model": llm_client.resolve_model(),
                "prompt": prompt,
                "images": [b64],
                "stream": False,
            },
        )
        response = str(body.get("response", "")).strip()
        if response and "cannot process" not in response.lower() and len(response) > 10:
            return response
    except Exception:
        pass

    # Local fallback
    return _local_image_summary(image_path)


def _local_image_summary(image_path: Path) -> str:
    """Basic size/colour summary when vision model is unavailable."""
    try:
        from PIL import Image as _Image
        with _Image.open(image_path) as img:
            img = img.convert("RGB")
            w, h = img.size
            avg = img.resize((1, 1)).getpixel((0, 0))
            brightness = sum(avg) / 3
            tone = "dark" if brightness < 85 else "bright" if brightness > 170 else "medium"
        return (
            f"Screenshot captured ({w}x{h}). "
            f"Average tone: {tone} (rgb{avg}). "
            f"Install a vision model with `ollama pull llava` for a full description."
        )
    except Exception as exc:
        return f"Screenshot saved but could not analyse: {exc}"


def _pyautogui_click(x: int, y: int) -> None:
    """Click at screen coordinates. Extracted for monkeypatching."""
    import pyautogui
    pyautogui.click(x, y)


def _pyautogui_press(key: str) -> None:
    """Press a key. Extracted for monkeypatching."""
    import pyautogui
    pyautogui.press(key)


def desktop_act(instruction: str, llm_client) -> tuple[bool, str]:
    """
    See → Decide → Act loop.
    1. Take screenshot
    2. Ask Ollama: what action to perform (click x,y or press key)
    3. Execute via pyautogui
    4. Take a second screenshot to verify
    Returns (success, message).
    """
    import tempfile

    tmp_dir = Path(tempfile.gettempdir())
    before = tmp_dir / "sam_before.png"
    after = tmp_dir / "sam_after.png"

    _capture_screen(before)

    prompt = (
        f"Look at this screenshot. I need to: {instruction}\n\n"
        "Return ONLY a JSON object describing the action:\n"
        '  {"action": "click", "x": 123, "y": 456}   — to click a screen position\n'
        '  {"action": "key", "key": "space"}           — to press a key\n'
        "Give pixel coordinates for click. No explanation."
    )

    try:
        import base64
        b64 = base64.b64encode(before.read_bytes()).decode("ascii")
        body = _ollama_generate(
            f"{llm_client.settings.base_url}/api/generate",
            {
                "model": llm_client.resolve_model(),
                "prompt": prompt,
                "images": [b64],
                "stream": False,
            },
        )
        raw = str(body.get("response", "")).strip()
        # Extract JSON from response
        m = re.search(r'\{[^}]+\}', raw)
        if not m:
            return False, f"Could not parse action from model response: {raw[:100]}"
        action_data = json.loads(m.group(0))
    except Exception as exc:
        return False, f"Vision call failed: {exc}"

    # Execute the action
    try:
        if action_data.get("action") == "click":
            x, y = int(action_data["x"]), int(action_data["y"])
            _pyautogui_click(x, y)
            msg = f"Clicked ({x}, {y})"
        elif action_data.get("action") == "key":
            key = str(action_data["key"])
            _pyautogui_press(key)
            msg = f"Pressed '{key}'"
        else:
            return False, f"Unknown action type: {action_data}"
    except Exception as exc:
        return False, f"pyautogui action failed: {exc}"

    # Verify: take another screenshot
    _capture_screen(after)
    verify_desc = _describe_with_ollama(after, llm_client)
    return True, f"{msg}. Screen now: {verify_desc[:200]}"
```

- [ ] **Step 4: Run all tests**

```
python -m pytest tests/test_executor.py -v
```
Expected: all 29 tests PASS. (PIL must be installed: `pip install Pillow`)

- [ ] **Step 5: Commit**

```bash
git add sam_brain/executor.py tests/test_executor.py
git commit -m "feat: executor desktop vision — _describe_with_ollama, desktop_act, _capture_screen"
```

---

## Task 6: Seed Screenshot Script

**Files:**
- Create: `sam_skills/system/screenshot.py`

- [ ] **Step 1: Create the seed script**

```python
# sam_skills/system/screenshot.py
"""
Standalone screenshot skill.
Captures the primary monitor and saves to Desktop/sam_screen.png.
"""
from __future__ import annotations
import sys
from pathlib import Path

OUTPUT = Path.home() / "Desktop" / "sam_screen.png"

try:
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        img.save(OUTPUT, format="PNG")
    print(f"Screenshot saved: {OUTPUT}")

except ImportError as exc:
    print(f"Missing package: {exc}. Run: pip install mss Pillow")
    sys.exit(1)
except Exception as exc:
    print(f"Screenshot failed: {exc}")
    sys.exit(1)
```

- [ ] **Step 2: Register it in the skills index**

Create `sam_skills/index.json` with this initial entry:

```json
{
  "screenshot": {
    "slug": "screenshot",
    "description": "Take a screenshot of the current screen and save to Desktop",
    "domain": "system",
    "tags": ["screenshot", "screen", "capture", "see", "vision"],
    "file": "system/screenshot.py",
    "run_count": 0,
    "last_run": "",
    "status": "active"
  }
}
```

- [ ] **Step 3: Verify the script runs**

```
cd C:\Users\DELL.COM\Desktop\Darey\sam-v2-clean
python sam_skills/system/screenshot.py
```
Expected: `Screenshot saved: C:\Users\DELL.COM\Desktop\sam_screen.png`

(If mss is missing: `pip install mss Pillow` then retry)

- [ ] **Step 4: Commit**

```bash
git add sam_skills/system/screenshot.py sam_skills/index.json
git commit -m "feat: seed screenshot skill in sam_skills/system/"
```

---

## Task 7: Music Memory Functions

**Files:**
- Modify: `sam_brain/memory.py`
- Create: `tests/test_music_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_music_memory.py
from __future__ import annotations
import pytest
from pathlib import Path
from sam_brain.memory import (
    save_music_pref,
    mark_music_liked,
    get_music_prefs,
    get_liked_music,
)


@pytest.fixture
def mem(tmp_path):
    return tmp_path / "memory.json"


def test_save_music_pref_creates_entry(mem):
    save_music_pref("lofi hip hop", "play_lofi", mem)
    prefs = get_music_prefs(mem)
    titles = [p["title"] for p in prefs["playlist"]]
    assert "lofi hip hop" in titles


def test_save_music_pref_increments_play_count(mem):
    save_music_pref("lofi hip hop", "play_lofi", mem)
    save_music_pref("lofi hip hop", "play_lofi", mem)
    prefs = get_music_prefs(mem)
    entry = next(p for p in prefs["playlist"] if p["title"] == "lofi hip hop")
    assert entry["play_count"] == 2


def test_mark_music_liked(mem):
    save_music_pref("lofi hip hop", "play_lofi", mem)
    mark_music_liked("lofi hip hop", mem)
    prefs = get_music_prefs(mem)
    assert "lofi hip hop" in prefs["liked"]
    entry = next(p for p in prefs["playlist"] if p["title"] == "lofi hip hop")
    assert entry["liked"] is True


def test_mark_music_liked_idempotent(mem):
    save_music_pref("lofi hip hop", "play_lofi", mem)
    mark_music_liked("lofi hip hop", mem)
    mark_music_liked("lofi hip hop", mem)
    prefs = get_music_prefs(mem)
    assert prefs["liked"].count("lofi hip hop") == 1


def test_get_liked_music_sorted_by_play_count(mem):
    save_music_pref("lofi", "play_lofi", mem)
    save_music_pref("lofi", "play_lofi", mem)
    save_music_pref("lofi", "play_lofi", mem)
    mark_music_liked("lofi", mem)

    save_music_pref("jazz", "play_jazz", mem)
    mark_music_liked("jazz", mem)

    liked = get_liked_music(mem)
    assert liked[0]["title"] == "lofi"  # highest play count first


def test_get_music_prefs_empty(mem):
    prefs = get_music_prefs(mem)
    assert prefs["playlist"] == []
    assert prefs["liked"] == []
    assert prefs["disliked"] == []
```

- [ ] **Step 2: Run to confirm failure**

```
python -m pytest tests/test_music_memory.py -v
```
Expected: `ImportError: cannot import name 'save_music_pref'`

- [ ] **Step 3: Add to `sam_brain/memory.py`**

Add this section at the end of the file, before the final blank line:

```python
# ---------------------------------------------------------------------------
# Music preferences & playlist
# ---------------------------------------------------------------------------

def get_music_prefs(memory_path: Path | None) -> dict:
    """Return the full music_preferences block. Creates empty structure if missing."""
    data = _load(memory_path)
    return data.get("music_preferences", {"playlist": [], "liked": [], "disliked": []})


def save_music_pref(title: str, skill_slug: str, memory_path: Path | None) -> None:
    """
    Record that a track was played.
    Increments play_count if the title already exists, otherwise creates a new entry.
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
    """
    Return playlist entries that are liked, sorted by play_count descending.
    """
    prefs = get_music_prefs(memory_path)
    liked_titles = set(t.lower() for t in prefs.get("liked", []))
    liked = [
        p for p in prefs.get("playlist", [])
        if p.get("title", "").lower() in liked_titles
    ]
    liked.sort(key=lambda p: int(p.get("play_count", 0)), reverse=True)
    return liked
```

- [ ] **Step 4: Run tests — all pass**

```
python -m pytest tests/test_music_memory.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sam_brain/memory.py tests/test_music_memory.py
git commit -m "feat: music memory — save_music_pref, mark_music_liked, get_liked_music"
```

---

## Task 8: Wire `execute` into SamBrain

**Files:**
- Modify: `sam_brain/brain.py`

- [ ] **Step 1: Add `execute` to `_THINK_PROMPT`**

In `brain.py`, replace the actions block in `_THINK_PROMPT`:

```python
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
  execute  - run a one-shot desktop task RIGHT NOW: open a URL, play/pause/resume media,
             take a screenshot, describe the screen, control the desktop, install a package
  remember - save new information, a path, or a credential to memory
  query    - read live data from a database (Firestore, SQL, etc.)
  skill    - run an acquired skill script (use skill_name field to specify which one)

Examples (message -> action):
  "hey Sam"                                     -> chat
  "what can you do?"                            -> chat
  "where are your own files?"                   -> chat
  "find the estate project"                     -> find
  "can you find my pubspec.yaml?"               -> find
  "can you fix the login bug in estate?"        -> code
  "can you add a dark mode toggle?"             -> code
  "open the folder"                             -> open
  "run the tests"                               -> run
  "open YouTube Music in Chrome"                -> execute
  "play some lofi music"                        -> execute
  "pause the music"                             -> execute
  "resume the music"                            -> execute
  "take a screenshot"                           -> execute
  "what can you see on my screen?"              -> execute
  "open chrome and go to github.com"            -> execute
  "install pyautogui"                           -> execute
  "remember that my key is in Documents"        -> remember
  "what is my name?"                            -> chat
  "can you fix code in general?"                -> chat
  "I can not wait to test this"                 -> chat

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
- execute = for immediate desktop actions, no project needed.
- run/code = for actions on a software project codebase.
- is_confirming_pending: true ONLY if a pending task exists AND user is agreeing.
- Use open only for opening folders/files in Explorer.
- Use remember ONLY when user is STORING new info. Recall = chat.
- Frustration, greetings, vague questions = chat.
- When in doubt = chat.
- Output ONLY the JSON. No extra text."""
```

- [ ] **Step 2: Add `execute` to `valid_actions` in `_think()`**

Find this line in the `_think()` method:
```python
valid_actions = {"chat", "find", "open", "code", "run", "remember", "query", "skill"}
```
Replace with:
```python
valid_actions = {"chat", "find", "open", "code", "run", "execute", "remember", "query", "skill"}
```

- [ ] **Step 3: Add `_pending_music_like` to `__init__`**

In `SamBrain.__init__`, after `self._last_thought_action: str = "chat"`, add:
```python
        # Pending music like — set after play, checked on next message
        self._pending_music_like: dict | None = None
```

- [ ] **Step 4: Add like-detection to `_record_and_return`**

Replace the existing `_record_and_return` method with:

```python
def _record_and_return(self, response: str) -> str:
    """Record Sam's response to history, check pending music like, audit quality."""
    print(f"[SAM] {response[:120]}{'…' if len(response) > 120 else ''}", flush=True)
    self._history.append({"role": "assistant", "content": response})

    # ── Music like detection ──────────────────────────────────────────────
    if self._pending_music_like:
        pending = self._pending_music_like
        self._pending_music_like = None
        # The PREVIOUS user message (the one that triggered this response)
        # is already in history. The message BEFORE that was the play request.
        # If the user's last message is not about changing/stopping music,
        # mark the track as liked.
        recent_user = next(
            (t["content"] for t in reversed(self._history[:-1]) if t["role"] == "user"),
            "",
        )
        change_words = {"change", "stop", "different", "another", "skip", "off", "no", "not"}
        if not any(w in recent_user.lower().split() for w in change_words):
            try:
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
```

- [ ] **Step 5: Add `elif action == "execute":` routing in `handle()`**

In the `# ── Step 4: Act ───` section, add the execute branch after the `elif action == "open":` line:

```python
            elif action == "execute":
                return self._record_and_return(self._handle_execute(goal))
```

- [ ] **Step 6: Add `_handle_execute` method to `SamBrain`**

Add this method after `_handle_open`:

```python
def _handle_execute(self, goal: str) -> str:
    """
    Execute a one-shot desktop task immediately.
    Order: check skills → reuse/patch → write fresh → auto-fix → save.
    """
    from sam_brain.executor import (
        check_skills, run_skill_script, patch_skill, llm_write_script,
        run_and_learn, desktop_act, increment_run_count,
        _describe_with_ollama, _capture_screen,
    )
    from sam_brain.memory import save_music_pref, get_liked_music
    from workers.names import resolve_worker_identity
    import tempfile

    goal_lower = goal.lower()
    is_vision = any(w in goal_lower for w in ("see", "screen", "screenshot", "look", "what", "describe", "vision"))
    is_music = any(w in goal_lower for w in ("music", "play", "pause", "resume", "song", "lofi", "spotify", "youtube music"))
    is_unprompted_play = is_music and not any(w in goal_lower for w in ("pause", "resume", "stop")) and \
                         not any(w in goal_lower for w in ("lofi", "jazz", "pop", "rock", "hip", "classical"))

    worker = resolve_worker_identity("execute")
    _worker_print(worker.name, f"Execute: {goal[:60]}")

    # ── Unprompted play: use liked playlist ───────────────────────────────
    if is_unprompted_play and "youtube" not in goal_lower and "spotify" not in goal_lower:
        liked = get_liked_music(self.memory_path)
        if liked:
            top = liked[0]
            skill_match = check_skills(top["title"])
            if skill_match and not skill_match.needs_patch:
                ok, output = run_skill_script(skill_match.file_path)
                if ok:
                    increment_run_count(skill_match.slug)
                    save_music_pref(top["title"], skill_match.slug, self.memory_path)
                    self._pending_music_like = {"title": top["title"], "skill": skill_match.slug}
                    return f"Playing {top['title']} — your most played."

    # ── Vision: screenshot + describe ────────────────────────────────────
    if is_vision and "pause" not in goal_lower and "click" not in goal_lower:
        tmp = Path(tempfile.gettempdir()) / "sam_screen.png"
        _capture_screen(tmp)
        description = _describe_with_ollama(tmp, self.llm)
        return f"Here's what I can see:\n\n{description}"

    # ── Desktop act (vision loop for click-based tasks) ───────────────────
    needs_vision_act = any(w in goal_lower for w in ("pause", "resume", "click", "press", "button"))
    if needs_vision_act:
        ok, msg = desktop_act(goal, self.llm)
        if ok:
            if is_music:
                title = self._last_music_title or "current track"
                save_music_pref(title, "", self.memory_path)
            return msg
        # Fall through to script approach if vision act failed

    # ── Check skills library first ────────────────────────────────────────
    match = check_skills(goal)

    if match and not match.needs_patch:
        # Exact/close match — run as-is
        ok, output = run_skill_script(match.file_path)
        if ok:
            increment_run_count(match.slug)
            if is_music:
                title = self._extract_music_title(goal)
                save_music_pref(title, match.slug, self.memory_path)
                self._pending_music_like = {"title": title, "skill": match.slug}
            return f"Done. {output[:200]}" if output else "Done."
        # Skill failed — fall through to write fresh
        _worker_print(worker.name, f"Skill {match.slug} failed, writing fresh script")

    if match and match.needs_patch:
        # Related match — duplicate + patch
        try:
            base_code = match.file_path.read_text(encoding="utf-8")
            patched = patch_skill(base_code, goal, self.llm)
            ok, output = run_and_learn(patched, goal, self.llm)
            if ok:
                if is_music:
                    title = self._extract_music_title(goal)
                    new_slug = check_skills(goal)
                    save_music_pref(title, new_slug.slug if new_slug else "", self.memory_path)
                    self._pending_music_like = {"title": title, "skill": ""}
                return f"Done. {output[:200]}" if output else "Done."
        except Exception:
            pass  # Fall through to write fresh

    # ── No match — write script from LLM ─────────────────────────────────
    _worker_print(worker.name, "Writing script...")
    script = llm_write_script(goal, self.llm)
    if not script:
        return "I couldn't figure out how to do that. Can you give me more detail?"

    ok, output = run_and_learn(script, goal, self.llm)
    if ok:
        if is_music:
            title = self._extract_music_title(goal)
            new_match = check_skills(goal)
            save_music_pref(title, new_match.slug if new_match else "", self.memory_path)
            self._pending_music_like = {"title": title, "skill": ""}
        return f"Done. {output[:200]}" if output else "Done."

    return f"I tried but something went wrong: {output[:300]}"

def _extract_music_title(self, goal: str) -> str:
    """Extract a track/genre title from a play goal. Falls back to 'music'."""
    stop = {"play", "open", "start", "some", "the", "a", "on", "in", "youtube", "music", "spotify", "chrome"}
    words = [w for w in goal.lower().split() if w not in stop and len(w) > 2]
    return " ".join(words[:3]) if words else "music"
```

Also add `self._last_music_title: str = ""` to `__init__` after `_pending_music_like`.

- [ ] **Step 7: Smoke test the full flow**

```
cd C:\Users\DELL.COM\Desktop\Darey\sam-v2-clean
python sam_test_drive.py "open YouTube Music in Chrome"
```
Expected: Chrome opens at music.youtube.com. No "which project?" prompt. Terminal shows `[Execute] Execute: open YouTube Music in Chrome`.

- [ ] **Step 8: Run the full test suite**

```
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add sam_brain/brain.py
git commit -m "feat: wire execute action into SamBrain — _handle_execute, music like-detection, vision loop"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All 4 sections covered. Success criteria 1–6 all have implementing tasks.
- [x] **No placeholders:** Every step has complete code. No TBDs.
- [x] **Type consistency:** `SkillMatch` defined in Task 2, used in Tasks 4 and 8. `save_music_pref` defined in Task 7, called in Task 8. `_ollama_generate` defined in Task 4, monkeypatched in Task 5 tests.
- [x] **`_last_music_title`** added in same step as `_pending_music_like` to avoid NameError.
- [x] **`increment_run_count`** defined in Task 4 executor, imported in Task 8.
