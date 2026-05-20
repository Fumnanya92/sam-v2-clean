# Sam UI Round-Off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 8 targeted UI improvements to Sam's PyQt6 chat window — multiline composer, markdown rendering, error styling, @files picker, Ctrl+K search, empty state prompts, copy button, and reliable auto-scroll.

**Architecture:** All changes are in `native_ui/windows.py` (UI widgets) and `native_ui/app.py` (controller wiring). No new dependencies. New helper classes and functions are inserted into `windows.py` in declaration order. Testable pure functions (`_render_markdown`) get unit tests; widget smoke tests use a shared `QApplication` fixture.

**Tech Stack:** Python 3.13, PyQt6, pytest

---

## File Map

| File | Role |
|---|---|
| `native_ui/windows.py` | All widget changes: `_GrowingInput`, `_render_markdown`, `_EmptyState`, error styling, copy button, Ctrl+K, auto-scroll |
| `native_ui/app.py` | Wire `is_error` from `SamResult` into `add_sam_message` |
| `tests/test_native_ui.py` | Unit tests for `_render_markdown`; smoke tests for new widgets |

---

## Task 1: Auto-scroll fix (rangeChanged)

**Files:**
- Modify: `native_ui/windows.py` — `ConversationArea.__init__` and `_scroll_end`

The current `QTimer.singleShot(60, ...)` fires before layout reflow completes on long messages. Connecting `rangeChanged` fires exactly when the scrollbar range grows — i.e., when new content has been laid out.

- [ ] **Step 1: Add `_on_range_changed` to `ConversationArea`**

In `native_ui/windows.py`, inside `ConversationArea`, add this method after `_scroll_end`:

```python
def _on_range_changed(self, _min: int, _max: int) -> None:
    self._scroll.verticalScrollBar().setValue(_max)
```

- [ ] **Step 2: Connect `rangeChanged` in `ConversationArea.__init__`**

Find the line in `ConversationArea.__init__` that reads:
```python
outer_lay.addWidget(self._scroll)
```

Add the connection immediately after the `self._scroll = QScrollArea()` block is complete (after `self._scroll.setWidget(self._outer)`):

```python
self._scroll.verticalScrollBar().rangeChanged.connect(self._on_range_changed)
```

- [ ] **Step 3: Replace `_scroll_end` with a no-op fallback**

Replace the existing `_scroll_end` method body so it calls the same handler immediately (keeps call sites working without a timer):

```python
def _scroll_end(self) -> None:
    QTimer.singleShot(0, lambda: self._on_range_changed(
        0, self._scroll.verticalScrollBar().maximum()
    ))
```

- [ ] **Step 4: Commit**

```bash
git add native_ui/windows.py
git commit -m "fix: reliable auto-scroll via rangeChanged signal"
```

---

## Task 2: `_GrowingInput` — auto-grow multiline composer

**Files:**
- Modify: `native_ui/windows.py` — add `_GrowingInput` class; update `Composer`
- Test: `tests/test_native_ui.py`

Replace the `QLineEdit` in `Composer` with a `QTextEdit` subclass that expands from 1 line to 5 lines as the user types. `Enter` submits; `Shift+Enter` inserts a newline.

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_native_ui.py`:

```python
import sys
import pytest

@pytest.fixture(scope="session")
def qt_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_growing_input_instantiates(qt_app):
    from native_ui.windows import _GrowingInput
    w = _GrowingInput()
    assert w is not None


def test_growing_input_starts_at_min_height(qt_app):
    from native_ui.windows import _GrowingInput
    w = _GrowingInput()
    assert w.sizeHint().height() == _GrowingInput._MIN_H


def test_composer_instantiates(qt_app):
    from native_ui.windows import Composer
    c = Composer()
    assert c is not None
```

- [ ] **Step 2: Run test — expect ImportError on `_GrowingInput`**

```bash
cd C:\Users\DELL.COM\Desktop\Darey\sam-v2-clean
python -m pytest tests/test_native_ui.py::test_growing_input_instantiates -v
```

Expected: `ImportError` or `FAILED` — `_GrowingInput` not yet defined.

- [ ] **Step 3: Add `_GrowingInput` to `windows.py`**

Add this class immediately before the `Composer` class (around line 1323 in the original file). Also add `QTextOption` to the `PyQt6.QtGui` imports at the top of the file:

```python
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient,
    QPainter, QPen, QRadialGradient, QTextOption,
)
```

Then insert the class:

```python
# ══════════════════════════════════════════════════════════════════════════════
#  GROWING INPUT  — auto-expanding QTextEdit (1–5 lines)
# ══════════════════════════════════════════════════════════════════════════════

class _GrowingInput(QTextEdit):
    submitted = pyqtSignal()

    _MIN_H = 38
    _MAX_H = 38 * 5

    def __init__(self) -> None:
        super().__init__()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setFixedHeight(self._MIN_H)
        self.document().contentsChanged.connect(self._on_contents_changed)

    def sizeHint(self) -> QSize:
        doc_h = int(self.document().size().height()) + 16
        h = max(self._MIN_H, min(doc_h, self._MAX_H))
        return QSize(super().sizeHint().width(), h)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(e)
            else:
                self.submitted.emit()
                e.accept()
                return
        super().keyPressEvent(e)

    def _on_contents_changed(self) -> None:
        hint_h = self.sizeHint().height()
        if self.height() != hint_h:
            self.setFixedHeight(hint_h)
```

- [ ] **Step 4: Update `Composer` to use `_GrowingInput`**

In `Composer.__init__`, replace:

```python
self._field = QLineEdit()
self._field.setPlaceholderText("Tell Sam what to do…")
self._field.setFont(_f_ui(14))
self._field.setFixedHeight(28)
self._field.setStyleSheet(
    f"QLineEdit {{ background: transparent; border: none; color: {_INK}; }}"
    f"QLineEdit::placeholder {{ color: {_INK_FAINT}; }}"
)
self._field.returnPressed.connect(self._submit)
```

with:

```python
self._field = _GrowingInput()
self._field.setPlaceholderText("Tell Sam what to do…")
self._field.setFont(_f_ui(14))
self._field.setStyleSheet(
    f"QTextEdit {{ background: transparent; border: none; color: {_INK}; }}"
    f"QTextEdit QScrollBar:vertical {{ width: 0px; }}"
)
self._field.submitted.connect(self._submit)
```

- [ ] **Step 5: Update `Composer._submit` and `set_busy`**

Replace `_submit`:

```python
def _submit(self) -> None:
    t = self._field.toPlainText().strip()
    if t and not self._busy:
        self.submitted.emit(t)
        self._field.clear()
```

Replace `set_busy`:

```python
def set_busy(self, busy: bool) -> None:
    self._busy = busy
    self._field.setPlaceholderText(
        "Sam is working — your next message will queue" if busy else "Tell Sam what to do…"
    )
```

- [ ] **Step 6: Add `set_text` convenience method to `Composer`**

Add after `set_busy`:

```python
def set_text(self, text: str) -> None:
    self._field.setPlainText(text)
    self._field.setFocus()
    cursor = self._field.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    self._field.setTextCursor(cursor)
```

- [ ] **Step 7: Run tests — expect pass**

```bash
python -m pytest tests/test_native_ui.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add native_ui/windows.py tests/test_native_ui.py
git commit -m "feat: auto-grow multiline composer (_GrowingInput)"
```

---

## Task 3: `_render_markdown` — markdown in Sam messages

**Files:**
- Modify: `native_ui/windows.py` — replace `_render_inline` with `_render_markdown` in `SamTurn`
- Test: `tests/test_native_ui.py`

Pure function — fully unit-testable without a running Qt app.

- [ ] **Step 1: Write failing unit tests**

Append to `tests/test_native_ui.py`:

```python
def test_render_markdown_bold():
    from native_ui.windows import _render_markdown
    result = _render_markdown("**hello world**")
    assert "<b>hello world</b>" in result


def test_render_markdown_italic():
    from native_ui.windows import _render_markdown
    result = _render_markdown("*emphasis*")
    assert "<i>emphasis</i>" in result


def test_render_markdown_h1():
    from native_ui.windows import _render_markdown
    result = _render_markdown("# Big heading")
    assert "Big heading" in result
    assert "font-size:16px" in result


def test_render_markdown_h2():
    from native_ui.windows import _render_markdown
    result = _render_markdown("## Sub heading")
    assert "Sub heading" in result
    assert "font-size:14px" in result


def test_render_markdown_bullet():
    from native_ui.windows import _render_markdown
    result = _render_markdown("- first item")
    assert "first item" in result
    assert "·" in result


def test_render_markdown_code_span():
    from native_ui.windows import _render_markdown
    result = _render_markdown("`my_func()`")
    assert "my_func()" in result
    assert "<code" in result


def test_render_markdown_escapes_html():
    from native_ui.windows import _render_markdown
    result = _render_markdown("<script>alert(1)</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_render_markdown_paragraph_break():
    from native_ui.windows import _render_markdown
    result = _render_markdown("line one\n\nline two")
    assert "<br><br>" in result
```

- [ ] **Step 2: Run tests — expect ImportError on `_render_markdown`**

```bash
python -m pytest tests/test_native_ui.py::test_render_markdown_bold -v
```

Expected: `ImportError` — `_render_markdown` not yet defined.

- [ ] **Step 3: Add `_render_markdown` to `windows.py`**

Add these compiled regexes near the top of the file, after the existing `_PATH_RE` and `_CODE_RE` definitions:

```python
_BOLD_RE   = re.compile(r'\*\*(.+?)\*\*', re.DOTALL)
_ITALIC_RE = re.compile(r'\*(.+?)\*', re.DOTALL)
_H2_RE     = re.compile(r'^## (.+)$', re.MULTILINE)
_H1_RE     = re.compile(r'^# (?!#)(.+)$', re.MULTILINE)
_BULLET_RE = re.compile(r'^[-*] (.+)$', re.MULTILINE)
```

Then add the function immediately after `_render_inline`:

```python
def _render_markdown(text: str) -> str:
    """Convert common markdown to HTML. Safe — HTML is escaped before processing."""
    s = html.escape(text)

    # Headings — H2 before H1 so ## isn't matched by the H1 pattern
    s = _H2_RE.sub(
        lambda m: (
            f'<p style="margin:4px 0 2px 0; padding:0; font-size:14px;'
            f' font-weight:600; color:{_INK_SOFT};">{m.group(1)}</p>'
        ),
        s,
    )
    s = _H1_RE.sub(
        lambda m: (
            f'<p style="margin:6px 0 2px 0; padding:0; font-size:16px;'
            f' font-weight:700; color:{_INK};">{m.group(1)}</p>'
        ),
        s,
    )

    # Bold / italic
    s = _BOLD_RE.sub(r'<b>\1</b>', s)
    s = _ITALIC_RE.sub(r'<i>\1</i>', s)

    # Bullet lines
    s = _BULLET_RE.sub(
        lambda m: (
            f'<p style="margin:1px 0; padding:0; padding-left:14px;">'
            f'<span style="color:{_ACCENT};">·</span> {m.group(1)}</p>'
        ),
        s,
    )

    # Inline code spans (backtick) — m.group(1) is already HTML-escaped, no double-escape
    s = _CODE_RE.sub(
        lambda m: (
            f'<code style="font-family:JetBrains Mono,Consolas,monospace;'
            f' font-size:11.5px; background:rgba(42,36,29,0.9);'
            f' color:{_ACCENT_INK}; padding:1px 5px; border-radius:3px;">'
            f'{m.group(1)}</code>'
        ),
        s,
    )

    # Paragraph breaks and line breaks
    s = s.replace('\n\n', '<br><br>').replace('\n', '<br>')

    return f'<span style="line-height:1.68;">{s}</span>'
```

- [ ] **Step 4: Use `_render_markdown` in `SamTurn`**

In `SamTurn.__init__`, find:

```python
body.setText(_render_inline(text))
```

Replace with:

```python
body.setText(_render_markdown(text))
```

- [ ] **Step 5: Run all tests — expect pass**

```bash
python -m pytest tests/test_native_ui.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add native_ui/windows.py tests/test_native_ui.py
git commit -m "feat: markdown rendering in Sam messages (_render_markdown)"
```

---

## Task 4: Error state styling on `SamTurn`

**Files:**
- Modify: `native_ui/windows.py` — `SamTurn`, `ConversationArea.add_sam_turn`, `SamWindow.add_sam_message`
- Modify: `native_ui/app.py` — `_on_done`
- Test: `tests/test_native_ui.py`

- [ ] **Step 1: Write failing smoke test**

Append to `tests/test_native_ui.py`:

```python
def test_sam_turn_error_instantiates(qt_app):
    from native_ui.windows import SamTurn
    turn = SamTurn("Something went wrong", "12:00", is_error=True)
    assert turn is not None


def test_sam_turn_normal_instantiates(qt_app):
    from native_ui.windows import SamTurn
    turn = SamTurn("All good", "12:00", is_error=False)
    assert turn is not None
```

- [ ] **Step 2: Run test — expect TypeError (unexpected keyword `is_error`)**

```bash
python -m pytest tests/test_native_ui.py::test_sam_turn_error_instantiates -v
```

Expected: `TypeError` — `is_error` not in signature.

- [ ] **Step 3: Add `is_error` to `SamTurn`**

In `SamTurn.__init__`, change the signature from:

```python
def __init__(
    self,
    text: str,
    at: str,
    trace: list[dict] | None = None,
    code_block: dict | None = None,
    approval: dict | None = None,
) -> None:
```

to:

```python
def __init__(
    self,
    text: str,
    at: str,
    trace: list[dict] | None = None,
    code_block: dict | None = None,
    approval: dict | None = None,
    is_error: bool = False,
) -> None:
```

At the very top of `__init__` body, store raw text (needed by copy button in Task 8 too):

```python
self._raw_text = text
```

Find the `sam_lbl` setup block:

```python
sam_lbl = QLabel("Sam")
sam_lbl.setFont(_f_ui(14, QFont.Weight.DemiBold))
sam_lbl.setStyleSheet(f"color: {_INK}; background: transparent;")
```

Replace with:

```python
sam_lbl = QLabel("Sam")
sam_lbl.setFont(_f_ui(14, QFont.Weight.DemiBold))
_sam_lbl_color = _BAD if is_error else _INK
sam_lbl.setStyleSheet(f"color: {_sam_lbl_color}; background: transparent;")
```

Find the `body` text label setup:

```python
body.setStyleSheet(
    f"color: {_INK_SOFT}; background: transparent;"
)
```

Replace with:

```python
_body_color = _BAD if is_error else _INK_SOFT
body.setStyleSheet(f"color: {_body_color}; background: transparent;")
```

At the end of `__init__`, add the left border when `is_error`:

```python
if is_error:
    self.setStyleSheet(
        f"border-left: 3px solid {_BAD}; padding-left: 10px;"
    )
```

- [ ] **Step 4: Propagate `is_error` through `ConversationArea.add_sam_turn`**

Change the signature of `add_sam_turn` in `ConversationArea`:

```python
def add_sam_turn(
    self,
    text: str,
    at: str | None = None,
    trace=None,
    code_block=None,
    approval=None,
    is_error: bool = False,
) -> None:
    self._remove_thinking()
    turn = SamTurn(
        text,
        at or datetime.now().strftime("%H:%M"),
        trace=trace,
        code_block=code_block,
        approval=approval,
        is_error=is_error,
    )
    turn.approved.connect(self.approved)
    turn.declined.connect(self.declined)
    turn.path_clicked.connect(self.path_clicked)
    self._content_lay.addWidget(turn)
    self._scroll_end()
```

- [ ] **Step 5: Propagate `is_error` through `SamWindow.add_sam_message`**

Change the signature of `add_sam_message` in `SamWindow`:

```python
def add_sam_message(
    self,
    text: str,
    trace=None,
    code_block=None,
    approval=None,
    is_error: bool = False,
) -> None:
    self._conv.add_sam_turn(
        text, trace=trace, code_block=code_block, approval=approval, is_error=is_error
    )
```

- [ ] **Step 6: Wire `is_error` in `app.py` `_on_done`**

In `native_ui/app.py`, find:

```python
self.window.add_sam_message(reply)
```

Replace with:

```python
self.window.add_sam_message(reply, is_error=not result.ok)
```

- [ ] **Step 7: Run tests — expect pass**

```bash
python -m pytest tests/test_native_ui.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add native_ui/windows.py native_ui/app.py tests/test_native_ui.py
git commit -m "feat: error state styling on Sam turns (red tint + left border)"
```

---

## Task 5: `@files` chip wired up

**Files:**
- Modify: `native_ui/windows.py` — `Composer.__init__`, add `_on_at_clicked` and `insert_at_file`

`QFileDialog` is already available via `PyQt6.QtWidgets`. Add `QFileDialog` to the existing import.

- [ ] **Step 1: Add `QFileDialog` to imports**

In `windows.py`, find:

```python
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)
```

Replace with:

```python
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)
```

- [ ] **Step 2: Replace the `@` chip with a clickable `QPushButton`**

In `Composer.__init__`, find the hint chip loop:

```python
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
```

Replace with:

```python
hints = QHBoxLayout(); hints.setSpacing(6)

# "/" chip — decorative only
slash_chip = QWidget()
slash_chip.setStyleSheet("background: transparent;")
sc = QHBoxLayout(slash_chip); sc.setContentsMargins(3, 0, 3, 0); sc.setSpacing(5)
sk = QLabel("/")
sk.setFont(_f_mono(10))
sk.setStyleSheet(
    f"color: {_INK_SOFT}; background: {_S3}; border: 1px solid {_BSOFT};"
    f" border-radius: 3px; padding: 1px 5px;"
)
sc.addWidget(sk)
sl = QLabel("commands")
sl.setFont(_f_ui(11))
sl.setStyleSheet(f"color: {_INK_FAINT}; background: transparent;")
sc.addWidget(sl)
hints.addWidget(slash_chip)

# "@" chip — opens file picker
at_btn = QPushButton("@ files")
at_btn.setFont(_f_ui(11))
at_btn.setCursor(Qt.CursorShape.PointingHandCursor)
at_btn.setStyleSheet(
    f"QPushButton {{ background: transparent; color: {_INK_FAINT};"
    f" border: 1px solid {_BSOFT}; border-radius: 4px; padding: 2px 8px; }}"
    f"QPushButton:hover {{ color: {_INK_SOFT}; border-color: {_BORDER}; }}"
)
at_btn.clicked.connect(self._on_at_clicked)
hints.addWidget(at_btn)
```

- [ ] **Step 3: Add `_on_at_clicked` and `insert_at_file` to `Composer`**

Add these methods to `Composer`:

```python
def _on_at_clicked(self) -> None:
    path, _ = QFileDialog.getOpenFileName(self, "Attach file")
    if path:
        self.insert_at_file(path)

def insert_at_file(self, path: str) -> None:
    current = self._field.toPlainText()
    sep = " " if current and not current.endswith(" ") else ""
    self._field.setPlainText(current + sep + f"@{path} ")
    self._field.setFocus()
    cursor = self._field.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    self._field.setTextCursor(cursor)
```

- [ ] **Step 4: Run existing tests — expect pass**

```bash
python -m pytest tests/test_native_ui.py -v
```

Expected: all tests PASS (no regression).

- [ ] **Step 5: Commit**

```bash
git add native_ui/windows.py
git commit -m "feat: @ chip opens file picker and inserts path into composer"
```

---

## Task 6: Ctrl+K focuses sidebar search

**Files:**
- Modify: `native_ui/windows.py` — `Sidebar.__init__` (promote `_search_field`), `SamWindow.__init__` (add shortcut)

- [ ] **Step 1: Promote `_search_field` in `Sidebar`**

In `Sidebar.__init__`, find:

```python
sf = QLineEdit()
sf.setPlaceholderText("search conversations")
sf.setStyleSheet(
    f"QLineEdit {{ background: transparent; border: none; color: {_INK}; }}"
    f"QLineEdit::placeholder {{ color: {_INK_FAINT}; }}"
)
sf.setFont(_f_ui(12))
sl.addWidget(sf, 1)
```

Replace `sf` with `self._search_field` throughout:

```python
self._search_field = QLineEdit()
self._search_field.setPlaceholderText("search conversations")
self._search_field.setStyleSheet(
    f"QLineEdit {{ background: transparent; border: none; color: {_INK}; }}"
    f"QLineEdit::placeholder {{ color: {_INK_FAINT}; }}"
)
self._search_field.setFont(_f_ui(12))
sl.addWidget(self._search_field, 1)
```

- [ ] **Step 2: Add `QKeySequence` and `QShortcut` imports**

In `windows.py`, find the `PyQt6.QtGui` import block and add `QKeySequence`, `QShortcut`:

```python
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QKeySequence, QLinearGradient,
    QPainter, QPen, QRadialGradient, QShortcut, QTextOption,
)
```

- [ ] **Step 3: Add shortcut in `SamWindow.__init__`**

In `SamWindow.__init__`, after `self._models = ["local", "claude", "codex"]` at the end:

```python
QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self._focus_search)
```

- [ ] **Step 4: Add `_focus_search` method to `SamWindow`**

Add in the `# ── private ──` section of `SamWindow`:

```python
def _focus_search(self) -> None:
    if not self._sidebar_open:
        self._toggle_sidebar()
    self._sidebar._search_field.setFocus()
```

- [ ] **Step 5: Run existing tests — expect pass**

```bash
python -m pytest tests/test_native_ui.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add native_ui/windows.py
git commit -m "feat: Ctrl+K shortcut focuses sidebar search"
```

---

## Task 7: `_EmptyState` — starter prompts

**Files:**
- Modify: `native_ui/windows.py` — add `_EmptyState` class; update `ConversationArea` and `SamWindow`
- Test: `tests/test_native_ui.py`

- [ ] **Step 1: Write failing smoke test**

Append to `tests/test_native_ui.py`:

```python
def test_empty_state_instantiates(qt_app):
    from native_ui.windows import _EmptyState
    w = _EmptyState()
    assert w is not None


def test_conversation_area_shows_empty_state(qt_app):
    from native_ui.windows import ConversationArea
    ca = ConversationArea()
    assert ca._empty_state is not None
```

- [ ] **Step 2: Run test — expect ImportError**

```bash
python -m pytest tests/test_native_ui.py::test_empty_state_instantiates -v
```

Expected: `ImportError` — `_EmptyState` not yet defined.

- [ ] **Step 3: Add `_EmptyState` class to `windows.py`**

Insert this class immediately before `ConversationArea`:

```python
# ══════════════════════════════════════════════════════════════════════════════
#  EMPTY STATE  — starter prompts shown when conversation is empty
# ══════════════════════════════════════════════════════════════════════════════

class _EmptyState(QWidget):
    prompt_selected = pyqtSignal(str)

    _PROMPTS = [
        "Build a new project",
        "Fix a bug in my code",
        "Explain a file",
        "Run my tests",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 80, 0, 0)
        lay.setSpacing(24)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        heading = QLabel("What can Sam do?")
        heading.setFont(_f_serif(22, italic=False))
        heading.setStyleSheet(f"color: {_INK_MUTED}; background: transparent;")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(heading)

        chips_row = QWidget()
        chips_row.setStyleSheet("background: transparent;")
        cl = QHBoxLayout(chips_row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        cl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        for prompt in self._PROMPTS:
            btn = QPushButton(prompt)
            btn.setFont(_f_ui(13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedSize(160, 64)
            btn.setStyleSheet(
                f"QPushButton {{ background: {_S1}; color: {_INK_SOFT};"
                f" border: 1px solid {_BSOFT}; border-radius: 9px; padding: 10px 12px;"
                f" text-align: center; }}"
                f"QPushButton:hover {{ background: {_S2}; border-color: {_BSTRONG};"
                f" color: {_INK}; }}"
            )
            btn.clicked.connect(lambda _, p=prompt: self.prompt_selected.emit(p))
            cl.addWidget(btn)

        lay.addWidget(chips_row)
        lay.addStretch()
```

- [ ] **Step 4: Update `ConversationArea` to manage `_EmptyState`**

In `ConversationArea.__init__`, add `self._empty_state: _EmptyState | None = None` at the start of the method, then call `self._show_empty_state()` at the very end (after `outer_lay.addWidget(self._scroll)`):

```python
def __init__(self) -> None:
    super().__init__()
    self._thinking_widget: ThinkingTurn | None = None
    self._empty_state: _EmptyState | None = None   # ← ADD THIS LINE

    # ... existing scroll/content setup unchanged ...

    self._show_empty_state()   # ← ADD AT END
```

Add these three methods to `ConversationArea`:

```python
def _show_empty_state(self) -> None:
    if self._empty_state is None:
        self._empty_state = _EmptyState()
        self._content_lay.insertWidget(0, self._empty_state)

def _remove_empty_state(self) -> None:
    if self._empty_state is not None:
        self._content_lay.removeWidget(self._empty_state)
        self._empty_state.deleteLater()
        self._empty_state = None

def connect_empty_state(self, slot) -> None:
    """Called by SamWindow to wire prompt_selected → composer."""
    if self._empty_state is not None:
        self._empty_state.prompt_selected.connect(slot)
```

In `ConversationArea.add_user_turn`, add `self._remove_empty_state()` as the first line:

```python
def add_user_turn(self, text: str, at: str | None = None) -> None:
    self._remove_empty_state()    # ← ADD
    self._remove_thinking()
    turn = UserTurn(text, at or datetime.now().strftime("%H:%M"))
    self._content_lay.addWidget(turn)
    self._scroll_end()
```

In `ConversationArea.clear`, call `_remove_empty_state()` then re-show it:

```python
def clear(self) -> None:
    self._remove_thinking()
    self._remove_empty_state()
    while self._content_lay.count():
        item = self._content_lay.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
    self._show_empty_state()
```

- [ ] **Step 5: Wire the signal in `SamWindow`**

In `SamWindow.__init__`, after `self._conv = ConversationArea()` wiring:

```python
self._conv.connect_empty_state(self._composer.set_text)
```

Also update `_new_session` in `SamWindow` to re-wire after clear (since `clear` creates a new `_EmptyState`):

```python
def _new_session(self) -> None:
    self._active_thread_title = ""
    self._started = datetime.now().strftime("%H:%M")
    self._topbar.set_crumb(self._project, "")
    self._conv.clear()
    self._conv.set_thread_header("New conversation", self._project, self._started)
    self._conv.connect_empty_state(self._composer.set_text)  # ← ADD
```

- [ ] **Step 6: Run all tests — expect pass**

```bash
python -m pytest tests/test_native_ui.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add native_ui/windows.py tests/test_native_ui.py
git commit -m "feat: empty state with starter prompt chips"
```

---

## Task 8: Copy button on `SamTurn`

**Files:**
- Modify: `native_ui/windows.py` — `SamTurn.__init__`, add `enterEvent`, `leaveEvent`, `_copy_text`

`QApplication` is already imported. `self._raw_text` was stored in Task 4.

- [ ] **Step 1: Write failing smoke test**

Append to `tests/test_native_ui.py`:

```python
def test_sam_turn_has_copy_button(qt_app):
    from native_ui.windows import SamTurn
    turn = SamTurn("Hello Sam", "12:00")
    assert hasattr(turn, "_copy_btn")
    assert not turn._copy_btn.isVisible()
```

- [ ] **Step 2: Run test — expect AttributeError**

```bash
python -m pytest tests/test_native_ui.py::test_sam_turn_has_copy_button -v
```

Expected: `AttributeError` — `_copy_btn` not yet defined.

- [ ] **Step 3: Add copy button to `SamTurn`**

In `SamTurn.__init__`, find the meta_row block (the "Sam  HH:MM" header):

```python
meta_row = QHBoxLayout(); meta_row.setSpacing(8)
sam_lbl = QLabel("Sam")
# ...
meta_row.addWidget(time_lbl)
meta_row.addStretch()
lay.addLayout(meta_row)
```

Replace `meta_row.addStretch()` and `lay.addLayout(meta_row)` with:

```python
meta_row.addStretch()

self._copy_btn = QPushButton("⧉")
self._copy_btn.setFixedSize(22, 22)
self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
self._copy_btn.setFont(_f_mono(11))
self._copy_btn.setToolTip("Copy")
self._copy_btn.setStyleSheet(
    f"QPushButton {{ background: transparent; color: {_INK_GHOST};"
    f" border: 1px solid transparent; border-radius: 4px; }}"
    f"QPushButton:hover {{ color: {_INK_SOFT}; border-color: {_BSOFT}; }}"
)
self._copy_btn.setVisible(False)
self._copy_btn.clicked.connect(self._copy_text)
meta_row.addWidget(self._copy_btn)

lay.addLayout(meta_row)
```

- [ ] **Step 4: Add hover and copy methods to `SamTurn`**

Add these three methods to `SamTurn`:

```python
def enterEvent(self, e) -> None:
    self._copy_btn.setVisible(True)
    super().enterEvent(e)

def leaveEvent(self, e) -> None:
    self._copy_btn.setVisible(False)
    super().leaveEvent(e)

def _copy_text(self) -> None:
    if self._raw_text:
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._raw_text)
        self._copy_btn.setText("✓")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText("⧉"))
```

- [ ] **Step 5: Run all tests — expect pass**

```bash
python -m pytest tests/test_native_ui.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add native_ui/windows.py tests/test_native_ui.py
git commit -m "feat: hover copy button on Sam turns"
```

---

## Final verification

- [ ] **Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS, no regressions against `tests/test_hardcoded_assumptions.py`.

- [ ] **Smoke-test the app visually**

```bash
python -m native_ui  # or however Sam is launched
```

Verify:
1. Composer expands as you type multi-line messages
2. Shift+Enter adds a newline; Enter submits
3. Sam replies with `**bold**` render as bold text
4. Clicking `@ files` opens a file picker; selected path appears in composer
5. Ctrl+K focuses the sidebar search field
6. Fresh session shows "What can Sam do?" with 4 prompt chips
7. Clicking a chip pre-fills the composer
8. Hovering over a Sam reply reveals the `⧉` copy button
9. Clicking `⧉` copies text; button shows `✓` briefly
10. New messages auto-scroll conversation to bottom
