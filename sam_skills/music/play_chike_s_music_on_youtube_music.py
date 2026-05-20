#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time

# Optional imports for further automation (not strictly required for opening the page)
try:
    import pyautogui  # noqa: F401
except Exception:
    pass
try:
    import mss  # noqa: F401
except Exception:
    pass


def open_youtube_music():
    url = "https://music.youtube.com/search?q=Chike"
    try:
        # Launch Chrome with the given URL using the Windows command interpreter
        subprocess.run(['cmd', '/c', 'start', 'chrome', url], check=True)

        # Small pause to give Chrome a moment to start (adjust if needed)
        time.sleep(5)

        print("YouTube Music opened successfully in Chrome.")
    except subprocess.CalledProcessError as cpe:
        print(f"Subprocess error: {cpe}", file=sys.stderr)
    except Exception as exc:
        print(f"An unexpected error occurred: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        open_youtube_music()
    except Exception as e:
        print(f"Failed to run script: {e}", file=sys.stderr)