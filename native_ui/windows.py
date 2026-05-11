"""
Sam v2 native shell windows — bioluminescent HUD redesign.

Visual language: deep-space command interface. Orbitron headers, IBM Plex Mono
body text, single cyan accent, amber for Sam responses, near-black void base.
Panels use angled clip-path corners for a HUD feel, not generic rounded rects.
"""

from __future__ import annotations

import math
import random
from datetime import datetime

from PyQt6.QtCore import (
    QPoint, QRect, QRectF, Qt,
    QPropertyAnimation, QEasingCurve, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics,
    QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QPlainTextEdit, QScrollArea,
    QVBoxLayout, QWidget,
)

# ══════════════════════════════════════════════════════════════════════════════
#  DESIGN TOKENS
# ══════════════════════════════════════════════════════════════════════════════

# colours
_V     = "rgba(2, 6, 14, 255)"          # void — deepest bg
_PANEL = "rgba(4, 11, 24, 252)"         # panel bg
_CARD  = "rgba(6, 16, 34, 245)"         # card / bubble bg
_INPUT = "rgba(5, 13, 28, 248)"         # input field bg
_HOVER = "rgba(10, 28, 52, 230)"        # hover state

_CYN   = "#48c8f0"                       # cyan accent (you-messages, borders, orb)
_AMB   = "#f0c040"                       # amber (Sam messages, working state)
_MINT  = "#38f8c0"                       # mint (success / listening)
_RED   = "#f06060"                       # error / danger
_VIO   = "#a080f8"                       # violet (music)

_B0    = "rgba(72, 200, 240, 0.08)"     # border ghost
_B1    = "rgba(72, 200, 240, 0.18)"     # border dim
_B2    = "rgba(72, 200, 240, 0.32)"     # border lit

_TX    = "#d8f8ff"                       # primary text
_TXD   = "rgba(200, 240, 255, 0.54)"    # muted text
_TXG   = "rgba(120, 200, 230, 0.36)"    # ghost text

# state chip backgrounds
_SBG = {
    "idle":           "rgba(24, 90, 128, 0.55)",
    "ready":          "rgba(12, 90, 60,  0.55)",
    "listening":      "rgba(12, 104, 76, 0.58)",
    "thinking":       "rgba(100, 64,  6, 0.60)",
    "working":        "rgba(6,   60, 118, 0.60)",
    "needs_approval": "rgba(118, 58,  6, 0.68)",
    "failed":         "rgba(100, 14, 14, 0.65)",
    "done":           "rgba(12,  82, 48, 0.58)",
    "success":        "rgba(12,  82, 48, 0.58)",
}

# popup types
_PICO = {
    "task":      "▣",
    "music":     "♫",
    "clipboard": "⧉",
    "note":      "⊡",
    "status":    "◎",
    "approval":  "⚠",
    "error":     "✕",
}
_PCOL = {
    "task": _CYN, "music": _VIO, "clipboard": _MINT,
    "note": _AMB, "status": _CYN, "approval": _AMB, "error": _RED,
}


# ══════════════════════════════════════════════════════════════════════════════
#  FONT HELPERS  (Orbitron for brand/labels, IBM Plex Mono for body)
# ══════════════════════════════════════════════════════════════════════════════

def _f_brand(size: int = 14) -> QFont:
    """Orbitron-style geometric label — falls back to Segoe UI if unavailable."""
    for name in ("Orbitron", "Rajdhani", "Exo 2", "Segoe UI"):
        f = QFont(name, size, QFont.Weight.Light)
        if QFontMetrics(f).averageCharWidth() > 0:
            return f
    return QFont("Segoe UI", size, QFont.Weight.Light)


def _f_mono(size: int = 12) -> QFont:
    """IBM Plex Mono — falls back to Consolas then Courier."""
    for name in ("IBM Plex Mono", "Consolas", "Courier New"):
        f = QFont(name, size)
        if QFontMetrics(f).averageCharWidth() > 0:
            return f
    return QFont("Courier New", size)


def _f_ui(size: int = 11, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    for name in ("Syne", "Exo 2", "Segoe UI"):
        f = QFont(name, size, weight)
        if QFontMetrics(f).averageCharWidth() > 0:
            return f
    return QFont("Segoe UI", size, weight)


# ══════════════════════════════════════════════════════════════════════════════
#  WIDGET PRIMITIVES
# ══════════════════════════════════════════════════════════════════════════════

def _chip(text: str, state: str = "idle") -> QLabel:
    lbl = QLabel(text)
    bg  = _SBG.get(state.lower(), _SBG["idle"])
    lbl.setFont(_f_ui(8, QFont.Weight.Medium))
    lbl.setStyleSheet(
        f"color: {_CYN}; font-size: 9px; letter-spacing: 1.8px;"
        f" background: {bg}; padding: 3px 10px; border-radius: 3px;"
    )
    return lbl


def _btn(text: str, accent: str = _CYN, danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(_f_ui(10, QFont.Weight.Medium))
    if danger:
        b.setStyleSheet(
            f"QPushButton {{ background: rgba(80,18,24,0.92); color: {_RED};"
            "  border: 1px solid rgba(160,40,56,0.30); border-radius: 4px;"
            "  padding: 6px 14px; letter-spacing: 1px; }}"
            "QPushButton:hover { background: rgba(110,24,34,0.96); }"
        )
    else:
        b.setStyleSheet(
            f"QPushButton {{ background: {_CARD}; color: {_TX};"
            f"  border: 1px solid {_B1}; border-radius: 4px;"
            f"  padding: 6px 14px; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: {_HOVER}; border-color: {accent};"
            f"  color: {accent}; }}"
        )
    return b


class _HLine(QFrame):
    def __init__(self, opacity: str = _B1) -> None:
        super().__init__()
        self.setFixedHeight(1)
        self.setStyleSheet(f"background: {opacity}; border: none;")


# ══════════════════════════════════════════════════════════════════════════════
#  IDLE SCENE  — full-screen breathing HUD
# ══════════════════════════════════════════════════════════════════════════════

class IdleSceneWindow(QWidget):
    activated = pyqtSignal()

    _STARS = 140
    _GRID  = 16

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._scan  = 0.0
        self._phase = 0.0
        self._clock_phase = 0.0

        rng = random.Random(13)
        self._stars = [
            (rng.random(), rng.random(), rng.uniform(0.4, 2.0), rng.randint(35, 185))
            for _ in range(self._STARS)
        ]

        t = QTimer(self); t.setInterval(14); t.timeout.connect(self._tick); t.start()

        # clock timer — updates every second
        self._now = datetime.now()
        ct = QTimer(self); ct.setInterval(1000); ct.timeout.connect(self._tick_clock); ct.start()

        # layout — vertically centred text block
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(5)

        self._lbl_time = QLabel(self._time_str())
        self._lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_time.setFont(_f_brand(62))
        self._lbl_time.setStyleSheet(
            f"color: {_TX}; letter-spacing: 6px; background: transparent;"
        )

        self._lbl_date = QLabel(self._date_str())
        self._lbl_date.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_date.setFont(_f_ui(13))
        self._lbl_date.setStyleSheet(
            f"color: {_TXD}; letter-spacing: 3px; background: transparent;"
        )

        sep = QLabel("· · ·")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep.setStyleSheet(f"color: {_TXG}; font-size: 18px; background: transparent;")

        self._lbl_brand = QLabel("S  A  M")
        self._lbl_brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_brand.setFont(_f_brand(38))
        self._lbl_brand.setStyleSheet(
            f"color: {_CYN}; letter-spacing: 14px; background: transparent;"
        )

        self._lbl_sub = QLabel("AUTONOMOUS PERSONAL ASSISTANT  ·  STANDBY")
        self._lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_sub.setFont(_f_ui(10))
        self._lbl_sub.setStyleSheet(
            f"color: {_TXD}; letter-spacing: 3.5px; background: transparent;"
        )

        self._lbl_hint = QLabel("CLICK ANYWHERE OR TAP THE ORB TO ACTIVATE")
        self._lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_hint.setFont(_f_ui(8))
        self._lbl_hint.setStyleSheet(
            f"color: {_TXG}; letter-spacing: 2px; background: transparent;"
        )

        for w in (self._lbl_time, self._lbl_date, sep, self._lbl_brand,
                  self._lbl_sub):
            outer.addWidget(w)
            outer.addSpacing(8)
        outer.addSpacing(20)
        outer.addWidget(self._lbl_hint)
        outer.addStretch(6)

    def _time_str(self) -> str:
        return self._now.strftime("%H:%M:%S")

    def _date_str(self) -> str:
        return self._now.strftime("%A  %d %B %Y").upper()

    def _tick_clock(self) -> None:
        self._now = datetime.now()
        self._lbl_time.setText(self._time_str())
        self._lbl_date.setText(self._date_str())

    def _tick(self) -> None:
        h = max(1, self.height())
        self._scan  = (self._scan + 2.8) % h
        self._phase = (self._phase + 0.014) % (2 * math.pi)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # deep void background
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0,  QColor(0,  3, 10, 255))
        bg.setColorAt(0.38, QColor(2, 10, 24, 255))
        bg.setColorAt(0.70, QColor(1, 16, 32, 255))
        bg.setColorAt(1.0,  QColor(0,  4, 12, 255))
        p.fillRect(self.rect(), bg)

        hz = h * 0.58   # horizon

        # perspective grid
        gc = QColor(36, 170, 210, 18)
        p.setPen(QPen(gc, 0.6))
        vx = w / 2
        for i in range(self._GRID + 1):
            bx = (i / self._GRID) * w
            p.drawLine(int(vx), int(hz), int(bx), h)
        for j in range(1, 12):
            t  = (j / 11) ** 1.85
            y  = hz + t * (h - hz)
            a  = int(6 + 14 * (1 - j / 11))
            p.setPen(QPen(QColor(36, 170, 210, a), 0.45))
            p.drawLine(0, int(y), w, int(y))

        # star field
        p.setPen(Qt.PenStyle.NoPen)
        for sx, sy, ss, sa in self._stars:
            tw = sa + int(28 * math.sin(self._phase * 2.3 + sx * 11.7))
            sc = QColor(195, 235, 255, max(0, min(255, tw)))
            p.setBrush(sc)
            p.drawEllipse(QRectF(sx * w - ss, sy * hz - ss, ss * 2, ss * 2))

        # horizon line glow
        hg = QLinearGradient(0, hz - 24, 0, hz + 24)
        hg.setColorAt(0.0, QColor(0, 0, 0, 0))
        hg.setColorAt(0.5, QColor(42, 188, 250, 28))
        hg.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), hg)

        # scan beam
        sb = QLinearGradient(0, self._scan - 110, 0, self._scan + 110)
        sb.setColorAt(0.0, QColor(0, 0, 0, 0))
        sb.setColorAt(0.5, QColor(58, 208, 255, 14))
        sb.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), sb)

        # subtle corner bracket marks — HUD feel
        blen = 28; bthk = 1.2
        corn = QColor(72, 200, 240, 55)
        p.setPen(QPen(corn, bthk))
        m = 32
        for (x1, y1, dx1, dy1, dx2, dy2) in [
            (m, m,       blen, 0, 0,     blen),      # top-left
            (w-m, m,     -blen, 0, 0,    blen),      # top-right
            (m, h-m,     blen, 0, 0,     -blen),     # bottom-left
            (w-m, h-m,   -blen, 0, 0,    -blen),     # bottom-right
        ]:
            p.drawLine(x1, y1, x1 + dx1, y1 + dy1)
            p.drawLine(x1, y1, x1 + dx2, y1 + dy2)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mouseReleaseEvent(e)


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD  — HUD command panel
# ══════════════════════════════════════════════════════════════════════════════

class _YouBubble(QFrame):
    """Right-aligned user message with cyan left-edge accent."""

    def __init__(self, text: str, ts: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(4)

        meta = QLabel(f"YOU  {ts}")
        meta.setFont(_f_ui(8))
        meta.setAlignment(Qt.AlignmentFlag.AlignRight)
        meta.setStyleSheet(f"color: {_TXG}; letter-spacing: 1.5px; background: transparent;")

        body = QLabel(text)
        body.setWordWrap(True)
        body.setFont(_f_mono(12))
        body.setAlignment(Qt.AlignmentFlag.AlignRight)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(f"color: {_TX}; background: transparent;")

        layout.addWidget(meta)
        layout.addWidget(body)

        self.setStyleSheet(
            f"QFrame {{ background: rgba(8,28,52,0.70);"
            f" border: 1px solid {_B1};"
            f" border-left: 3px solid {_CYN};"
            " border-radius: 0px 10px 10px 0px; }}"
        )


class _SamBubble(QFrame):
    """Left-aligned Sam message with amber top-edge accent."""

    def __init__(self, text: str, ts: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(4)

        meta = QLabel(f"SAM  {ts}")
        meta.setFont(_f_ui(8))
        meta.setStyleSheet(f"color: rgba(240,192,64,0.52); letter-spacing: 1.5px; background: transparent;")

        body = QLabel(text)
        body.setWordWrap(True)
        body.setFont(_f_mono(12))
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(f"color: rgba(255, 236, 180, 0.95); background: transparent;")

        layout.addWidget(meta)
        layout.addWidget(body)

        self.setStyleSheet(
            "QFrame { background: rgba(28, 20, 6, 0.72);"
            f" border: 1px solid rgba(240, 192, 64, 0.16);"
            f" border-top: 2px solid rgba(240, 192, 64, 0.42);"
            " border-radius: 10px 0px 10px 10px; }"
        )


class DashboardWindow(QWidget):
    submitted       = pyqtSignal(str)
    idle_requested  = pyqtSignal()
    close_requested = pyqtSignal()

    _W, _H = 460, 720

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._W, self._H)
        self._drag: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._panel = QFrame()
        self._panel.setStyleSheet(
            f"QFrame {{ background: {_PANEL};"
            f" border: 1px solid {_B1}; border-radius: 6px; }}"
        )
        outer.addWidget(self._panel)

        root = QVBoxLayout(self._panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── top header bar ────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(52)
        header.setStyleSheet(
            f"QFrame {{ background: rgba(3, 9, 20, 255);"
            f" border-bottom: 1px solid {_B1}; border-radius: 0px; }}"
        )
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(18, 0, 12, 0)
        hlay.setSpacing(10)

        # three window-control dots
        dot_row = QHBoxLayout(); dot_row.setSpacing(7)
        for col in ("#ff5f57", "#febc2e", "#28c840"):
            d = QLabel("●")
            d.setStyleSheet(f"color: {col}; font-size: 9px; background: transparent;")
            dot_row.addWidget(d)
        hlay.addLayout(dot_row)
        hlay.addSpacing(10)

        brand = QLabel("SAM")
        brand.setFont(_f_brand(16))
        brand.setStyleSheet(f"color: {_CYN}; letter-spacing: 8px; background: transparent;")
        hlay.addWidget(brand)
        hlay.addStretch()

        self._state_chip = _chip("IDLE", "idle")
        hlay.addWidget(self._state_chip)
        hlay.addSpacing(8)

        close_b = _btn("✕", danger=True)
        close_b.setFixedSize(30, 26)
        close_b.clicked.connect(self.close_requested.emit)
        hlay.addWidget(close_b)
        root.addWidget(header)

        # ── chat scroll area ──────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QScrollBar:vertical {{ background: {_V}; width: 3px; }}"
            f"QScrollBar::handle:vertical {{ background: rgba(72,200,240,0.28); border-radius: 1px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self._cw = QWidget()
        self._cw.setStyleSheet("background: transparent;")
        self._cl = QVBoxLayout(self._cw)
        self._cl.setContentsMargins(16, 14, 16, 14)
        self._cl.setSpacing(10)
        self._cl.addStretch()
        self._scroll.setWidget(self._cw)
        root.addWidget(self._scroll, 1)

        # ── input area ────────────────────────────────────────────────────────
        inp_frame = QFrame()
        inp_frame.setStyleSheet(
            f"QFrame {{ background: rgba(3, 9, 20, 255);"
            f" border-top: 1px solid {_B1}; border-radius: 0px; }}"
        )
        inp_lay = QVBoxLayout(inp_frame)
        inp_lay.setContentsMargins(16, 12, 16, 12)
        inp_lay.setSpacing(10)

        inp_row = QHBoxLayout(); inp_row.setSpacing(8)
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Give Sam an instruction…")
        self.input_line.setMinimumHeight(42)
        self.input_line.setFont(_f_mono(12))
        self.input_line.setStyleSheet(
            f"QLineEdit {{ background: {_INPUT}; color: {_TX};"
            f" border: 1px solid {_B1}; border-radius: 4px;"
            " padding: 8px 14px; }"
            f"QLineEdit:focus {{ border: 1px solid {_B2}; }}"
        )
        self.input_line.returnPressed.connect(self._submit)
        inp_row.addWidget(self.input_line, 1)

        send = _btn("↑", accent=_CYN)
        send.setFixedSize(42, 42)
        send.clicked.connect(self._submit)
        inp_row.addWidget(send)
        inp_lay.addLayout(inp_row)

        bot_row = QHBoxLayout(); bot_row.setSpacing(8)
        standby = _btn("◎  STANDBY", accent=_CYN)
        standby.clicked.connect(self.idle_requested.emit)
        bot_row.addWidget(standby)
        bot_row.addStretch()
        inp_lay.addLayout(bot_row)
        root.addWidget(inp_frame)

        # animation
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(480)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def paintEvent(self, _) -> None:
        """Paint subtle scan-line texture over the panel."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # left edge cyan glow strip
        glow = QLinearGradient(0, 0, 6, 0)
        glow.setColorAt(0.0, QColor(72, 200, 240, 40))
        glow.setColorAt(1.0, QColor(72, 200, 240, 0))
        p.fillRect(0, 0, 6, self.height(), glow)

    # ── public API ────────────────────────────────────────────────────────────

    def set_state(self, state: str) -> None:
        key = state.lower()
        bg  = _SBG.get(key, _SBG["idle"])
        self._state_chip.setText(state.upper())
        self._state_chip.setStyleSheet(
            f"color: {_CYN}; font-size: 9px; letter-spacing: 1.8px;"
            f" background: {bg}; padding: 3px 10px; border-radius: 3px;"
        )

    def append_chat_message(self, speaker: str, text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        if speaker.lower() == "sam":
            bubble = _SamBubble(text, ts)
        else:
            bubble = _YouBubble(text, ts)
        n = self._cl.count()
        self._cl.insertWidget(n - 1, bubble)
        QTimer.singleShot(30, self._end)

    def append_response(self, text: str) -> None:
        self.append_chat_message("Sam", text)

    def animate_to(self, target: QRect) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    # ── private ───────────────────────────────────────────────────────────────

    def _submit(self) -> None:
        t = self.input_line.text().strip()
        if t:
            self.submitted.emit(t)
            self.input_line.clear()

    def _end(self) -> None:
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None
        super().mouseReleaseEvent(e)


# ══════════════════════════════════════════════════════════════════════════════
#  TASK / NOTIFICATION POPUP  — floating HUD card
# ══════════════════════════════════════════════════════════════════════════════

class TaskPopupWindow(QWidget):
    """
    Floating intel card.  Sam summons this to surface live output,
    music info, clipboard contents, notes, approvals, or errors.
    Never visible at launch — only shown when Sam decides.

    API:
        set_popup_type("music")   → changes icon + accent colour
        set_task(title, status, lines)
        set_title / set_status / append_line
    """
    close_requested = pyqtSignal()

    _W, _H = 450, 278

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._W, self._H)

        self._ptype  = "task"
        self._drag: QPoint | None = None
        self._phi    = 0.0
        self._accent = _CYN

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._panel = QFrame()
        self._panel.setStyleSheet(
            f"QFrame {{ background: rgba(2, 8, 18, 252);"
            f" border: 1px solid {_B2}; border-radius: 6px; }}"
        )
        outer.addWidget(self._panel)

        root = QVBoxLayout(self._panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header strip
        hdr_frame = QFrame()
        hdr_frame.setFixedHeight(44)
        hdr_frame.setStyleSheet(
            "QFrame { background: rgba(2, 6, 14, 255);"
            f" border-bottom: 1px solid {_B1}; border-radius: 0px; }}"
        )
        hlay = QHBoxLayout(hdr_frame)
        hlay.setContentsMargins(14, 0, 10, 0)
        hlay.setSpacing(10)

        self._icon = QLabel(_PICO["task"])
        self._icon.setFont(QFont("Segoe UI", 16))
        self._icon.setStyleSheet(f"color: {_CYN}; background: transparent;")
        hlay.addWidget(self._icon)

        self.title_label = QLabel("No active task")
        self.title_label.setFont(_f_brand(12))
        self.title_label.setStyleSheet(f"color: {_TX}; letter-spacing: 1px; background: transparent;")
        hlay.addWidget(self.title_label, 1)

        self.status_chip = _chip("IDLE", "idle")
        hlay.addWidget(self.status_chip)
        hlay.addSpacing(6)

        hide = _btn("HIDE")
        hide.setFixedSize(52, 26)
        hide.clicked.connect(self.close_requested.emit)
        hlay.addWidget(hide)
        root.addWidget(hdr_frame)

        # progress rail
        self._rail = QFrame()
        self._rail.setFixedHeight(2)
        self._rail.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f" stop:0 {_CYN}, stop:1 transparent); border: none;"
        )
        self._rail.hide()
        root.addWidget(self._rail)

        # body log
        self.body_view = QPlainTextEdit()
        self.body_view.setReadOnly(True)
        self.body_view.setFont(_f_mono(11))
        self.body_view.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; color: {_TXD};"
            " border: none; padding: 12px 16px; }"
            "QScrollBar:vertical { background: transparent; width: 2px; }"
            "QScrollBar::handle:vertical { background: rgba(72,200,240,0.22); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        root.addWidget(self.body_view, 1)

        # animation
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(380)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._rail_t = QTimer(self)
        self._rail_t.setInterval(26)
        self._rail_t.timeout.connect(self._pulse)

    def paintEvent(self, _) -> None:
        """Paint top-edge accent glow matching popup type."""
        p = QPainter(self)
        glow = QLinearGradient(0, 0, self.width(), 0)
        c = QColor(self._accent)
        c.setAlpha(80)
        t = QColor(self._accent); t.setAlpha(0)
        glow.setColorAt(0.0, t)
        glow.setColorAt(0.5, c)
        glow.setColorAt(1.0, t)
        p.fillRect(0, 0, self.width(), 3, glow)

    # ── public API ────────────────────────────────────────────────────────────

    def set_popup_type(self, ptype: str) -> None:
        self._ptype  = ptype if ptype in _PICO else "task"
        self._accent = _PCOL.get(self._ptype, _CYN)
        self._icon.setText(_PICO[self._ptype])
        self._icon.setStyleSheet(f"color: {self._accent}; background: transparent;")
        self.update()

    def set_task(self, title: str, status: str, lines: list[str]) -> None:
        self.title_label.setText(title)
        self.set_status(status)
        self.body_view.setPlainText("\n".join(lines).strip())

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_status(self, status: str) -> None:
        key = status.lower()
        bg  = _SBG.get(key, _SBG["idle"])
        self.status_chip.setText(status.upper())
        self.status_chip.setStyleSheet(
            f"color: {_CYN}; font-size: 9px; letter-spacing: 1.8px;"
            f" background: {bg}; padding: 3px 10px; border-radius: 3px;"
        )
        active = key in ("working", "thinking")
        if active:
            self._rail.show(); self._rail_t.start()
        else:
            self._rail.hide(); self._rail_t.stop()

    def append_line(self, line: str) -> None:
        txt = self.body_view.toPlainText().strip()
        self.body_view.setPlainText(f"{txt}\n{line}".strip() if txt else line)
        sb = self.body_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def animate_to(self, target: QRect) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    # ── private ───────────────────────────────────────────────────────────────

    def _pulse(self) -> None:
        self._phi = (self._phi + 0.036) % 1.0
        s1 = max(0.0, self._phi - 0.26)
        s2 = self._phi
        s3 = min(1.0, self._phi + 0.26)
        acc = self._accent
        self._rail.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f" stop:{s1:.2f} transparent, stop:{s2:.2f} {acc},"
            f" stop:{s3:.2f} transparent); border: none;"
        )

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None
        super().mouseReleaseEvent(e)
