"""Regression for GitHub #186 (lupo1190): the Settings page exposes a
'Steam launch method' dropdown that persists to the steam_launch_method
config key.

Default value is the URI launch path (current behavior). Switching to
Direct (-applaunch) writes ``"applaunch"`` to the config row, which
fluent_window._on_launch_game reads on the next launch attempt.

The fluent_window code path is covered separately by
test_launch_game_logging; this file pins the UI side.
"""
from __future__ import annotations

import pytest

pytest_qt = pytest.importorskip("pytestqt")

from cdumm.i18n import load as load_translations
from cdumm.storage.config import Config

load_translations("en")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_default_dropdown_index_is_uri(qtbot, app, db, tmp_path):
    """With no saved value, the dropdown should show URI (index 0)
    so a fresh install gets the existing behavior."""
    from cdumm.gui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_managers(db=db, game_dir=tmp_path)
    assert page._steam_launch_method_combo.currentIndex() == 0


def test_saved_applaunch_loads_into_dropdown(qtbot, app, db, tmp_path):
    """If the config row already says 'applaunch', the dropdown
    reflects index 1 on first render, so the user sees their
    persisted choice."""
    Config(db).set("steam_launch_method", "applaunch")

    from cdumm.gui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_managers(db=db, game_dir=tmp_path)
    assert page._steam_launch_method_combo.currentIndex() == 1


def test_dropdown_change_persists_to_config(qtbot, app, db, tmp_path):
    """Changing the dropdown to Direct (-applaunch) writes 'applaunch'
    to the config row; changing back to URI writes 'uri'."""
    from cdumm.gui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_managers(db=db, game_dir=tmp_path)

    # Programmatically trigger the slot the way the user would.
    page._steam_launch_method_combo.setCurrentIndex(1)
    assert Config(db).get("steam_launch_method") == "applaunch"

    page._steam_launch_method_combo.setCurrentIndex(0)
    assert Config(db).get("steam_launch_method") == "uri"
