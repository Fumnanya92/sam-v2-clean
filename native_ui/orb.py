"""Plasma orb for Sam v2 — bioluminescent HUD edition."""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import (
    QPoint, QRect, QRectF, Qt,
    QPropertyAnimation, QEasingCurve, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget


# ── state palettes: (core_rgb, ring_rgb, glow_rgb, glow_alpha) ────────────────
_PAL = {
    "idle":      ((12,  60, 100), (60, 190, 240), (40, 150, 220),  38),
    "listening": ((8,  110,  82), (48, 240, 172), (24, 210, 145),  52),
    "thinking":  ((110,  68,   6), (240, 188,  44), (210, 148,  18),  55),
    "working":   ((6,   48, 112), (38, 215, 255), (18, 185, 255),  68),
}

_SPEED = {
    "idle":      (0.016, 0.024),
    "listening": (0.048, 0.044),
    "thinking":  (0.032, 0.034),
    "working":   (0.065, 0.056),
}

_GLYPH = {"idle": "◎", "listening": "◉", "thinking": "◈", "working": "◆"}


class _Spark:
    __slots__ = ("a", "rf", "sz", "spd", "al")

    def __init__(self) -> None:
        self.a   = random.uniform(0, math.tau)
        self.rf  = random.uniform(1.08, 1.60)
        self.sz  = random.uniform(0.9, 2.6)
        self.spd = random.uniform(0.003, 0.015) * random.choice((-1, 1))
        self.al  = random.randint(45, 165)


class OrbVisual(QWidget):
    clicked = pyqtSignal()
    _N = 36

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state  = "idle"
        self._ph     = 0.0
        self._rph    = 0.0
        self._sc     = 1.0
        self._tip    = 0
        self._hover  = False
        self._sparks = [_Spark() for _ in range(self._N)]
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        t = QTimer(self); t.setInterval(14); t.timeout.connect(self._tick); t.start()

    def set_state(self, s: str) -> None:
        self._state = s if s in _PAL else "idle"

    def _tick(self) -> None:
        if not self.isVisible():
            return
        spd, amp = _SPEED.get(self._state, (0.016, 0.024))
        self._ph  += spd
        self._rph += spd * 0.62
        self._sc   = 1.0 + math.sin(self._ph) * amp
        for sp in self._sparks:
            sp.a += sp.spd
        want = 210 if self._hover else 0
        self._tip = max(0, min(210, self._tip + (14 if self._tip < want else -14)))
        self.update()

    def enterEvent(self, e) -> None:
        self._hover = True; super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = False; super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rc = self.rect()
        cx = float(rc.center().x())
        cy = float(rc.center().y())
        br = (min(rc.width(), rc.height()) / 2.0) - 20.0
        r  = br * self._sc

        cr, rr, gr, ga = _PAL[self._state]

        def _c(rgb, a=255): return QColor(rgb[0], rgb[1], rgb[2], a)

        # 1 — outer atmospheric glow (two layers for depth)
        for ext, alph in ((r + 52, ga), (r + 28, min(255, ga + 30))):
            p.setPen(Qt.PenStyle.NoPen)
            g = QRadialGradient(cx, cy, ext)
            g.setColorAt(0.0, _c(gr, alph))
            g.setColorAt(1.0, _c(gr, 0))
            p.setBrush(g)
            p.drawEllipse(QRectF(cx - ext, cy - ext, ext * 2, ext * 2))

        # 2 — outer dashed orbital ring
        p.setBrush(Qt.BrushStyle.NoBrush)
        dc = _c(rr, 58 + int(26 * math.sin(self._rph)))
        pen = QPen(dc, 0.9); pen.setStyle(Qt.PenStyle.DashLine); p.setPen(pen)
        o = r + 18
        p.drawEllipse(QRectF(cx - o, cy - o, o * 2, o * 2))

        # 3 — second orbital ring (solid, thinner, opposite phase)
        sc2 = _c(rr, 45 + int(22 * math.sin(self._rph + math.pi)))
        p.setPen(QPen(sc2, 0.6))
        o2 = r + 10
        p.drawEllipse(QRectF(cx - o2, cy - o2, o2 * 2, o2 * 2))

        # 4 — main breathing ring
        rc2 = _c(rr, 118 + int(70 * math.sin(self._ph * 1.3)))
        p.setPen(QPen(rc2, 1.8))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # 5 — spark particles
        p.setPen(Qt.PenStyle.NoPen)
        for sp in self._sparks:
            pr = br * sp.rf
            px = cx + pr * math.cos(sp.a)
            py = cy + pr * math.sin(sp.a)
            pc = _c(rr, sp.al)
            p.setBrush(pc)
            p.drawEllipse(QRectF(px - sp.sz, py - sp.sz, sp.sz * 2, sp.sz * 2))

        # 6 — core body (layered radial fill)
        fill = QRadialGradient(cx - r * 0.18, cy - r * 0.26, r * 1.1)
        fill.setColorAt(0.00, QColor(215, 248, 255, 82))
        fill.setColorAt(0.25, _c(cr, 128))
        fill.setColorAt(0.65, QColor(cr[0] // 2, cr[1] // 2, cr[2] // 2, 188))
        fill.setColorAt(1.00, QColor(2, 8, 18, 224))
        p.setBrush(fill); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # 7 — inner plasma shimmer
        sh = QRadialGradient(cx + r * 0.12, cy + r * 0.14, r * 0.55)
        sh.setColorAt(0.0, _c(rr, 22 + int(18 * math.sin(self._ph * 2.1))))
        sh.setColorAt(1.0, _c(rr, 0))
        p.setBrush(sh)
        p.drawEllipse(QRectF(cx - r * 0.67, cy - r * 0.41, r * 1.2, r * 1.1))

        # 8 — specular highlight
        sp2 = QRadialGradient(cx - r * 0.25, cy - r * 0.34, r * 0.44)
        sp2.setColorAt(0.0, QColor(255, 255, 255, 44))
        sp2.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(sp2)
        p.drawEllipse(QRectF(cx - r * 0.69, cy - r * 0.78, r * 0.80, r * 0.80))

        # 9 — state glyph
        gl = _GLYPH.get(self._state, "◎")
        lc = _c(rr, 188)
        p.setPen(lc)
        f = QFont("Segoe UI", 10, QFont.Weight.Light)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
        p.setFont(f)
        p.drawText(QRectF(cx - r * 0.5, cy - 10, r, 20), Qt.AlignmentFlag.AlignCenter, gl)

        # 10 — hover label pill
        if self._tip > 0:
            tw, th = 84.0, 22.0
            tx = cx - tw / 2; ty = cy + r + 12
            p.setBrush(QColor(4, 12, 26, self._tip))
            bc = _c(rr, self._tip // 2)
            p.setPen(QPen(bc, 0.7))
            p.drawRoundedRect(QRectF(tx, ty, tw, th), 5, 5)
            tc = _c(rr, self._tip)
            p.setPen(tc)
            p.setFont(QFont("Segoe UI", 7, QFont.Weight.Medium))
            p.drawText(QRectF(tx, ty, tw, th), Qt.AlignmentFlag.AlignCenter, self._state.upper())


class OrbWindow(QWidget):
    clicked = pyqtSignal()
    _SZ = 214

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._SZ, self._SZ)
        self.visual = OrbVisual(self)
        self.visual.setGeometry(0, 0, self._SZ, self._SZ)
        self.visual.clicked.connect(self.clicked.emit)
        self._drag: QPoint | None = None
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_state(self, state: str) -> None:
        self.visual.set_state(state)

    def animate_to(self, target: QRect) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

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
