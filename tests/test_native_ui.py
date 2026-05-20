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
