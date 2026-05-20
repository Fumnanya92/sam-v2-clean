# Sam UI Round-Off Design

**Date:** 2026-05-20  
**Files affected:** `native_ui/windows.py`, `native_ui/app.py`  
**Scope:** 8 targeted improvements to the PyQt6 chat UI — no new dependencies, no structural refactor.

---

## 1. Auto-grow Composer

**What:** Replace `QLineEdit` in `Composer` with a `_GrowingInput(QTextEdit)` subclass.

**Behaviour:**
- Starts at 1 line tall (same height as current input)
- Expands line-by-line up to 5 lines as the user types
- Scrolls internally beyond 5 lines
- `Enter` → submit; `Shift+Enter` → newline
- `document().contentsChanged` → `updateGeometry()` to trigger layout resize

**Implementation:**
- Override `sizeHint` to return `document().size().height()` clamped between `_MIN_H` and `_MAX_H`
- Override `keyPressEvent` to intercept bare Enter (submit) vs Shift+Enter (newline)
- `Composer.set_busy()` updates placeholder text on the `_GrowingInput`
- `Composer._submit()` reads `self._field.toPlainText().strip()`

---

## 2. Markdown Rendering in Sam Messages

**What:** Upgrade `_render_inline` → `_render_markdown` with support for structural elements.

**Supported syntax (regex-only, no new dependency):**
| Markdown | Output |
|---|---|
| `**bold**` | `<b>bold</b>` |
| `*italic*` | `<i>italic</i>` |
| `# Heading` | `<h3>` styled with `_INK`, font size 16 |
| `## Heading` | `<h4>` styled with `_INK_SOFT`, font size 14 |
| `- item` or `* item` | Inline bullet row with `·` glyph |
| `` `code` `` | Existing code span (unchanged) |
| Blank line | `<br><br>` paragraph break |

**Implementation:**
- `_render_markdown(text: str) -> str` replaces `_render_inline` in `SamTurn`
- Process order: escape HTML first, then headings, then bold/italic, then bullets, then code spans, then line breaks
- Output wrapped in a single `<span style="line-height:1.68;">` block

---

## 3. Error State Styling

**What:** Visually distinguish Sam error replies from normal replies.

**Behaviour:**
- `SamTurn` gains `is_error: bool = False` constructor param
- When `is_error=True`: left border `3px solid _BAD (#c8533a)`, body text colour `_BAD`, header "Sam" label tinted `_BAD`
- `ConversationArea.add_sam_turn` and `SamWindow.add_sam_message` propagate `is_error`
- `app.py` `_on_done` passes `is_error=not result.ok`

---

## 4. @Files Chip Wired Up

**What:** Clicking the `@` chip opens a file picker and inserts the path into the composer.

**Behaviour:**
- `QFileDialog.getOpenFileName(parent, "Attach file")` — no filter, any file
- Selected path inserted into composer as `@/path/to/file ` (trailing space)
- If user cancels, no-op
- Composer gains focus after insertion

**Implementation:**
- `Composer` exposes `insert_at_file(path: str)` method
- The `@` chip's `clicked` signal connects to `_on_at_clicked` in `Composer`

---

## 5. Ctrl+K Focuses Sidebar Search

**What:** Global keyboard shortcut to jump focus to the sidebar search field.

**Implementation:**
- `Sidebar._search_field` promoted from local variable to `self._search_field`
- `SamWindow.__init__` adds `QShortcut(QKeySequence("Ctrl+K"), self)` → `self._sidebar._search_field.setFocus()`
- Works whether sidebar is open or closed (if closed, also opens sidebar first)

---

## 6. Empty State / Starter Prompts

**What:** Show helpful prompt chips when the conversation is empty.

**Behaviour:**
- `_EmptyState` widget displayed in the centre of `ConversationArea` when no turns exist
- Contains a brief heading ("What can Sam do?") and 4 clickable prompt chips:
  1. "Build me a new project"
  2. "Fix a bug in my code"
  3. "Explain what this file does"
  4. "Run my tests and summarise"
- Clicking a chip pre-fills the composer (does not auto-submit)
- Widget is removed when `add_user_turn` is first called

**Implementation:**
- `ConversationArea` tracks `self._empty_state: _EmptyState | None`
- `_EmptyState` emits `prompt_selected = pyqtSignal(str)`
- Connected to `Composer._field.setText` (or equivalent) via `SamWindow`

---

## 7. Copy Button on Sam Turns

**What:** One-click copy of Sam's reply text.

**Behaviour:**
- Small clipboard button (`⧉` or `📋`) in the top-right of the Sam turn header row
- Hidden by default; visible on mouse hover over the turn widget
- Copies `self._raw_text` (the original plain text, not HTML) to `QApplication.clipboard()`
- Brief visual confirmation: button text changes to `✓` for 1.5s then reverts

**Implementation:**
- `SamTurn` stores `self._raw_text = text`
- `enterEvent` / `leaveEvent` toggle `self._copy_btn.setVisible()`
- `QTimer.singleShot(1500, lambda: self._copy_btn.setText("⧉"))` for revert

---

## 8. Auto-scroll Fix

**What:** Make scroll-to-bottom reliable — remove the fragile 60ms timer.

**Current problem:** `QTimer.singleShot(60, ...)` fires before the layout reflow completes on long messages, so the scroll stops short.

**Fix:**
- Connect `self._scroll.verticalScrollBar().rangeChanged` to `self._on_range_changed`
- `_on_range_changed(min, max)` → `self._scroll.verticalScrollBar().setValue(max)`
- This fires automatically whenever new content pushes the scrollbar range up
- Remove `_scroll_end` timer calls (or keep as a fallback with 0ms delay)

---

## Interaction Map

```
SamWindow
├── TopBar (unchanged)
├── Sidebar
│   └── _search_field (now public)   ← Ctrl+K shortcut
├── ConversationArea
│   ├── _EmptyState                  ← removed on first user turn
│   ├── UserTurn                     (unchanged)
│   ├── SamTurn
│   │   ├── _copy_btn                ← hover-visible
│   │   └── _render_markdown()       ← replaces _render_inline
│   └── scrollbar.rangeChanged       ← auto-scroll
└── Composer
    ├── _GrowingInput (QTextEdit)     ← replaces QLineEdit
    └── @ chip → QFileDialog         ← new
```

---

## Out of Scope

- `/commands` chip (requires a command palette — separate feature)
- Streaming markdown (markdown rendered once, on message completion)
- Search functionality in sidebar (chip focuses field; filtering is a future task)
