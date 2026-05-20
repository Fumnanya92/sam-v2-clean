# Sam `execute` Action — Design Spec
**Date:** 2026-05-20
**Status:** Approved

---

## Problem

Sam's brain currently has no way to run a one-shot desktop task immediately. Both the `run` and `code` actions route through `_handle_code_smart`, which requires a named project context. When asked to "open Chrome", "take a screenshot", or "pause music", Sam asks "which project should Forge work on?" — and then either stalls or hallucinates execution.

---

## Goal

Give Sam a direct `execute` action that:
- Runs one-shot Python scripts immediately on the user's Windows machine
- Learns from successful runs by saving scripts as reusable skills
- Builds a music preference memory and playlist over time
- Describes the screen using Ollama vision when asked

---

## Section 1 — The `execute` Action

### New brain action
Add `execute` to `_THINK_PROMPT`'s action list and `valid_actions` set in `brain.py`.

**When the LLM should pick `execute` (not `run`/`code`):**
- Task needs to happen right now on this machine
- No project codebase is involved
- Examples: open a URL, take a screenshot, play/pause/resume media, install a package, run a system command

**Routing flow:**
```
User message
  → _think() → action = "execute", goal = "open YouTube Music in Chrome"
  → _handle_execute(goal)
      1. check_skills(goal)            # fuzzy match against sam_skills/
      2a. exact/close match → run skill script
      2b. related match     → duplicate + patch + save + run
      2c. no match          → llm_write_script(goal) + run + save on success
      3. on failure         → auto_fix_once() → retry
      4. report result      # silent — just the outcome, no script dump
```

### `_handle_execute(goal: str) -> str`
Lives in `sam_brain/brain.py`. Calls helpers from a new `sam_brain/executor.py` module.

---

## Section 2 — Skills Library (`sam_skills/`)

### Directory layout

Root: `{sam_root}/sam_skills/` — same parent directory as `sam_brain/`, resolved as `Path(__file__).parent.parent / "sam_skills"` inside `executor.py`.

```
sam_skills/
  music/
    play_youtube_music.py
    play_youtube_music_lofi.py
    pause_music.py
    resume_music.py
  browser/
    open_chrome.py
  system/
    screenshot.py
  <domain>/
    <slug>.py
```

Each skill file is a standalone Python script. A companion `sam_skills/index.json` tracks metadata:

```json
{
  "play_youtube_music": {
    "slug": "play_youtube_music",
    "description": "Open Chrome and play YouTube Music",
    "domain": "music",
    "tags": ["youtube", "music", "chrome", "play"],
    "file": "music/play_youtube_music.py",
    "run_count": 3,
    "last_run": "2026-05-20",
    "status": "active"
  }
}
```

### Skill matching logic (in `sam_brain/executor.py`)

1. **Exact slug match** — task maps directly to a known slug → run as-is
2. **Fuzzy tag/description match** (score ≥ 0.7) → run as-is
3. **Same domain, different detail** (score 0.4–0.69) → duplicate file, patch the differing value (URL, song title), save under new slug, run
4. **No match** (score < 0.4) → generate fresh script via LLM, save on success

Sam never deletes old skills. Library only grows.

### Auto-fix on failure (one retry)
When a script fails, `auto_fix_once()`:
- `ModuleNotFoundError` → `pip install <module>` → retry
- Chrome not found at default path → try common Windows paths (`C:\Program Files\Google\Chrome\...`, `C:\Program Files (x86)\...`) → patch script → retry
- Any other error → report the error message to user, no retry

---

## Section 3 — Music Memory & Playlist

### Storage
Inside `memory.json` under a `music_preferences` key:

```json
{
  "music_preferences": {
    "playlist": [
      {
        "title": "lofi hip hop",
        "skill": "play_youtube_music_lofi",
        "play_count": 3,
        "last_played": "2026-05-20",
        "liked": true
      }
    ],
    "liked": ["lofi hip hop"],
    "disliked": []
  }
}
```

### Like detection
After Sam plays music, he sets a `_pending_music_like` flag with the track title and skill slug. If the user's *next* message is NOT about changing or stopping the music, Sam marks it as liked and increments play count. If the user immediately asks to change it, he does not mark it.

### Unprompted play
When the user says "play something" or "play music" with no specific request:
- Sam checks `liked` list ordered by `play_count` descending
- Picks the top entry and runs that skill
- Responds: *"Playing lofi hip hop — your most played."*
- If no history yet, defaults to opening YouTube Music home page

---

## Section 4 — Vision (Screenshot + Describe)

### Flow
```
User: "what can you see?" / "take a screenshot and describe it"
  → execute action, goal = "screenshot and describe"
  → _handle_execute → runs system/screenshot.py (mss capture)
  → passes saved PNG to _describe_with_ollama(image_path)
  → returns description as Sam's response
```

### Ollama vision call (`sam_brain/executor.py`)
```python
def _describe_with_ollama(image_path: Path, llm_client) -> str:
    # Encode image as base64
    # POST to /api/generate with model=llm_client.resolve_model()
    # Include {"images": [base64_str]} in the payload
    # Return response text
```

If the model returns an error indicating no vision support, Sam responds:
> *"I captured the screenshot but my current Ollama model can't describe images. Try `ollama pull llava` and I'll be able to see."*

Fallback: `describe_image_locally()` from ScreenCaptureAI (size/colour/brightness summary).

---

## Files Changed / Created

| File | Change |
|---|---|
| `sam_brain/brain.py` | Add `execute` to `_THINK_PROMPT` + `valid_actions`; add `_handle_execute()`; add `_pending_music_like` tracking in `_record_and_return()` |
| `sam_brain/executor.py` | **New** — `check_skills()`, `run_skill_script()`, `llm_write_script()`, `auto_fix_once()`, `save_skill()`, `_describe_with_ollama()` |
| `sam_brain/memory.py` | Add `get_music_prefs()`, `save_music_like()`, `get_liked_music()` |
| `sam_skills/index.json` | **New** — skill registry, auto-created on first save |
| `sam_skills/music/` | **New** — populated at runtime as skills are learned |
| `sam_skills/system/screenshot.py` | **New** — mss screenshot script seeded at startup |

---

## Out of Scope

- No Selenium/WebDriver automation (subprocess + Chrome URL is sufficient for YouTube Music)
- No cross-machine or remote execution
- No Windows audio API control (pause/resume is done via pyautogui keyboard shortcut `space` on the Chrome window)
- No OpenAI vision (Ollama only, as decided)
- No user-facing skill editor UI

---

## Success Criteria

1. `"open YouTube Music"` → Chrome opens at music.youtube.com, no "which project?" prompt
2. `"pause the music"` → sends space keypress to Chrome window via pyautogui
3. `"play lofi"` on day 2 → Sam finds the skill, runs it without writing a new script
4. `"play something"` after 3 sessions → Sam picks the most-played liked track
5. `"what can you see?"` → Sam captures screen and returns an Ollama vision description
6. Missing `pyautogui` → auto-installs and retries once
