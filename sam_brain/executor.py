"""
Sam's one-shot execution engine.

Lifecycle for every execute request:
  1. _classify_execute_goal() — LLM decides task type, domain, music title
  2. check_skills()           — LLM matches against the skill library
  2a. exact match  → run existing skill as-is
  2b. close match  → duplicate + patch via LLM + run
  2c. no match     → llm_write_script() + run
  3. auto_fix_once()          — one retry on failure (pip install or path patch)
  4. save_skill()             — persist on success so Sam reuses it next time

No keyword matching. The LLM decides everything about intent, domain, and skill fit.
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


# ---------------------------------------------------------------------------
# Slug / tags helpers — pure text processing, not routing
# ---------------------------------------------------------------------------

def _slug_from_goal(goal: str) -> str:
    """Convert a goal description to a filesystem-safe slug, max 50 chars."""
    slug = re.sub(r"[^a-z0-9]+", "_", goal.lower().strip())
    slug = slug.strip("_")
    return slug[:50]


def _tags_from_goal(goal: str) -> list[str]:
    """Extract meaningful words from a goal to use as index tags."""
    _stop = {"a", "an", "the", "is", "to", "on", "in", "for", "me", "my", "can",
             "you", "it", "do", "and", "or", "of", "at", "by"}
    words = re.findall(r"[a-z0-9]+", goal.lower())
    return [w for w in words if w not in _stop and len(w) > 2][:8]


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


# ---------------------------------------------------------------------------
# Skill matching — LLM decides, not keyword overlap
# ---------------------------------------------------------------------------

class SkillMatch(NamedTuple):
    slug: str
    score: float       # 1.0 = exact, 0.5 = close (needs patch)
    file_path: Path
    needs_patch: bool  # True when LLM says "close" — duplicate + patch before running


def check_skills(
    goal: str,
    llm_client=None,
    skills_root: Path | None = None,
) -> SkillMatch | None:
    """
    Ask the LLM which skill (if any) best matches this goal.
    Returns SkillMatch or None if no skill fits.
    """
    root = skills_root or SKILLS_ROOT
    index = load_index(root)
    if not index:
        return None

    active = {slug: e for slug, e in index.items() if e.get("status") == "active"}
    if not active:
        return None

    if llm_client is None:
        return None

    skills_list = "\n".join(
        f'  {slug}: {entry["description"]}'
        for slug, entry in active.items()
    )

    prompt = (
        f'Goal: "{goal}"\n\n'
        f'Available skills:\n{skills_list}\n\n'
        'Which skill best fits this goal? Reply ONLY with JSON:\n'
        '{\n'
        '  "slug": "skill_slug or empty string if none match",\n'
        '  "fit": "exact|close|none"\n'
        '}\n\n'
        'exact = the skill can run as-is to accomplish this goal\n'
        'close = same domain, needs a small change (e.g. different URL or song title)\n'
        'none  = no skill matches this goal\n'
        'Output ONLY the JSON. No explanation.'
    )

    try:
        body = _ollama_generate(
            f"{llm_client.settings.base_url}/api/generate",
            {"model": llm_client.resolve_model(), "prompt": prompt, "stream": False},
        )
        raw = str(body.get("response", "")).strip()
        m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        fit = str(data.get("fit", "none")).lower()
        slug = str(data.get("slug", "")).strip()
        if fit == "none" or not slug or slug not in active:
            return None
        entry = active[slug]
        file_path = root / entry["file"]
        return SkillMatch(
            slug=slug,
            score=1.0 if fit == "exact" else 0.5,
            file_path=file_path,
            needs_patch=(fit == "close"),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Script execution
# ---------------------------------------------------------------------------

def _run_script(code: str, timeout: int = 30) -> tuple[bool, str, str]:
    """Write code to a temp file and run it. Returns (success, stdout, stderr)."""
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
    m = re.search(r"No module named '([^']+)'", error_output)
    if m:
        pkg = m.group(1).split(".")[0]
        return {"action": "pip_install", "package": pkg}

    if "chrome" in error_output.lower() and (
        "not found" in error_output.lower()
        or "cannot find" in error_output.lower()
        or "FileNotFoundError" in error_output
    ):
        for chrome_path in _CHROME_PATHS:
            if Path(chrome_path).exists():
                _safe = chrome_path
                patched = re.sub(
                    r'["\'](chrome|google-chrome)["\']',
                    lambda m, p=_safe: f'"{p}"',
                    script,
                )
                return {"action": "patch_script", "patched_script": patched}
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
    """Write script to sam_skills/<domain>/<slug>.py and register in index."""
    root = skills_root or SKILLS_ROOT
    skill_dir = root / domain
    skill_dir.mkdir(parents=True, exist_ok=True)

    script_path = skill_dir / f"{slug}.py"
    script_path.write_text(script_code, encoding="utf-8")

    index = load_index(root)
    relative_file = f"{domain}/{slug}.py"

    if slug in index:
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
    with _req.urlopen(req, timeout=60) as resp:
        return _json.loads(resp.read())


def _extract_python(text: str) -> str:
    """Pull Python code out of an LLM response that may include markdown fences."""
    m = re.search(r"```python\s*([\s\S]+?)```", text)
    if m:
        return m.group(1).strip()
    text = text.strip()
    if text.startswith(("import", "from", "subprocess", "#", "def ", "class ")):
        return text
    return text


def _classify_execute_goal(goal: str, llm_client) -> dict:
    """
    Ask the LLM to classify the execute goal and extract metadata.
    The LLM — not keywords — decides what kind of task this is.

    Returns:
      {
        "task_type":         "vision|act|music_play|script",
        "music_title":       "track or genre if music_play, else empty",
        "domain":            "music|browser|system|general",
        "is_unprompted_play": True if user said 'play music' with no specific request
      }
    """
    prompt = (
        f'Desktop task: "{goal}"\n\n'
        'Classify this task. Reply ONLY with JSON:\n'
        '{\n'
        '  "task_type": "vision|act|music_play|script",\n'
        '  "music_title": "track or genre name if music_play, else empty string",\n'
        '  "domain": "music|browser|system|general",\n'
        '  "is_unprompted_play": true or false\n'
        '}\n\n'
        'task_type meanings:\n'
        '  vision       = take screenshot / describe screen / what can you see\n'
        '  act          = click or press a button using vision to locate it\n'
        '  music_play   = open and play music (specific track, genre, or general)\n'
        '  script       = any other desktop action (open URL, install package, etc.)\n'
        'is_unprompted_play = true ONLY when the user said something like "play music" or '
        '"play something" with no specific track, artist, or genre mentioned\n'
        'Output ONLY the JSON. No explanation.'
    )
    try:
        body = _ollama_generate(
            f"{llm_client.settings.base_url}/api/generate",
            {"model": llm_client.resolve_model(), "prompt": prompt, "stream": False},
        )
        raw = str(body.get("response", "")).strip()
        m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            return {
                "task_type": str(data.get("task_type", "script")).lower(),
                "music_title": str(data.get("music_title", "")).strip(),
                "domain": str(data.get("domain", "general")).lower(),
                "is_unprompted_play": bool(data.get("is_unprompted_play", False)),
            }
    except Exception:
        pass
    return {
        "task_type": "script",
        "music_title": "",
        "domain": "general",
        "is_unprompted_play": False,
    }


def llm_write_script(goal: str, llm_client) -> str:
    """Ask Ollama to write a standalone Python script for goal. Returns Python source."""
    prompt = (
        f"Write a standalone Python 3 script for Windows that does this:\n\n{goal}\n\n"
        "Rules:\n"
        "- Must be fully self-contained (handle its own imports)\n"
        "- Must work on Windows 10\n"
        "- To launch Chrome: subprocess.run(['cmd', '/c', 'start', 'chrome', 'URL'])\n"
        "- Use pyautogui for mouse/keyboard control if needed\n"
        "- Use mss for screenshots if needed\n"
        "- Print a short confirmation message when done\n"
        "- Handle errors with try/except and print what went wrong\n"
        "- No user input — run silently and complete\n\n"
        "IMPORTANT rules for music tasks:\n"
        "- NEVER open a search results page — open a DIRECT playable URL\n"
        "- For YouTube Music lofi: use https://music.youtube.com/watch?v=jfKfPfyJRdk\n"
        "- For YouTube Music jazz: use https://music.youtube.com/watch?v=Dx5qFachd3A\n"
        "- For YouTube Music general: use https://music.youtube.com (home page auto-resumes)\n"
        "- After opening Chrome, do: import time; time.sleep(4)\n"
        "- Then use pyautogui.press('space') to start/resume playback\n"
        "- This ensures music actually plays, not just the page opens\n\n"
        "IMPORTANT rules for pause/resume:\n"
        "- Use pyautogui.press('space') — the universal media play/pause key\n"
        "- Do NOT try to click anything — just press space\n\n"
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
    """Ask Ollama to adapt base_script for new_goal with minimal changes."""
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
    import time as _time
    root = skills_root or SKILLS_ROOT
    index = load_index(root)
    if slug in index:
        index[slug]["run_count"] = int(index[slug].get("run_count", 0)) + 1
        index[slug]["last_run"] = _time.strftime("%Y-%m-%d")
        save_index(index, root)


def _ensure_media_playing() -> None:
    """Press space after a short delay to ensure media playback has started."""
    import time as _time
    _time.sleep(4)
    try:
        import pyautogui
        pyautogui.press("space")
    except Exception:
        pass


def run_and_learn(
    script: str,
    goal: str,
    llm_client,
    skills_root: Path | None = None,
) -> tuple[bool, str]:
    """Run a script, auto-fix once on failure, save as skill on success."""
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
                    script = patched

    if ok:
        classification = _classify_execute_goal(goal, llm_client)
        slug = _slug_from_goal(goal)
        domain = classification.get("domain", "general")
        tags = _tags_from_goal(goal)
        save_skill(slug, goal, domain, tags, script, skills_root)

        # Ensure media actually plays after any music script
        if classification.get("task_type") == "music_play":
            _ensure_media_playing()

    return ok, combined


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
        try:
            from PIL import Image as _Image
            _Image.new("RGB", (1920, 1080), (50, 50, 50)).save(output_path)
        except ImportError:
            output_path.write_bytes(b"")
    return output_path


def _ocr_screen(image_path: Path) -> list[dict]:
    """
    Run OCR on a screenshot and return list of {text, x, y, w, h} for each element.
    Uses easyocr (pip install easyocr). Falls back to empty list on failure.
    """
    try:
        import easyocr
        reader = easyocr.Reader(["en"], verbose=False, gpu=False)
        results = reader.readtext(str(image_path))
        elements = []
        for (bbox, text, conf) in results:
            if conf < 0.3 or not text.strip():
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x = int(min(xs))
            y = int(min(ys))
            w = int(max(xs) - min(xs))
            h = int(max(ys) - min(ys))
            cx = x + w // 2
            cy = y + h // 2
            elements.append({"text": text.strip(), "x": cx, "y": cy, "w": w, "h": h})
        return elements
    except Exception:
        return []


def _describe_with_ocr(image_path: Path) -> str:
    """
    Describe a screenshot by extracting all visible text with OCR.
    Returns a human-readable summary of what text is on screen.
    """
    elements = _ocr_screen(image_path)
    if not elements:
        return _local_image_summary(image_path)

    lines = [f'  "{e["text"]}"  (at {e["x"]},{e["y"]})' for e in elements[:30]]
    return "Visible text on screen:\n" + "\n".join(lines)


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
    See -> Decide -> Act loop using OCR + text LLM (no vision model needed).
    1. Take a screenshot
    2. OCR it to get all visible text with coordinates
    3. Ask the text LLM: given these elements, what should I click/press?
    4. Execute via pyautogui
    5. Take a verification screenshot, OCR it, return what changed
    Returns (success, message).
    """
    tmp_dir = Path(tempfile.gettempdir())
    before = tmp_dir / "sam_before.png"
    after = tmp_dir / "sam_after.png"

    _capture_screen(before)
    elements = _ocr_screen(before)

    if not elements:
        # No text found — fall back to pressing space (safe default for media)
        try:
            _pyautogui_press("space")
            return True, "Pressed space (no text elements found on screen)"
        except Exception as exc:
            return False, f"Could not act on screen: {exc}"

    # Build a text summary of what's on screen for the LLM
    screen_text = "\n".join(
        f'  "{e["text"]}" at ({e["x"]}, {e["y"]})'
        for e in elements[:25]
    )

    prompt = (
        f'I need to: {instruction}\n\n'
        f'Text visible on screen with pixel coordinates:\n{screen_text}\n\n'
        'What single action should I take? Reply ONLY with JSON:\n'
        '  {"action": "click", "x": 123, "y": 456}  -- click this pixel position\n'
        '  {"action": "key", "key": "space"}          -- press this keyboard key\n'
        'No explanation. Just the JSON.'
    )

    try:
        body = _ollama_generate(
            f"{llm_client.settings.base_url}/api/generate",
            {"model": llm_client.resolve_model(), "prompt": prompt, "stream": False},
        )
        raw = str(body.get("response", "")).strip()
        m = re.search(r"\{[^}]+\}", raw)
        if not m:
            return False, f"Could not parse action: {raw[:100]}"
        action_data = json.loads(m.group(0))
    except Exception as exc:
        return False, f"LLM call failed: {exc}"

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
            return False, f"Unknown action: {action_data}"
    except Exception as exc:
        return False, f"pyautogui failed: {exc}"

    _capture_screen(after)
    verify = _describe_with_ocr(after)
    return True, f"{msg}. Screen now: {verify[:200]}"
