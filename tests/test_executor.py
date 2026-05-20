"""Tests for sam_brain/executor.py — all LLM calls are monkeypatched."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm(base_url: str = "http://localhost:11434", model: str = "test-model"):
    llm = MagicMock()
    llm.settings.base_url = base_url
    llm.resolve_model.return_value = model
    return llm


# ---------------------------------------------------------------------------
# Task 1 — Foundation: slug helpers + index load/save
# ---------------------------------------------------------------------------

from sam_brain.executor import _slug_from_goal, _tags_from_goal, load_index, save_index, SKILLS_ROOT


def test_slug_from_goal_basic():
    assert _slug_from_goal("open YouTube Music") == "open_youtube_music"


def test_slug_from_goal_strips_special_chars():
    assert _slug_from_goal("play lofi hip-hop!!") == "play_lofi_hip_hop"


def test_slug_from_goal_max_length():
    long = "this is a very long goal that should be truncated at some point in time"
    assert len(_slug_from_goal(long)) <= 50


def test_tags_from_goal_extracts_words():
    tags = _tags_from_goal("play lofi music on youtube")
    assert "lofi" in tags
    assert "youtube" in tags


def test_tags_from_goal_excludes_stop_words():
    tags = _tags_from_goal("play some music on my machine")
    assert "on" not in tags
    assert "my" not in tags


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


def test_load_index_corrupt(tmp_path):
    root = tmp_path / "sam_skills"
    root.mkdir()
    (root / "index.json").write_text("{corrupt json", encoding="utf-8")
    assert load_index(root) == {}


# ---------------------------------------------------------------------------
# Task 2 — Skill matching (LLM-driven)
# ---------------------------------------------------------------------------

from sam_brain.executor import SkillMatch, check_skills


def _seed_index(root: Path, slug: str = "play_youtube_music") -> None:
    skill_file = root / "music" / f"{slug}.py"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("print('playing')", encoding="utf-8")
    save_index(
        {
            slug: {
                "slug": slug,
                "description": "Play YouTube Music in Chrome",
                "domain": "music",
                "tags": ["play", "youtube", "music"],
                "file": f"music/{slug}.py",
                "run_count": 1,
                "last_run": "2026-05-20",
                "status": "active",
            }
        },
        root,
    )


def test_check_skills_exact_match(monkeypatch, tmp_path):
    import sam_brain.executor as ex

    root = tmp_path / "sam_skills"
    _seed_index(root)

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": '{"slug": "play_youtube_music", "fit": "exact"}'},
    )

    match = check_skills("play youtube music", llm_client=_make_llm(), skills_root=root)
    assert match is not None
    assert match.slug == "play_youtube_music"
    assert match.score == 1.0
    assert match.needs_patch is False


def test_check_skills_close_match(monkeypatch, tmp_path):
    import sam_brain.executor as ex

    root = tmp_path / "sam_skills"
    _seed_index(root)

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": '{"slug": "play_youtube_music", "fit": "close"}'},
    )

    match = check_skills("play lofi on youtube music", llm_client=_make_llm(), skills_root=root)
    assert match is not None
    assert match.needs_patch is True
    assert match.score == 0.5


def test_check_skills_no_match(monkeypatch, tmp_path):
    import sam_brain.executor as ex

    root = tmp_path / "sam_skills"
    _seed_index(root)

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": '{"slug": "", "fit": "none"}'},
    )

    match = check_skills("take a screenshot", llm_client=_make_llm(), skills_root=root)
    assert match is None


def test_check_skills_empty_index(tmp_path):
    root = tmp_path / "sam_skills"
    # No index at all — should return None without calling LLM
    match = check_skills("play music", llm_client=_make_llm(), skills_root=root)
    assert match is None


def test_check_skills_no_llm(tmp_path):
    root = tmp_path / "sam_skills"
    _seed_index(root)
    match = check_skills("play music", llm_client=None, skills_root=root)
    assert match is None


def test_check_skills_llm_error(monkeypatch, tmp_path):
    import sam_brain.executor as ex

    root = tmp_path / "sam_skills"
    _seed_index(root)

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    match = check_skills("play youtube music", llm_client=_make_llm(), skills_root=root)
    assert match is None


# ---------------------------------------------------------------------------
# Task 3 — Script running + auto-fix + save_skill
# ---------------------------------------------------------------------------

from sam_brain.executor import _run_script, auto_fix_once, run_skill_script, save_skill


def test_run_script_success():
    ok, out, err = _run_script("print('hello')", timeout=10)
    assert ok is True
    assert "hello" in out


def test_run_script_failure():
    ok, out, err = _run_script("raise RuntimeError('boom')", timeout=10)
    assert ok is False
    assert "boom" in err


def test_run_script_timeout():
    ok, out, err = _run_script("import time; time.sleep(60)", timeout=1)
    assert ok is False
    assert "timed out" in err


def test_auto_fix_missing_module():
    error = "ModuleNotFoundError: No module named 'nonexistent_pkg_xyz'"
    result = auto_fix_once("import nonexistent_pkg_xyz", error)
    assert result is not None
    assert result["action"] == "pip_install"
    assert result["package"] == "nonexistent_pkg_xyz"


def test_auto_fix_chrome_path():
    error = "FileNotFoundError: chrome not found"
    script = 'import subprocess\nsubprocess.run(["chrome", "https://example.com"])'
    result = auto_fix_once(script, error)
    assert result is not None
    # Either patches with real chrome.exe path, or falls back to cmd /c start chrome
    assert result["action"] == "patch_script"
    patched = result["patched_script"]
    assert "Program Files" in patched or "cmd" in patched


def test_auto_fix_unknown_error():
    result = auto_fix_once("x = 1", "SomeRandomError: unknown")
    assert result is None


def test_run_skill_script_success(tmp_path):
    script_path = tmp_path / "test_skill.py"
    script_path.write_text("print('skill ran')", encoding="utf-8")
    ok, output = run_skill_script(script_path)
    assert ok is True
    assert "skill ran" in output


def test_run_skill_script_missing_file(tmp_path):
    ok, output = run_skill_script(tmp_path / "nonexistent.py")
    assert ok is False
    assert "Could not read" in output


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


def test_save_skill_update_preserves_run_count(tmp_path):
    root = tmp_path / "sam_skills"
    save_skill("play_yt", "desc", "music", [], "print('v1')", root)
    index = load_index(root)
    index["play_yt"]["run_count"] = 5
    save_index(index, root)

    save_skill("play_yt", "updated desc", "music", ["music"], "print('v2')", root)
    index2 = load_index(root)
    assert index2["play_yt"]["run_count"] == 5


# ---------------------------------------------------------------------------
# Task 4 — LLM script writer, classify, run_and_learn
# ---------------------------------------------------------------------------

from sam_brain.executor import llm_write_script, patch_skill, run_and_learn, _classify_execute_goal


def test_llm_write_script_returns_code(monkeypatch):
    import sam_brain.executor as ex

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": "```python\nprint('hello')\n```"},
    )

    result = llm_write_script("say hello", _make_llm())
    assert "print" in result


def test_patch_skill_swaps_detail(monkeypatch):
    import sam_brain.executor as ex

    base = "subprocess.run(['cmd','/c','start','chrome','https://music.youtube.com'])"
    patched_code = "subprocess.run(['cmd','/c','start','chrome','https://music.youtube.com/playlist?list=lofi'])"

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": f"```python\n{patched_code}\n```"},
    )

    result = patch_skill(base, "play lofi playlist on youtube music", _make_llm())
    assert "lofi" in result or "playlist" in result


def test_classify_execute_goal_vision(monkeypatch):
    import sam_brain.executor as ex

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {
            "response": '{"task_type": "vision", "music_title": "", "domain": "system", "is_unprompted_play": false}'
        },
    )

    result = _classify_execute_goal("what can you see on my screen?", _make_llm())
    assert result["task_type"] == "vision"
    assert result["domain"] == "system"


def test_classify_execute_goal_music_play(monkeypatch):
    import sam_brain.executor as ex

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {
            "response": '{"task_type": "music_play", "music_title": "lofi hip hop", "domain": "music", "is_unprompted_play": false}'
        },
    )

    result = _classify_execute_goal("play some lofi hip hop", _make_llm())
    assert result["task_type"] == "music_play"
    assert result["music_title"] == "lofi hip hop"


def test_classify_execute_goal_unprompted_play(monkeypatch):
    import sam_brain.executor as ex

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {
            "response": '{"task_type": "music_play", "music_title": "", "domain": "music", "is_unprompted_play": true}'
        },
    )

    result = _classify_execute_goal("play something", _make_llm())
    assert result["is_unprompted_play"] is True


def test_classify_execute_goal_fallback_on_error(monkeypatch):
    import sam_brain.executor as ex

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    result = _classify_execute_goal("do something", _make_llm())
    assert result["task_type"] == "script"


def test_run_and_learn_saves_on_success(monkeypatch, tmp_path):
    import sam_brain.executor as ex

    monkeypatch.setattr(ex, "SKILLS_ROOT", tmp_path / "sam_skills")
    monkeypatch.setattr(ex, "_run_script", lambda code, timeout=30: (True, "done", ""))
    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {
            "response": '{"task_type": "script", "music_title": "", "domain": "general", "is_unprompted_play": false}'
        },
    )

    ok, output = run_and_learn("print('done')", "open browser", _make_llm(), tmp_path / "sam_skills")
    assert ok is True
    index = ex.load_index(tmp_path / "sam_skills")
    assert len(index) == 1


def test_run_and_learn_auto_fixes_pip(monkeypatch, tmp_path):
    import sam_brain.executor as ex

    skills_root = tmp_path / "sam_skills"
    monkeypatch.setattr(ex, "SKILLS_ROOT", skills_root)

    calls = []

    def fake_run(code, timeout=30):
        calls.append(code)
        if len(calls) == 1:
            return False, "", "ModuleNotFoundError: No module named 'mss'"
        return True, "captured", ""

    monkeypatch.setattr(ex, "_run_script", fake_run)

    pip_calls = []
    monkeypatch.setattr(ex, "_pip_install", lambda pkg: pip_calls.append(pkg))
    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {
            "response": '{"task_type": "script", "domain": "system", "music_title": "", "is_unprompted_play": false}'
        },
    )

    ok, output = run_and_learn("import mss\nprint('hi')", "screenshot", _make_llm(), skills_root)
    assert ok is True
    assert "mss" in pip_calls
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Task 5 — Vision helpers
# ---------------------------------------------------------------------------

from sam_brain.executor import _describe_with_ollama, desktop_act


def test_describe_with_ollama_success(monkeypatch, tmp_path):
    import sam_brain.executor as ex
    from PIL import Image

    img_path = tmp_path / "screen.png"
    Image.new("RGB", (640, 480), color=(30, 30, 30)).save(img_path)

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": "I can see a Chrome browser showing YouTube Music."},
    )

    result = _describe_with_ollama(img_path, _make_llm())
    assert "Chrome" in result or "YouTube" in result


def test_describe_with_ollama_falls_back_to_local(monkeypatch, tmp_path):
    import sam_brain.executor as ex
    from PIL import Image

    img_path = tmp_path / "screen.png"
    Image.new("RGB", (100, 100), color=(128, 128, 128)).save(img_path)

    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: (_ for _ in ()).throw(RuntimeError("no vision")),
    )

    result = _describe_with_ollama(img_path, _make_llm())
    assert isinstance(result, str) and len(result) > 0


def _mock_capture(output_path):
    """Write a blank PNG to output_path and return it (mirrors real _capture_screen)."""
    from PIL import Image
    Image.new("RGB", (1920, 1080), color=(0, 0, 0)).save(output_path)
    return output_path


def test_desktop_act_click(monkeypatch):
    import sam_brain.executor as ex

    monkeypatch.setattr(ex, "_capture_screen", _mock_capture)
    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": '{"action": "click", "x": 960, "y": 540}'},
    )

    clicks = []
    monkeypatch.setattr(ex, "_pyautogui_click", lambda x, y: clicks.append((x, y)))

    ok, msg = desktop_act("click the pause button", _make_llm())
    assert ok is True
    assert clicks == [(960, 540)]
    assert "Clicked" in msg


def test_desktop_act_key_press(monkeypatch):
    import sam_brain.executor as ex

    monkeypatch.setattr(ex, "_capture_screen", _mock_capture)
    monkeypatch.setattr(
        ex, "_ollama_generate",
        lambda url, payload: {"response": '{"action": "key", "key": "space"}'},
    )

    keys = []
    monkeypatch.setattr(ex, "_pyautogui_press", lambda k: keys.append(k))

    ok, msg = desktop_act("press space to pause", _make_llm())
    assert ok is True
    assert "space" in keys
    assert "Pressed" in msg
