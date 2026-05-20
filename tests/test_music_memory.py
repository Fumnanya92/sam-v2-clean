"""Tests for music preference memory functions in sam_brain/memory.py."""
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


def test_get_music_prefs_empty(mem):
    prefs = get_music_prefs(mem)
    assert prefs["playlist"] == []
    assert prefs["liked"] == []
    assert prefs["disliked"] == []


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


def test_save_music_pref_tracks_multiple(mem):
    save_music_pref("lofi", "play_lofi", mem)
    save_music_pref("jazz", "play_jazz", mem)
    prefs = get_music_prefs(mem)
    titles = [p["title"] for p in prefs["playlist"]]
    assert "lofi" in titles
    assert "jazz" in titles


def test_save_music_pref_empty_title_is_noop(mem):
    save_music_pref("", "play_lofi", mem)
    prefs = get_music_prefs(mem)
    assert prefs["playlist"] == []


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
    assert liked[0]["title"] == "lofi"


def test_get_liked_music_excludes_not_liked(mem):
    save_music_pref("lofi", "play_lofi", mem)
    save_music_pref("jazz", "play_jazz", mem)
    mark_music_liked("jazz", mem)

    liked = get_liked_music(mem)
    titles = [p["title"] for p in liked]
    assert "jazz" in titles
    assert "lofi" not in titles
