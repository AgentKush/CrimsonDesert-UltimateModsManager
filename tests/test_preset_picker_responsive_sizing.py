"""Regression for GitHub #184 item 1 (devCKVargas): the preset-picker
dialog scales with the parent main window instead of capping at a
500 px sliver against empty space on 1440p displays.

Floor stays at 500 px wide / 200 px tall so tiny windows are not
made worse. Above that, the dialog widget's minimum width is set to
``max(500, 55 percent of parent width)`` and the inner scroll
viewport's minimum height to ``max(200, 45 percent of parent
height)``. The tests build the dialog with a parent of known size
and assert the computed minima land where expected.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest_qt = pytest.importorskip("pytestqt")

from cdumm.i18n import load as load_translations

load_translations("en")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _make_parent(qtbot, width: int, height: int):
    """Build a real QWidget at the requested size so the dialog has
    a window() to read dimensions from."""
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    parent.resize(width, height)
    qtbot.addWidget(parent)
    return parent


def _make_picker(qtbot, parent, tmp_path):
    """Construct PresetPickerDialog with one trivial preset so the
    init code completes."""
    from cdumm.gui.preset_picker import PresetPickerDialog
    presets = [
        (tmp_path / "fake.json",
         {"name": "Demo", "label": "demo",
          "filename": "fake.json", "patches": []}),
    ]
    (tmp_path / "fake.json").write_text("{}", encoding="utf-8")
    dlg = PresetPickerDialog(presets, parent)
    qtbot.addWidget(dlg)
    return dlg


def test_minimum_floor_when_parent_is_tiny(qtbot, app, tmp_path):
    """A small parent (the small floor case) must not push the
    dialog below the 500-wide / 200-tall floor."""
    parent = _make_parent(qtbot, 600, 400)
    dlg = _make_picker(qtbot, parent, tmp_path)
    assert dlg.widget.minimumWidth() >= 500


def test_scales_up_with_large_parent(qtbot, app, tmp_path):
    """On a 2560-wide parent (1440p full screen) the dialog should
    be wider than the 500 floor. 55% of 2560 = 1408."""
    parent = _make_parent(qtbot, 2560, 1440)
    dlg = _make_picker(qtbot, parent, tmp_path)
    assert dlg.widget.minimumWidth() > 500
    # Within +/- 20 px of 55% to allow for rounding.
    assert 1380 <= dlg.widget.minimumWidth() <= 1430


def test_scales_height_with_large_parent(qtbot, app, tmp_path):
    """The inner scroll viewport's minimum height should also scale
    on big monitors. 45% of 1440 = 648, so the picker shows more
    rows without forcing a tiny inner scroller."""
    parent = _make_parent(qtbot, 2560, 1440)
    dlg = _make_picker(qtbot, parent, tmp_path)
    # Find the SmoothScrollArea / SingleDirectionScrollArea inside
    # the dialog and check its minimum height is well above the floor.
    from qfluentwidgets import SingleDirectionScrollArea
    scrolls = dlg.findChildren(SingleDirectionScrollArea)
    assert scrolls, (
        "Expected the preset picker to host a vertical scroll area")
    # The scroll viewport's minimum height should reflect the 45%
    # ceiling on a 1440-tall parent.
    assert any(s.minimumHeight() >= 600 for s in scrolls), (
        f"None of the scroll viewports scaled up: "
        f"{[s.minimumHeight() for s in scrolls]}")
