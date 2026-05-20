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


def test_sam_turn_error_instantiates(qt_app):
    from native_ui.windows import SamTurn
    turn = SamTurn("Something went wrong", "12:00", is_error=True)
    assert turn is not None


def test_sam_turn_normal_instantiates(qt_app):
    from native_ui.windows import SamTurn
    turn = SamTurn("All good", "12:00", is_error=False)
    assert turn is not None


def test_sam_turn_has_copy_button(qt_app):
    from native_ui.windows import SamTurn
    turn = SamTurn("Hello Sam", "12:00")
    assert hasattr(turn, "_copy_btn")
    assert not turn._copy_btn.isVisible()


def test_empty_state_instantiates(qt_app):
    from native_ui.windows import _EmptyState
    w = _EmptyState()
    assert w is not None


def test_conversation_area_shows_empty_state(qt_app):
    from native_ui.windows import ConversationArea
    ca = ConversationArea()
    assert ca._empty_state is not None
