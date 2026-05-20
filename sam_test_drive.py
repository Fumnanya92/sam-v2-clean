"""
Sam conversation test driver.
Runs SamBrain directly with the real memory path so context persists.
Each message is sent one at a time and the response is captured.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

# Force UTF-8 output on Windows so emojis don't crash the terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Point to the real runtime directory Sam uses
MEMORY_PATH = Path(r"C:\Users\DELL.COM\Desktop\Darey\sam_v2\workspace\runtime\memory.json")
MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))

from sam_brain.brain import SamBrain

brain = SamBrain(memory_path=MEMORY_PATH)

SEPARATOR = "=" * 70

def send(message: str) -> str:
    print(f"\n{SEPARATOR}")
    print(f"[TEST DRIVER -> SAM]  {message}")
    print(SEPARATOR)
    start = time.time()
    response = brain.handle(message)
    elapsed = time.time() - start
    print(f"\n  [{elapsed:.1f}s]")
    print(f"\n[SAM FULL RESPONSE]\n{response}")
    print(SEPARATOR)
    return response


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]).strip()
    if not msg:
        print("Usage: python sam_test_drive.py <message>")
        sys.exit(1)
    send(msg)
