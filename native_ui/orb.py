"""Sam v2 — Orb module.

The plasma orb has been replaced by the ambient StatusDot embedded inside
SamPanel. This module preserves OrbWindow as a no-op shim so existing
imports compile without change.

OrbVisual and OrbWindow are kept as empty stubs. StatusDot is re-exported
here for any callers that want the live state indicator directly.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget

from .windows import StatusDot  # noqa: F401 — re-export


class OrbVisual(QWidget):
    """Deprecated stub — visual is now StatusDot inside SamPanel."""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

    def set_state(self, s: str) -> None:
        pass


class OrbWindow(QWidget):
    """Deprecated stub — replaced by SamPanel's CollapsedStrip."""

    clicked = pyqtSignal()
    _SZ = 0

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(0, 0)
        self.visual = OrbVisual(self)

    def set_state(self, s: str) -> None:
        pass

    def animate_to(self, target: QRect) -> None:
        pass

    def show(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def raise_(self) -> None:
        pass
