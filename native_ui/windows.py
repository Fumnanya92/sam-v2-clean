"""Sam v2 — PyQt6 faithful conversion of the JS workbench design.

Warm ink-black palette. Ember accent. Left sidebar with thread history.
Single conversation column: user right (subtle box), Sam left (no box).
Inline diffs. Collapsible trace. Approval cards. Thinking dots.
"""

from __future__ import annotations

import html
import math
import re
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF,
    QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient,
    QPainter, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)

# ══════════════════════════════════════════════════════════════════════════════
#  DESIGN TOKENS  — warm ink-black, ember accent
# ══════════════════════════════════════════════════════════════════════════════

_VOID      = "#100d0a"
_BG        = "#15120f"
_S1        = "#1c1814"   # surface-1
_S2        = "#221d18"   # surface-2
_S3        = "#2a241d"   # surface-3
_HOVER_BG  = "#2e2820"

_BORDER    = "#2e2820"
_BSOFT     = "#221d18"
_BSTRONG   = "#3b332a"

_INK       = "#f3ece0"
_INK_SOFT  = "#d8cfbf"
_INK_MUTED = "#9c9282"
_INK_FAINT = "#6a6253"
_INK_GHOST = "#4a4438"

_ACCENT     = "#e9794a"
_ACCENT_INK = "#ffd7c2"

_OK   = "#8aa57a"
_WARN = "#d3a45a"
_BAD  = "#c8533a"

_DIFF_ADD_BG = "rgba(122,159,102,26)"
_DIFF_ADD_FG = "#b6c89e"
_DIFF_REM_BG = "rgba(200,83,58,26)"
_DIFF_REM_FG = "#d99e88"


# ══════════════════════════════════════════════════════════════════════════════
#  FONTS
# ══════════════════════════════════════════════════════════════════════════════

def _f_ui(size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    for name in ("Inter", "Segoe UI Variable", "Segoe UI"):
        f = QFont(name, size, weight)
        if QFontMetrics(f).averageCharWidth() > 0:
            return f
    return QFont("Segoe UI", size, weight)


def _f_serif(size: int = 16, italic: bool = True) -> QFont:
    """Serif italic — used for 'sam' brand and Sam speaker label."""
    for name in ("Palatino Linotype", "Book Antiqua", "Cambria", "Georgia"):
        f = QFont(name, size)
        f.setItalic(italic)
        if QFontMetrics(f).averageCharWidth() > 0:
            return f
    f = QFont("Georgia", size)
    f.setItalic(italic)
    return f


def _f_mono(size: int = 11) -> QFont:
    for name in ("JetBrains Mono", "Consolas", "Courier New"):
        f = QFont(name, size)
        if QFontMetrics(f).averageCharWidth() > 0:
            return f
    return QFont("Consolas", size)


# ══════════════════════════════════════════════════════════════════════════════
#  INLINE RENDERING  — backtick code spans + path links
# ══════════════════════════════════════════════════════════════════════════════

_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s<>'\"]+")
_CODE_RE = re.compile(r"`([^`]+)`")


def _render_inline(text: str) -> str:
    """Convert `code` spans to styled HTML. Escape everything else.

    Code spans are intentionally lightweight — no border, soft background,
    just enough to distinguish from prose without breaking the reading flow.
    The outer <span> sets line-height so wrapped paragraphs breathe.
    """
    parts: list[str] = []
    last = 0
    for m in _CODE_RE.finditer(text):
        if m.start() > last:
            parts.append(html.escape(text[last:m.start()]))
        code = html.escape(m.group(1))
        parts.append(
            f'<code style="font-family: JetBrains Mono, Consolas, monospace;'
            f' font-size: 11.5px; background: rgba(42,36,29,0.9);'
            f' color: {_ACCENT_INK}; padding: 1px 5px; border-radius: 3px;">'
            f'{code}</code>'
        )
        last = m.end()
    if last < len(text):
        parts.append(html.escape(text[last:]))
    body = "".join(parts).replace("\n\n", '<br><br>').replace("\n", " ")
    return f'<p style="margin:0; padding:0; line-height:1.68;">{body}</p>'


def _linkify(text: str) -> str:
    escaped = html.escape(text)
    def repl(m: re.Match) -> str:
        raw = m.group(0).rstrip(".,;:)")
        href = QUrl.fromLocalFile(raw).toString()
        return f'<a href="{html.escape(href)}" style="color:{_ACCENT};text-decoration:none;">{html.escape(raw)}</a>'
    return _PATH_RE.sub(repl, escaped).replace("\n", "<br>")


# ══════════════════════════════════════════════════════════════════════════════
#  SAM DOT  — breathing state indicator (7px)
# ══════════════════════════════════════════════════════════════════════════════

class SamDot(QWidget):
    _R = 3.5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        pad = 8
        sz = int(self._R * 2 + pad * 2)
        self.setFixedSize(sz, sz)
        self._state = "ready"
        self._t = 0.0
        timer = QTimer(self)
        timer.setInterval(16)
        timer.timeout.connect(self._tick)
        timer.start()

    def set_state(self, s: str) -> None:
        self._state = s

    def _tick(self) -> None:
        speeds = {"ready": 0.008, "thinking": 0.038, "awaiting": 0.02}
        self._t = (self._t + speeds.get(self._state, 0.01)) % (2 * math.pi)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        pulse = 0.5 + 0.5 * math.sin(self._t)
        color = _WARN if self._state == "awaiting" else _ACCENT
        r = self._R * (0.88 + 0.12 * pulse)
        # glow
        gr = r * 2.8
        g = QRadialGradient(cx, cy, gr)
        c = QColor(color); c.setAlpha(int(50 * pulse))
        g.setColorAt(0.0, c)
        c2 = QColor(color); c2.setAlpha(0)
        g.setColorAt(1.0, c2)
        p.setBrush(g); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        # core
        alpha = int(160 + 95 * pulse)
        c3 = QColor(color); c3.setAlpha(alpha)
        p.setBrush(c3)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))


# ══════════════════════════════════════════════════════════════════════════════
#  THINKING DOTS  — three bouncing dots while Sam processes
# ══════════════════════════════════════════════════════════════════════════════

class ThinkingDots(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._t = 0
        self.setFixedHeight(24)
        t = QTimer(self); t.setInterval(16); t.timeout.connect(self._tick); t.start()

    def _tick(self) -> None:
        self._t += 1
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        offsets = [0.0, 0.18, 0.36]
        for i, phase in enumerate(offsets):
            v = (math.sin((self._t / 60.0 * 1.1 + phase) * 2 * math.pi) + 1) / 2
            dy = -3.0 * v
            alpha = int(80 + 175 * v)
            x = 4 + i * 13.0
            y = self.height() / 2.0 + dy
            c = QColor(_INK_MUTED); c.setAlpha(alpha)
            p.setBrush(c); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))
        # "thinking…" label
        p.setPen(QColor(_INK_MUTED))
        f = _f_ui(13); p.setFont(f)
        p.drawText(QRectF(50, 0, 100, self.height()), Qt.AlignmentFlag.AlignVCenter, "thinking…")


# ══════════════════════════════════════════════════════════════════════════════
#  TOP BAR
# ══════════════════════════════════════════════════════════════════════════════

class TopBar(QFrame):
    sidebar_toggle = pyqtSignal()
    new_requested  = pyqtSignal()
    model_cycle    = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(46)
        self.setStyleSheet(
            f"QFrame {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f" stop:0 #1a1612, stop:1 {_BG});"
            f" border-bottom: 1px solid {_BORDER}; }}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(0)

        # ── left ──────────────────────────────────────────────────────────────
        left = QHBoxLayout(); left.setSpacing(10)

        hamburger = QPushButton()
        hamburger.setFixedSize(28, 28)
        hamburger.setCursor(Qt.CursorShape.PointingHandCursor)
        hamburger.setToolTip("Toggle history")
        hamburger.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_INK_MUTED};"
            f" border: 1px solid transparent; border-radius: 6px; padding: 0; }}"
            f"QPushButton:hover {{ background: {_S2}; color: {_INK};"
            f" border-color: {_BSOFT}; }}"
        )
        hamburger.setText("≡")
        hamburger.setFont(_f_ui(14))
        hamburger.clicked.connect(self.sidebar_toggle)
        left.addWidget(hamburger)

        brand = QLabel("sam")
        brand.setFont(_f_serif(22))
        brand.setStyleSheet(f"color: {_INK}; background: transparent; letter-spacing: -0.01em;")
        left.addWidget(brand)

        self._dot = SamDot()
        left.addWidget(self._dot)
        left.addStretch()

        # ── center ────────────────────────────────────────────────────────────
        self._crumb = QFrame()
        self._crumb.setStyleSheet(
            f"QFrame {{ background: {_S1}; border: 1px solid {_BSOFT}; border-radius: 6px; }}"
        )
        cl = QHBoxLayout(self._crumb)
        cl.setContentsMargins(10, 5, 10, 5)
        cl.setSpacing(8)

        self._crumb_project = QLabel("sam")
        self._crumb_project.setFont(_f_ui(12, QFont.Weight.Medium))
        self._crumb_project.setStyleSheet(f"color: {_INK_SOFT}; background: transparent;")
        cl.addWidget(self._crumb_project)

        sep = QLabel("/")
        sep.setFont(_f_ui(12))
        sep.setStyleSheet(f"color: {_INK_GHOST}; background: transparent;")
        cl.addWidget(sep)

        self._crumb_title = QLabel("")
        self._crumb_title.setFont(_f_ui(12))
        self._crumb_title.setStyleSheet(f"color: {_INK}; background: transparent;")
        cl.addWidget(self._crumb_title)
        self._crumb.hide()

        # ── right ─────────────────────────────────────────────────────────────
        right = QHBoxLayout(); right.setSpacing(8)
        right.addStretch()

        new_btn = QPushButton("+ new")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setFont(_f_ui(12))
        new_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_INK_SOFT};"
            f" border: 1px solid transparent; border-radius: 6px; padding: 6px 9px; }}"
            f"QPushButton:hover {{ background: {_S2}; color: {_INK};"
            f" border-color: {_BSOFT}; }}"
        )
        new_btn.clicked.connect(self.new_requested)
        right.addWidget(new_btn)

        self._model_pill = QPushButton()
        self._model_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._model_pill.setFont(_f_ui(12))
        self._model_pill.setStyleSheet(
            f"QPushButton {{ background: {_S1}; color: {_INK};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; padding: 5px 4px 5px 9px; }}"
            f"QPushButton:hover {{ border-color: {_BSTRONG}; }}"
        )
        self._model_pill.clicked.connect(self.model_cycle)
        self._set_model_pill("local")
        right.addWidget(self._model_pill)

        # ── assemble ──────────────────────────────────────────────────────────
        left_w = QWidget(); left_w.setLayout(left); left_w.setStyleSheet("background:transparent;")
        right_w = QWidget(); right_w.setLayout(right); right_w.setStyleSheet("background:transparent;")

        lay.addWidget(left_w, 1)
        lay.addWidget(self._crumb)
        lay.addWidget(right_w, 1)

    def _set_model_pill(self, model: str) -> None:
        self._model_pill.setText(f"model  {model.lower()}")

    def set_crumb(self, project: str, title: str) -> None:
        self._crumb_project.setText(project or "sam")
        self._crumb_title.setText(title or "")
        self._crumb.setVisible(bool(title))

    def set_model(self, model: str) -> None:
        self._set_model_pill(model)

    def set_state(self, s: str) -> None:
        self._dot.set_state(s)


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

class HistoryItem(QPushButton):
    def __init__(self, title: str, preview: str, at: str, thread_id: str) -> None:
        super().__init__()
        self.thread_id = thread_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(56)
        self._active = False
        self._title = title
        self._preview = preview
        self._at = at
        self._update_style()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._update_style()

    def _update_style(self) -> None:
        border = _ACCENT if self._active else "transparent"
        bg = _S1 if self._active else "transparent"
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: 0; border-left: 2px solid {border};"
            f" border-radius: 0; text-align: left; padding: 9px 11px; }}"
            f"QPushButton:hover {{ background: {_S1}; }}"
        )

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(11, 0, -8, 0)

        # title
        title_c = QColor(_INK if self._active else _INK_SOFT)
        p.setPen(title_c)
        p.setFont(_f_ui(13, QFont.Weight.Medium))
        title_r = QRectF(rect.left() + 2, rect.top() + 8, rect.width() - 50, 18)
        p.drawText(title_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._title)

        # time
        p.setPen(QColor(_INK_GHOST))
        p.setFont(_f_mono(10))
        time_r = QRectF(rect.right() - 45, rect.top() + 8, 45, 18)
        p.drawText(time_r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._at)

        # preview
        p.setPen(QColor(_INK_FAINT))
        p.setFont(_f_ui(11))
        prev_r = QRectF(rect.left() + 2, rect.top() + 30, rect.width(), 16)
        metrics = QFontMetrics(_f_ui(11))
        preview = metrics.elidedText(self._preview, Qt.TextElideMode.ElideRight,
                                      int(prev_r.width()))
        p.drawText(prev_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   preview)


class Sidebar(QFrame):
    thread_selected = pyqtSignal(str)

    _W = 296

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(self._W)
        self.setStyleSheet(
            f"QFrame {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f" stop:0 #181410, stop:1 #14110e);"
            f" border-right: 1px solid {_BORDER}; }}"
        )
        self._items: list[HistoryItem] = []
        self._active_id: str = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 16, 0, 14)
        root.setSpacing(0)

        # header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(18, 0, 18, 12)
        lbl = QLabel("HISTORY")
        lbl.setFont(_f_ui(10))
        lbl.setStyleSheet(
            f"color: {_INK_FAINT}; letter-spacing: 0.14em; background: transparent;"
        )
        hdr.addWidget(lbl)
        hdr.addStretch()
        self._count_lbl = QLabel("0")
        self._count_lbl.setFont(_f_mono(11))
        self._count_lbl.setStyleSheet(f"color: {_INK_GHOST}; background: transparent;")
        hdr.addWidget(self._count_lbl)
        root.addLayout(hdr)

        # search
        search = QFrame()
        search.setStyleSheet(
            f"QFrame {{ background: {_S1}; border: 1px solid {_BSOFT}; border-radius: 7px; }}"
        )
        search.setFixedHeight(34)
        sl = QHBoxLayout(search)
        sl.setContentsMargins(9, 0, 9, 0)
        sl.setSpacing(8)
        si = QLabel("⌕")
        si.setFont(_f_ui(11))
        si.setStyleSheet(f"color: {_INK_MUTED}; background: transparent;")
        sl.addWidget(si)
        sf = QLineEdit()
        sf.setPlaceholderText("search conversations")
        sf.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {_INK}; }}"
            f"QLineEdit::placeholder {{ color: {_INK_FAINT}; }}"
        )
        sf.setFont(_f_ui(12))
        sl.addWidget(sf, 1)
        kbd = QLabel("⌘K")
        kbd.setFont(_f_mono(10))
        kbd.setStyleSheet(
            f"color: {_INK_FAINT}; background: {_S3}; border: 1px solid {_BORDER};"
            f" border-radius: 3px; padding: 1px 5px;"
        )
        sl.addWidget(kbd)
        marg = QWidget()
        marg.setStyleSheet("background: transparent;")
        ml = QHBoxLayout(marg)
        ml.setContentsMargins(14, 0, 14, 14)
        ml.addWidget(search)
        root.addWidget(marg)

        # list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:vertical {{ background: transparent; width: 6px; }}"
            f"QScrollBar::handle:vertical {{ background: {_S3}; border-radius: 3px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self._list_w = QWidget()
        self._list_w.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(8, 4, 8, 8)
        self._list_lay.setSpacing(0)
        self._list_lay.addStretch()
        self._scroll.setWidget(self._list_w)
        root.addWidget(self._scroll, 1)

        # footer
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {_BSOFT}; border: none;")
        root.addWidget(sep)

        foot = QWidget()
        foot.setStyleSheet("background: transparent;")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(16, 8, 16, 4)
        fl.setSpacing(0)
        root.addWidget(foot)

    def add_group(self, group_name: str, items: list[dict]) -> None:
        glbl = QLabel(group_name.upper())
        glbl.setFont(_f_ui(10))
        glbl.setStyleSheet(
            f"color: {_INK_FAINT}; letter-spacing: 0.12em; background: transparent;"
            f" padding: 6px 10px 8px;"
        )
        n = self._list_lay.count()
        self._list_lay.insertWidget(n - 1, glbl)
        for it in items:
            item = HistoryItem(it["title"], it.get("preview",""), it.get("at",""), it["id"])
            item.clicked.connect(lambda _=False, tid=it["id"]: self._select(tid))
            if it.get("active"):
                item.set_active(True)
                self._active_id = it["id"]
            n = self._list_lay.count()
            self._list_lay.insertWidget(n - 1, item)
            self._items.append(item)
        self._count_lbl.setText(str(len(self._items)))

    def _select(self, tid: str) -> None:
        for item in self._items:
            item.set_active(item.thread_id == tid)
        self._active_id = tid
        self.thread_selected.emit(tid)

    def set_project_root(self, project: str, root: str) -> None:
        pass  # could update footer labels


# ══════════════════════════════════════════════════════════════════════════════
#  CODE DIFF
# ══════════════════════════════════════════════════════════════════════════════

class CodeDiff(QFrame):
    def __init__(self, file: str, line: int, before: str, after: str) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {_S1}; border: 1px solid {_BORDER};"
            f" border-radius: 9px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # header
        hdr = QFrame()
        hdr.setStyleSheet(
            f"QFrame {{ background: {_S2}; border: none;"
            f" border-bottom: 1px solid {_BSOFT};"
            f" border-top-left-radius: 9px; border-top-right-radius: 9px; }}"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 8, 14, 8)
        hl.setSpacing(6)
        file_lbl = QLabel(file)
        file_lbl.setFont(_f_mono(11))
        file_lbl.setStyleSheet(f"color: {_INK_SOFT}; background: transparent;")
        hl.addWidget(file_lbl)
        dot = QLabel("·")
        dot.setFont(_f_mono(11))
        dot.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
        hl.addWidget(dot)
        line_lbl = QLabel(f"line {line}")
        line_lbl.setFont(_f_mono(11))
        line_lbl.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
        hl.addWidget(line_lbl)
        hl.addStretch()
        lay.addWidget(hdr)

        # compute diff lines
        before_lines = before.split("\n") if before else []
        after_lines = after.split("\n") if after else []

        # find trailing context
        ctx = 0
        while (ctx < len(before_lines) and ctx < len(after_lines) and
               before_lines[-(ctx+1)] == after_lines[-(ctx+1)]):
            ctx += 1
        context = before_lines[len(before_lines)-ctx:] if ctx else []
        removed = before_lines[:len(before_lines)-ctx]
        added = after_lines[:len(after_lines)-ctx]

        all_lines = (
            [("-", ln) for ln in removed] +
            [("+", ln) for ln in added] +
            [(" ", ln) for ln in context]
        )

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 6, 0, 6)
        bl.setSpacing(0)

        for kind, ln in all_lines:
            row = QFrame()
            if kind == "-":
                row.setStyleSheet(f"background: {_DIFF_REM_BG}; border: none;")
                gutter_c, text_c = _DIFF_REM_FG, _DIFF_REM_FG
                prefix = "−"
            elif kind == "+":
                row.setStyleSheet(f"background: {_DIFF_ADD_BG}; border: none;")
                gutter_c, text_c = _DIFF_ADD_FG, _DIFF_ADD_FG
                prefix = "+"
            else:
                row.setStyleSheet("background: transparent; border: none;")
                gutter_c, text_c = _INK_FAINT, _INK_MUTED
                prefix = " "
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 0, 14, 0)
            rl.setSpacing(12)
            gl = QLabel(prefix)
            gl.setFont(_f_mono(11))
            gl.setFixedWidth(12)
            gl.setStyleSheet(f"color: {gutter_c}; background: transparent;")
            rl.addWidget(gl)
            tl = QLabel(html.escape(ln) if ln else " ")
            tl.setFont(_f_mono(11))
            tl.setStyleSheet(
                f"color: {text_c}; background: transparent; white-space: pre;"
            )
            tl.setTextFormat(Qt.TextFormat.RichText)
            rl.addWidget(tl, 1)
            bl.addWidget(row)

        lay.addWidget(body)


# ══════════════════════════════════════════════════════════════════════════════
#  INLINE TRACE  — collapsible "show what I did · N steps"
# ══════════════════════════════════════════════════════════════════════════════

class InlineTrace(QWidget):
    def __init__(self, trace: list[dict]) -> None:
        super().__init__()
        self._open = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        n = len(trace)
        self._btn = QPushButton()
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFont(_f_ui(12))
        self._btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_INK_MUTED};"
            f" border: 1px solid {_BSOFT}; border-radius: 6px; padding: 6px 12px;"
            f" text-align: left; }}"
            f"QPushButton:hover {{ background: {_S1}; color: {_INK_SOFT};"
            f" border-color: {_BORDER}; }}"
        )
        self._btn.clicked.connect(self._toggle)
        lay.addWidget(self._btn)

        # trace body
        self._body = QFrame()
        self._body.setStyleSheet(
            f"QFrame {{ background: {_S1}; border: 1px solid {_BSOFT}; border-radius: 8px; }}"
        )
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(4, 8, 4, 8)
        bl.setSpacing(0)

        verbs = {"read": "read", "list": "listed", "search": "searched",
                 "write": "wrote", "run": "ran", "edit": "edited"}

        for i, row in enumerate(trace):
            rw = QWidget()
            rw.setStyleSheet("background: transparent;")
            if i < len(trace) - 1:
                rw.setStyleSheet(
                    f"background: transparent; border-bottom: 1px dashed {_BSOFT};"
                )
            rl = QHBoxLayout(rw)
            rl.setContentsMargins(14, 5, 14, 5)
            rl.setSpacing(12)

            verb = verbs.get(row.get("kind", ""), row.get("kind", ""))
            vl = QLabel(verb.upper())
            vl.setFont(_f_mono(10))
            vl.setFixedWidth(60)
            vl.setStyleSheet(
                f"color: {_ACCENT}; letter-spacing: 0.08em; background: transparent;"
            )
            rl.addWidget(vl)

            tgt = QLabel(row.get("target", ""))
            tgt.setFont(_f_mono(11))
            tgt.setStyleSheet(f"color: {_INK_SOFT}; background: transparent;")
            rl.addWidget(tgt, 1)

            if row.get("note"):
                nl = QLabel(row["note"])
                nl.setFont(_f_mono(11))
                nl.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
                rl.addWidget(nl)

            bl.addWidget(rw)

        self._body.hide()
        lay.addWidget(self._body)
        self._n = n
        self._update_btn()

    def _update_btn(self) -> None:
        chevron = "▾" if self._open else "▸"
        self._btn.setText(f"{chevron}  show what I did  ·  {self._n} step{'s' if self._n != 1 else ''}")

    def _toggle(self) -> None:
        self._open = not self._open
        self._body.setVisible(self._open)
        self._update_btn()


# ══════════════════════════════════════════════════════════════════════════════
#  APPROVAL CARD
# ══════════════════════════════════════════════════════════════════════════════

class ApprovalCard(QFrame):
    approved = pyqtSignal()
    declined = pyqtSignal()

    def __init__(self, approval: dict) -> None:
        super().__init__()
        self._status = "pending"
        self._apply_style()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(0)

        # ── question / summary ────────────────────────────────────────────────
        self._summary_lbl = QLabel(approval.get("summary", ""))
        self._summary_lbl.setFont(_f_ui(14))
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet(f"color: {_INK}; background: transparent;")
        lay.addWidget(self._summary_lbl)
        lay.addSpacing(14)

        # ── thin rule ─────────────────────────────────────────────────────────
        sep1 = QFrame(); sep1.setFixedHeight(1)
        sep1.setStyleSheet(f"background: {_BSOFT}; border: none;")
        lay.addWidget(sep1)
        lay.addSpacing(10)

        # ── action rows ───────────────────────────────────────────────────────
        verb_colors = {
            "edit":   _ACCENT,
            "run":    "#5b8ecc",
            "read":   _INK_MUTED,
            "create": _OK,
        }
        for action in approval.get("actions", []):
            row = QHBoxLayout(); row.setSpacing(12)
            verb = action.get("verb", "edit").lower()
            vc = verb_colors.get(verb, _INK_MUTED)
            vl = QLabel(verb.upper())
            vl.setFont(_f_mono(10))
            vl.setFixedWidth(44)
            vl.setStyleSheet(
                f"color: {vc}; letter-spacing: 0.1em; background: transparent;"
            )
            row.addWidget(vl)
            tl = QLabel(action.get("target", ""))
            tl.setFont(_f_mono(11))
            tl.setStyleSheet(f"color: {_INK_SOFT}; background: transparent;")
            row.addWidget(tl, 1)
            if action.get("note"):
                nl = QLabel(action["note"])
                nl.setFont(_f_ui(11))
                nl.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
                row.addWidget(nl)
            lay.addLayout(row)
            lay.addSpacing(5)

        lay.addSpacing(8)

        # ── thin rule ─────────────────────────────────────────────────────────
        sep2 = QFrame(); sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background: {_BSOFT}; border: none;")
        lay.addWidget(sep2)
        lay.addSpacing(12)

        # ── buttons ───────────────────────────────────────────────────────────
        self._footer = QHBoxLayout()
        self._footer.addStretch()

        self._not_yet = QPushButton("not yet")
        self._not_yet.setCursor(Qt.CursorShape.PointingHandCursor)
        self._not_yet.setFont(_f_ui(12))
        self._not_yet.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_INK_SOFT};"
            f" border: 1px solid {_BSTRONG}; border-radius: 7px; padding: 7px 16px; }}"
            f"QPushButton:hover {{ background: {_S2}; color: {_INK};"
            f" border-color: {_BORDER}; }}"
        )
        self._not_yet.clicked.connect(self._decline)
        self._footer.addWidget(self._not_yet)
        self._footer.addSpacing(8)

        self._go = QPushButton("go ahead")
        self._go.setCursor(Qt.CursorShape.PointingHandCursor)
        self._go.setFont(_f_ui(12, QFont.Weight.Medium))
        self._go.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #1a0d05;"
            f" border: none; border-radius: 7px; padding: 7px 16px; }}"
            f"QPushButton:hover {{ background: #f08050; }}"
        )
        self._go.clicked.connect(self._approve)
        self._footer.addWidget(self._go)
        lay.addLayout(self._footer)

        # status shown after decision
        self._status_lbl = QLabel("")
        self._status_lbl.setFont(_f_ui(12))
        self._status_lbl.hide()
        lay.addWidget(self._status_lbl)

        # kept for _approve/_decline compat
        self._label = QLabel(""); self._label.hide(); lay.addWidget(self._label)
        self._hint  = QLabel(""); self._hint.hide();  lay.addWidget(self._hint)

    def _apply_style(self) -> None:
        if self._status == "pending":
            self.setStyleSheet(
                f"QFrame {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                f" stop:0 rgba(233,121,74,28), stop:0.8 {_S1});"
                f" border: none;"
                f" border-radius: 11px; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame {{ background: {_S1}; border: none;"
                f" border-radius: 11px; }}"
            )

    def _approve(self) -> None:
        self._status = "approved"
        self._apply_style()
        self._not_yet.hide()
        self._go.hide()
        self._summary_lbl.setStyleSheet(f"color: {_INK_MUTED}; background: transparent;")
        self._status_lbl.setText("✓  approved — running now")
        self._status_lbl.setStyleSheet(
            f"color: {_OK}; background: transparent; font-weight: 500;"
        )
        self._status_lbl.show()
        self.approved.emit()

    def _decline(self) -> None:
        self._status = "declined"
        self._apply_style()
        self._not_yet.hide()
        self._go.hide()
        self._summary_lbl.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
        self._status_lbl.setText("○  declined — standing by")
        self._status_lbl.setStyleSheet(
            f"color: {_INK_FAINT}; background: transparent;"
        )
        self._status_lbl.show()
        self.declined.emit()


# ══════════════════════════════════════════════════════════════════════════════
#  USER TURN  — right-aligned, subtle box, "you · time" below
# ══════════════════════════════════════════════════════════════════════════════

class UserTurn(QWidget):
    def __init__(self, text: str, at: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignRight)

        # text box
        box = QFrame()
        box.setStyleSheet(
            f"QFrame {{ background: {_S1}; border: 1px solid {_BSOFT}; border-radius: 10px; }}"
        )
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setFont(_f_ui(14))
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(f"color: {_INK}; background: transparent;")
        bl.addWidget(body)

        # right-align the box
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(box)
        lay.addLayout(row)

        # "you  HH:MM" below, right-aligned
        meta = QHBoxLayout()
        meta.addStretch()
        you = QLabel("you")
        you.setFont(_f_ui(12))
        you.setStyleSheet(f"color: {_INK_MUTED}; letter-spacing: 0.02em; background: transparent;")
        meta.addWidget(you)
        time_lbl = QLabel(at)
        time_lbl.setFont(_f_mono(10))
        time_lbl.setStyleSheet(f"color: {_INK_GHOST}; background: transparent;")
        meta.addWidget(time_lbl)
        lay.addLayout(meta)


# ══════════════════════════════════════════════════════════════════════════════
#  SAM TURN  — left-aligned, no box
# ══════════════════════════════════════════════════════════════════════════════

class SamTurn(QWidget):
    approved = pyqtSignal()
    declined = pyqtSignal()
    path_clicked = pyqtSignal(str)

    def __init__(
        self,
        text: str,
        at: str,
        trace: list[dict] | None = None,
        code_block: dict | None = None,
        approval: dict | None = None,
    ) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # "Sam  11:44" header
        meta_row = QHBoxLayout(); meta_row.setSpacing(8)
        sam_lbl = QLabel("Sam")
        sam_lbl.setFont(_f_ui(14, QFont.Weight.DemiBold))
        sam_lbl.setStyleSheet(f"color: {_INK}; background: transparent;")
        meta_row.addWidget(sam_lbl)
        time_lbl = QLabel(at)
        time_lbl.setFont(_f_mono(10))
        time_lbl.setStyleSheet(
            f"color: {_INK_GHOST}; background: transparent; margin-top: 4px;"
        )
        meta_row.addWidget(time_lbl)
        meta_row.addStretch()
        lay.addLayout(meta_row)

        # body text with inline code — QLabel RichText honours <p line-height>
        if text:
            body = QLabel()
            body.setWordWrap(True)
            body.setFont(_f_ui(14))
            body.setTextFormat(Qt.TextFormat.RichText)
            body.setOpenExternalLinks(False)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setText(_render_inline(text))
            body.setStyleSheet(
                f"color: {_INK_SOFT}; background: transparent;"
            )
            lay.addWidget(body)

        # inline code diff
        if code_block:
            lay.addWidget(CodeDiff(
                code_block.get("file", ""),
                code_block.get("line", 0),
                code_block.get("before", ""),
                code_block.get("after", ""),
            ))

        # trace toggle
        if trace:
            lay.addWidget(InlineTrace(trace))

        # approval card
        if approval:
            card = ApprovalCard(approval)
            card.approved.connect(self.approved)
            card.declined.connect(self.declined)
            lay.addWidget(card)


# ══════════════════════════════════════════════════════════════════════════════
#  THINKING TURN
# ══════════════════════════════════════════════════════════════════════════════

class ThinkingTurn(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        meta_row = QHBoxLayout(); meta_row.setSpacing(8)
        sam_lbl = QLabel("Sam")
        sam_lbl.setFont(_f_ui(14, QFont.Weight.DemiBold))
        sam_lbl.setStyleSheet(f"color: {_INK}; background: transparent;")
        meta_row.addWidget(sam_lbl)
        t = QLabel("now")
        t.setFont(_f_mono(10))
        t.setStyleSheet(f"color: {_INK_GHOST}; background: transparent; margin-top: 3px;")
        meta_row.addWidget(t)
        meta_row.addStretch()
        lay.addLayout(meta_row)

        lay.addWidget(ThinkingDots())


# ══════════════════════════════════════════════════════════════════════════════
#  LIVE ACTIVITY CARD  — streams worker steps, collapses to trace when done
# ══════════════════════════════════════════════════════════════════════════════

class LiveActivityCard(QFrame):
    """
    Shows Sam's internal steps streaming in real time.

    While working: pulsing header + live rows.
    When finish() is called: collapses to an InlineTrace-style toggle.
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[str, str]] = []   # (worker, message)
        self._finished = False
        self._open = False

        self.setStyleSheet(
            f"QFrame {{ background: {_S1}; border: 1px solid {_BSOFT};"
            f" border-radius: 10px; }}"
        )

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ── header ────────────────────────────────────────────────────────────
        self._hdr = QFrame()
        self._hdr.setStyleSheet(
            f"QFrame {{ background: {_S2}; border: none;"
            f" border-top-left-radius: 10px; border-top-right-radius: 10px; }}"
        )
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(10)

        self._dots = ThinkingDots()
        self._dots.setFixedWidth(120)
        hl.addWidget(self._dots)
        hl.addStretch()

        self._hdr_toggle = QPushButton()
        self._hdr_toggle.setFont(_f_ui(12))
        self._hdr_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hdr_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_INK_MUTED};"
            f" border: none; padding: 2px 6px; }}"
            f"QPushButton:hover {{ color: {_INK}; }}"
        )
        self._hdr_toggle.hide()
        self._hdr_toggle.clicked.connect(self._toggle)
        hl.addWidget(self._hdr_toggle)

        self._root.addWidget(self._hdr)

        # ── body — list of step rows ──────────────────────────────────────────
        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 4, 0, 6)
        self._body_lay.setSpacing(0)
        self._root.addWidget(self._body)

    # ── public API ────────────────────────────────────────────────────────────

    def add_line(self, worker: str, message: str) -> None:
        """Add one streaming step row."""
        if self._finished:
            return
        self._rows.append((worker, message))
        self._add_row(worker, message, live=True)

    def finish(self) -> None:
        """Collapse to a toggle. Called when Sam's response arrives."""
        if self._finished:
            return
        self._finished = True
        n = len(self._rows)
        if n == 0:
            self.hide()
            return

        # Replace the dots header with a static collapsed toggle
        self._dots.hide()
        chevron = "▸"
        self._hdr_toggle.setText(
            f"{chevron}  show what I did  ·  {n} step{'s' if n != 1 else ''}"
        )
        self._hdr_toggle.show()
        self._hdr.setStyleSheet(
            f"QFrame {{ background: transparent; border: none;"
            f" border-top-left-radius: 10px; border-top-right-radius: 10px; }}"
        )
        self.setStyleSheet(
            f"QFrame {{ background: transparent; border: none; border-radius: 10px; }}"
        )

        # Rebuild body rows as finished style (no live indicator)
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for w, m in self._rows:
            self._add_row(w, m, live=False)

        # Start collapsed
        self._body.hide()

    # ── private ───────────────────────────────────────────────────────────────

    def _toggle(self) -> None:
        self._open = not self._open
        self._body.setVisible(self._open)
        n = len(self._rows)
        chevron = "▾" if self._open else "▸"
        self._hdr_toggle.setText(
            f"{chevron}  show what I did  ·  {n} step{'s' if n != 1 else ''}"
        )

    def _add_row(self, worker: str, message: str, *, live: bool) -> None:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(16, 3, 16, 3)
        rl.setSpacing(14)

        wl = QLabel(worker.upper()[:10])
        wl.setFont(_f_mono(10))
        wl.setFixedWidth(72)
        color = _ACCENT if live else _INK_MUTED
        wl.setStyleSheet(
            f"color: {color}; letter-spacing: 0.07em; background: transparent;"
        )
        rl.addWidget(wl)

        ml = QLabel(message)
        ml.setFont(_f_ui(12))
        ml.setStyleSheet(f"color: {_INK_SOFT}; background: transparent;")
        ml.setWordWrap(True)
        rl.addWidget(ml, 1)

        self._body_lay.addWidget(row)


# ══════════════════════════════════════════════════════════════════════════════
#  CONVERSATION AREA
# ══════════════════════════════════════════════════════════════════════════════

class ConversationArea(QWidget):
    approved     = pyqtSignal()
    declined     = pyqtSignal()
    path_clicked = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._thinking_widget: ThinkingTurn | None = None

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: {_S3}; border-radius: 4px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        # outer container for centering
        self._outer = QWidget()
        self._outer.setStyleSheet(f"background: {_BG};")
        self._outer_lay = QHBoxLayout(self._outer)
        self._outer_lay.setContentsMargins(0, 0, 0, 0)
        self._outer_lay.setSpacing(0)

        # content — max 760px centered
        self._content = QWidget()
        self._content.setStyleSheet(f"background: {_BG};")
        self._content.setMaximumWidth(760)
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(52, 32, 52, 24)
        self._content_lay.setSpacing(30)

        self._outer_lay.addStretch()
        self._outer_lay.addWidget(self._content, 1)
        self._outer_lay.addStretch()
        self._scroll.setWidget(self._outer)
        self._scroll.verticalScrollBar().rangeChanged.connect(self._on_range_changed)

        outer_lay = QVBoxLayout(self)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.addWidget(self._scroll)

    def set_thread_header(self, title: str, project: str, started: str) -> None:
        # remove old header if any
        if self._content_lay.count() > 0:
            first = self._content_lay.itemAt(0)
            if first and isinstance(first.widget(), QWidget):
                w = first.widget()
                if hasattr(w, "_is_thread_header"):
                    self._content_lay.removeWidget(w)
                    w.deleteLater()

        hdr = QWidget()
        hdr._is_thread_header = True  # type: ignore[attr-defined]
        hdr.setStyleSheet("background: transparent;")
        hl = QVBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 14)
        hl.setSpacing(5)

        title_lbl = QLabel(title)
        title_lbl.setFont(_f_serif(28, italic=False))
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"color: {_INK}; background: transparent; letter-spacing: -0.01em;"
        )
        hl.addWidget(title_lbl)

        sub_row = QHBoxLayout(); sub_row.setSpacing(8)
        for i, txt in enumerate([f"started {started}", "·", f"project {project}"]):
            sl = QLabel(txt)
            sl.setFont(_f_ui(12))
            sl.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
            sub_row.addWidget(sl)
        sub_row.addStretch()
        hl.addLayout(sub_row)

        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {_BSOFT}; border: none;")
        hl.addWidget(sep)

        self._content_lay.insertWidget(0, hdr)

    def add_user_turn(self, text: str, at: str | None = None) -> None:
        self._remove_thinking()
        turn = UserTurn(text, at or datetime.now().strftime("%H:%M"))
        self._content_lay.addWidget(turn)
        self._scroll_end()

    def add_sam_turn(
        self,
        text: str,
        at: str | None = None,
        trace=None,
        code_block=None,
        approval=None,
    ) -> None:
        self._remove_thinking()
        turn = SamTurn(
            text,
            at or datetime.now().strftime("%H:%M"),
            trace=trace,
            code_block=code_block,
            approval=approval,
        )
        turn.approved.connect(self.approved)
        turn.declined.connect(self.declined)
        turn.path_clicked.connect(self.path_clicked)
        self._content_lay.addWidget(turn)
        self._scroll_end()

    def show_thinking(self) -> None:
        if self._thinking_widget is None:
            self._thinking_widget = ThinkingTurn()
            self._content_lay.addWidget(self._thinking_widget)
            self._scroll_end()

    def start_live_activity(self) -> LiveActivityCard:
        """Replace thinking dots with a live activity card. Returns the card."""
        self._remove_thinking()
        card = LiveActivityCard()
        self._content_lay.addWidget(card)
        self._scroll_end()
        return card

    def finish_live_activity(self, card: LiveActivityCard) -> None:
        """Collapse the live card. The widget stays in the layout as a trace."""
        card.finish()

    def _remove_thinking(self) -> None:
        if self._thinking_widget is not None:
            self._content_lay.removeWidget(self._thinking_widget)
            self._thinking_widget.deleteLater()
            self._thinking_widget = None

    def clear(self) -> None:
        self._remove_thinking()
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _scroll_end(self) -> None:
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _on_range_changed(self, _min: int, _max: int) -> None:
        self._scroll.verticalScrollBar().setValue(_max)


# ══════════════════════════════════════════════════════════════════════════════
#  COMPOSER
# ══════════════════════════════════════════════════════════════════════════════

class Composer(QWidget):
    submitted = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        self.setStyleSheet(f"background: {_BG};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # fade gradient at top
        wrap = QWidget()
        wrap.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f" stop:0 rgba(21,18,15,0), stop:0.4 {_BG});"
        )
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 10, 0, 16)

        # centered container
        center = QHBoxLayout()
        center.addStretch()

        self._box = QFrame()
        self._box.setMaximumWidth(760)
        self._box.setStyleSheet(
            f"QFrame {{ background: {_S1}; border: 1px solid {_BORDER}; border-radius: 12px; }}"
        )
        bl = QVBoxLayout(self._box)
        bl.setContentsMargins(14, 12, 14, 10)
        bl.setSpacing(0)

        self._field = QLineEdit()
        self._field.setPlaceholderText("Tell Sam what to do…")
        self._field.setFont(_f_ui(14))
        self._field.setFixedHeight(28)
        self._field.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {_INK}; }}"
            f"QLineEdit::placeholder {{ color: {_INK_FAINT}; }}"
        )
        self._field.returnPressed.connect(self._submit)
        bl.addWidget(self._field)

        # footer
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 8, 0, 0)
        foot.setSpacing(0)

        hints = QHBoxLayout(); hints.setSpacing(6)
        for key, label in [("/", "commands"), ("@", "files")]:
            chip = QWidget()
            chip.setStyleSheet("background: transparent;")
            cl = QHBoxLayout(chip); cl.setContentsMargins(3, 0, 3, 0); cl.setSpacing(5)
            kl = QLabel(key)
            kl.setFont(_f_mono(10))
            kl.setStyleSheet(
                f"color: {_INK_SOFT}; background: {_S3}; border: 1px solid {_BSOFT};"
                f" border-radius: 3px; padding: 1px 5px;"
            )
            cl.addWidget(kl)
            ll = QLabel(label)
            ll.setFont(_f_ui(11))
            ll.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
            cl.addWidget(ll)
            hints.addWidget(chip)
        foot.addLayout(hints)
        foot.addStretch()

        shortcut = QLabel("enter to send")
        shortcut.setFont(_f_ui(11))
        shortcut.setStyleSheet(f"color: {_INK_GHOST}; background: transparent;")
        foot.addWidget(shortcut)
        foot.addSpacing(10)

        self._send = QPushButton("→")
        self._send.setFixedSize(28, 28)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setFont(_f_ui(12))
        self._send.setStyleSheet(
            f"QPushButton {{ background: {_S3}; color: {_INK_SOFT};"
            f" border: 1px solid {_BSTRONG}; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: {_ACCENT}; border-color: {_ACCENT}; color: #1a0d05; }}"
            f"QPushButton:disabled {{ opacity: 0.4; }}"
        )
        self._send.clicked.connect(self._submit)
        foot.addWidget(self._send)

        bl.addLayout(foot)
        center.addWidget(self._box, 1)
        center.addStretch()
        wl.addLayout(center)
        outer.addWidget(wrap)

    def _submit(self) -> None:
        t = self._field.text().strip()
        if t and not self._busy:
            self.submitted.emit(t)
            self._field.clear()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._field.setPlaceholderText(
            "Sam is working — your next message will queue" if busy else "Tell Sam what to do…"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class SamWindow(QWidget):
    submitted    = pyqtSignal(str)
    path_clicked = pyqtSignal(str)
    approved     = pyqtSignal()
    declined     = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sam")
        self.setMinimumSize(800, 560)
        self.setStyleSheet(f"QWidget {{ background: {_BG}; }}")

        self._project = "sam-v2"
        self._started = datetime.now().strftime("%H:%M")
        self._active_thread_title = ""
        self._sidebar_open = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # top bar
        self._topbar = TopBar()
        self._topbar.sidebar_toggle.connect(self._toggle_sidebar)
        self._topbar.new_requested.connect(self._new_session)
        self._topbar.model_cycle.connect(self._cycle_model)
        root.addWidget(self._topbar)

        # main area
        self._main = QWidget()
        self._main.setStyleSheet(f"background: {_BG};")
        ml = QHBoxLayout(self._main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        self._sidebar = Sidebar()
        self._sidebar.thread_selected.connect(self._on_thread_selected)
        ml.addWidget(self._sidebar)

        # workspace
        ws = QVBoxLayout()
        ws.setContentsMargins(0, 0, 0, 0)
        ws.setSpacing(0)

        self._conv = ConversationArea()
        self._conv.approved.connect(self.approved)
        self._conv.declined.connect(self.declined)
        self._conv.path_clicked.connect(self.path_clicked)
        ws.addWidget(self._conv, 1)

        self._composer = Composer()
        self._composer.submitted.connect(self.submitted)
        ws.addWidget(self._composer)

        ws_w = QWidget()
        ws_w.setStyleSheet(f"background: {_BG};")
        ws_w.setLayout(ws)
        ml.addWidget(ws_w, 1)

        root.addWidget(self._main, 1)

        # set default thread header
        self._conv.set_thread_header("New conversation", self._project, self._started)

        # models list for cycling
        self._models = ["local", "claude", "codex"]
        self._model_idx = 0

    # ── public API ────────────────────────────────────────────────────────────

    def set_state(self, s: str) -> None:
        state_map = {"idle": "ready", "listening": "ready",
                     "thinking": "thinking", "working": "thinking"}
        self._topbar.set_state(state_map.get(s, "ready"))
        label = {"thinking": "Sam — Thinking…", "working": "Sam — Working…"}.get(s, "Sam")
        self.setWindowTitle(label)

    def set_model(self, model: str) -> None:
        self._topbar.set_model(model)

    def set_coding_model(self, model: str) -> None:
        self.set_model(model)

    def set_project(self, name: str) -> None:
        self._project = name

    def set_thread_title(self, title: str) -> None:
        self._active_thread_title = title
        self._topbar.set_crumb(self._project, title)
        self._conv.set_thread_header(title, self._project, self._started)

    def add_user_message(self, text: str) -> None:
        # derive a thread title from first message if none set
        if not self._active_thread_title:
            title = (text[:48] + "…") if len(text) > 48 else text
            self.set_thread_title(title)
        self._conv.add_user_turn(text)

    def add_sam_message(
        self,
        text: str,
        trace=None,
        code_block=None,
        approval=None,
    ) -> None:
        self._conv.add_sam_turn(
            text, trace=trace, code_block=code_block, approval=approval
        )

    def show_thinking(self) -> None:
        self._conv.show_thinking()

    def set_input_busy(self, busy: bool) -> None:
        self._composer.set_busy(busy)

    def set_live_status(self, line: str) -> None:
        """Update the window title with a live status line while Sam is working."""
        if line:
            short = (line[:60] + "…") if len(line) > 60 else line
            self.setWindowTitle(f"Sam — {short}")
        else:
            self.setWindowTitle("Sam")

    def append_chat_message(self, speaker: str, text: str) -> None:
        if speaker.lower() == "sam":
            self.add_sam_message(text)
        else:
            self.add_user_message(text)

    def load_sidebar_history(self, groups: list[dict]) -> None:
        """groups = [{"name": "Today", "items": [{"id":..,"title":..,...}]}]"""
        for g in groups:
            self._sidebar.add_group(g["name"], g["items"])

    # ── TaskCard shim — kept for app.py compatibility ─────────────────────────

    def task_card(self) -> "_TaskCardShim":
        return _TaskCardShim(self._conv)

    # ── demo interactions ─────────────────────────────────────────────────────

    def _demo_after_approval(self) -> None:
        """Approval card 'go ahead' → Sam thinks briefly, then reports the test green."""
        self.set_state("thinking")
        self.set_input_busy(True)
        QTimer.singleShot(350, self._conv.show_thinking)
        QTimer.singleShot(2700, self._demo_show_result)

    def _demo_show_result(self) -> None:
        self.set_state("idle")
        self.set_input_busy(False)
        self._conv.add_sam_turn(
            "Done. Patched `auth/middleware.py` at L147 and ran the suite — "
            "`test_login_flow` is green. 1 passed in 6.2s.",
            trace=[
                {"kind": "edit", "target": "auth/middleware.py",
                 "note": "L147 — 1 line changed"},
                {"kind": "run",  "target": "pytest tests/auth/test_login_flow.py -v",
                 "note": "1 passed in 6.2s"},
            ],
        )

    def load_demo_thread(self) -> None:
        """Seed the window with the mock auth-test thread from the JS prototype."""
        self._conv.clear()
        self._project = "sam-v2"
        self._started = "11:42"
        self.set_thread_title("Investigate failing auth test")

        self._sidebar.add_group("Today", [
            {"id": "auth-test",  "title": "Investigate failing auth test",
             "preview": "the test_login_flow test has been red since friday",
             "at": "11:42", "active": True},
            {"id": "stripe",     "title": "Wire up Stripe webhooks",
             "preview": "let's get the subscription.updated handler in",    "at": "09:15"},
            {"id": "warnings",   "title": "Triage build warnings",
             "preview": "cleared the deprecation list down to 4",           "at": "08:02"},
        ])
        self._sidebar.add_group("Yesterday", [
            {"id": "session", "title": "Refactor session middleware",
             "preview": "split the cookie writer out of the redirect path", "at": "Yesterday"},
            {"id": "model",   "title": "Switch coding model to Claude",
             "preview": "set claude as the default for this project",       "at": "Yesterday"},
        ])
        self._sidebar.add_group("Earlier this week", [
            {"id": "bootstrap", "title": "Bootstrap sam-v2 project",
             "preview": "scaffolded folders, wired memory + runtime",       "at": "Mon"},
            {"id": "memory",    "title": "Memory architecture review",
             "preview": "we agreed on the daily_state / long_term split",   "at": "Mon"},
            {"id": "ci",        "title": "Set up CI on the runtime tests",
             "preview": "GitHub Actions, py3.12 matrix, 4m17s green",      "at": "Sun"},
        ])

        self._conv.add_user_turn(
            "the test_login_flow test has been red since friday. can you take a look?",
            at="11:42",
        )
        self._conv.add_sam_turn(
            "Yeah — found it. The test expects a `Set-Cookie` header on the redirect "
            "from `/login/callback`, but as of Friday the middleware only writes the "
            "cookie after the follow-up `/me` call. The cookie writer moved out of "
            "`handle_callback` into a new `attach_session` helper, and `attach_session` "
            "is only invoked by `require_user` — which `/login/callback` doesn't pass through.",
            at="11:44",
            trace=[
                {"kind": "read",   "target": "tests/auth/test_login_flow.py", "note": "76 lines"},
                {"kind": "list",   "target": "auth/",                         "note": "12 files"},
                {"kind": "read",   "target": "auth/middleware.py",            "note": "210 lines"},
                {"kind": "read",   "target": "auth/session.py",               "note": "84 lines"},
                {"kind": "search", "target": "Set-Cookie in auth/**",         "note": "3 matches"},
            ],
            code_block={
                "file": "auth/middleware.py",
                "line": 147,
                "before": '    response.set_cookie("sid", mint_session(user))\n    return response',
                "after":  "    attach_session(response, user)\n    return response",
            },
            approval={
                "kind": "patch+test",
                "summary": "Apply that one-line fix and run the auth test to confirm — okay if I go ahead?",
                "actions": [
                    {"verb": "edit", "target": "auth/middleware.py",
                     "note": "1 line changed at L147"},
                    {"verb": "run",  "target": "pytest tests/auth/test_login_flow.py -v",
                     "note": "~6s"},
                ],
            },
        )

    # ── private ───────────────────────────────────────────────────────────────

    def _toggle_sidebar(self) -> None:
        self._sidebar_open = not self._sidebar_open
        if self._sidebar_open:
            self._sidebar.show()
        else:
            self._sidebar.hide()

    def _new_session(self) -> None:
        self._active_thread_title = ""
        self._started = datetime.now().strftime("%H:%M")
        self._topbar.set_crumb(self._project, "")
        self._conv.clear()
        self._conv.set_thread_header("New conversation", self._project, self._started)

    def _cycle_model(self) -> None:
        self._model_idx = (self._model_idx + 1) % len(self._models)
        self.set_model(self._models[self._model_idx])

    def _on_thread_selected(self, tid: str) -> None:
        pass  # future: load thread from history


class _TaskCardShim:
    """Translates old task-card calls into thinking/status updates on SamWindow."""

    def __init__(self, conv: ConversationArea) -> None:
        self._conv = conv

    def set_title(self, t: str) -> None:
        pass

    def set_status(self, s: str) -> None:
        if s.lower() in ("thinking", "working"):
            self._conv.show_thinking()

    def append_line(self, line: str) -> None:
        pass

    def clear_log(self) -> None:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  BACKWARD-COMPAT SHIMS
# ══════════════════════════════════════════════════════════════════════════════

SamPanel = SamWindow


class DashboardWindow(SamWindow):
    submitted       = pyqtSignal(str)
    idle_requested  = pyqtSignal()
    close_requested = pyqtSignal()
    path_clicked    = pyqtSignal(str)
    _W, _H = 900, 700


class TaskPopupWindow(QWidget):
    close_requested = pyqtSignal()
    path_clicked    = pyqtSignal(str)
    _W, _H = 0, 0

    def set_task(self, *a, **k): pass
    def set_title(self, *a): pass
    def set_status(self, *a): pass
    def append_line(self, *a): pass
    def animate_to(self, *a): pass
    def show(self): pass
    def hide(self): pass
    def raise_(self): pass


class IdleSceneWindow(QWidget):
    activated = pyqtSignal()

    def showFullScreen(self): pass
    def hide(self): pass
