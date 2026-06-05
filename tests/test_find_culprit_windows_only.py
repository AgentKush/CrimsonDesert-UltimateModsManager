"""GitHub #195 (RoGreat): "Find culprit mod" is Windows-only and should
be disabled on Linux / macOS, the way the ASI plugins section already is.

Verified root cause: the bisection (FindCulpritPage -> _AutoBisectWorker
-> game_monitor.launch_and_test) decides crash-vs-stable from the
CrimsonDesert.exe process table and the Pearl Abyss crashpad .dmp files,
both Windows-only. On non-Windows, game_monitor.find_game_process()
always returns None, so launch_and_test waits the full launch_timeout
and then declares "process not found -> treating as crash" EVERY round.
The result on Linux is a 60s-per-round run that reports a false crash for
every mod subset and points at an innocent mod. Disable the feature
there instead of producing a misleading culprit.
"""
from __future__ import annotations

import pytest

import cdumm.gui.pages.tool_page as tool_page
from cdumm.gui.pages.tool_page import (
    FindCulpritPage,
    _bisect_windows_only_message,
)


def test_policy_message_none_on_windows():
    assert _bisect_windows_only_message(True) is None


def test_policy_message_present_off_windows():
    msg = _bisect_windows_only_message(False)
    assert isinstance(msg, str) and msg.strip(), (
        "non-Windows must yield a user-facing explanation string")


def test_run_button_disabled_off_windows(qtbot, monkeypatch):
    """#195: on Linux/macOS the Start button must be disabled so the
    broken bisection can never be launched."""
    monkeypatch.setattr(tool_page, "IS_WINDOWS", False)
    page = FindCulpritPage()
    qtbot.addWidget(page)
    assert page._run_btn.isEnabled() is False, (
        "Find Culprit Start button must be disabled on non-Windows")


def test_run_button_enabled_on_windows(qtbot, monkeypatch):
    """Windows keeps the feature fully available."""
    monkeypatch.setattr(tool_page, "IS_WINDOWS", True)
    page = FindCulpritPage()
    qtbot.addWidget(page)
    assert page._run_btn.isEnabled() is True


def test_on_run_clicked_is_guarded_off_windows(qtbot, monkeypatch):
    """Defense in depth: even if the button is somehow enabled (e.g. a
    retranslate / state-reset path re-enables it), _on_run_clicked must
    not start a bisection on a non-Windows host."""
    monkeypatch.setattr(tool_page, "IS_WINDOWS", False)
    page = FindCulpritPage()
    qtbot.addWidget(page)

    started = []
    # If the guard fails, _on_run_clicked would proceed past the platform
    # check toward the mod-manager / bisection setup. Make any such
    # progression observable and fatal.
    monkeypatch.setattr(
        page, "_can_run", lambda: started.append("can_run") or True)

    page._on_run_clicked()

    assert started == [], (
        "_on_run_clicked must short-circuit on non-Windows before "
        "reaching _can_run / bisection setup")
