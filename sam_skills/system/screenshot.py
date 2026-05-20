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
