"""
Sabi talks to Sam to test the new execute action.
Single session — history persists between messages.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MEMORY_PATH = Path(r"C:\Users\DELL.COM\Desktop\Darey\sam_v2\workspace\runtime\memory.json")
MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))

from sam_brain.brain import SamBrain

brain = SamBrain(memory_path=MEMORY_PATH)

SEP = "=" * 70

def say(message: str) -> str:
    print(f"\n{SEP}")
    print(f"[SABI]  {message}")
    print(SEP)
    t0 = time.time()
    response = brain.handle(message)
    elapsed = time.time() - t0
    print(f"\n[SAM]  {response}")
    print(f"  ({elapsed:.1f}s)")
    return response


if __name__ == "__main__":
    print("\nSabi is testing Sam's new execute action...\n")

    # 1. Open YouTube Music
    say("open YouTube Music in Chrome")

    # Brief pause so Chrome has a moment to open
    time.sleep(3)

    # 2. Ask Sam what he can see (vision)
    say("what can you see on my screen right now?")

    # 3. Pause the music
    say("pause the music")

    time.sleep(2)

    # 4. Resume the music
    say("resume the music")

    time.sleep(2)

    # 5. Play something generic — should use liked playlist if any, else open YT Music home
    say("play something for me")

    print(f"\n{SEP}")
    print("Test complete.")
    print(SEP)
