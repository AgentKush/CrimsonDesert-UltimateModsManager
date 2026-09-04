"""Regression tests for :func:`cdumm.platform.python_script_command`.

Script-import built its argv as a hardcoded ``["py", "-3"]``. ``py`` is
the Windows Python Launcher; it does not exist on macOS or Linux, so on
the native path (LINUX.md's recommended mode) the spawn raised
FileNotFoundError, ``import_from_script`` swallowed it into a generic
"Script execution failed", and every ``.py`` mod imported as a no-op
diff. Windows CI could not see it -- ``windows-tests.yml`` runs pytest
on ``windows-latest`` only, so nothing ever exercised these paths off
Windows.

The frozen-build caveat is the one :func:`cdumm.platform.worker_command`
already documents: under PyInstaller ``sys.executable`` is CDUMM itself,
not an interpreter, so it must never be handed a script path.
"""
from __future__ import annotations

import sys

import pytest

from cdumm import platform as cdplat


@pytest.fixture
def _unfreeze(monkeypatch):
    """Default every test to run-from-source unless it says otherwise."""
    monkeypatch.delattr(sys, "frozen", raising=False)


def test_windows_keeps_the_launcher(monkeypatch, _unfreeze):
    """The launcher resolves a real Python 3 even when none is on PATH,
    which is the case on most players' machines -- so Windows behaviour
    is deliberately unchanged."""
    monkeypatch.setattr(cdplat, "IS_WINDOWS", True)
    assert cdplat.python_script_command() == ["py", "-3"]


def test_posix_from_source_uses_the_running_interpreter(monkeypatch, _unfreeze):
    monkeypatch.setattr(cdplat, "IS_WINDOWS", False)
    assert cdplat.python_script_command() == [sys.executable]


def test_posix_never_returns_the_py_launcher(monkeypatch, _unfreeze):
    """The actual bug: 'py' off Windows is an unrunnable command."""
    monkeypatch.setattr(cdplat, "IS_WINDOWS", False)
    cmd = cdplat.python_script_command()
    assert cmd is not None
    assert "py" not in cmd


def test_posix_frozen_resolves_python3_not_the_app(monkeypatch):
    """Under PyInstaller ``sys.executable`` is the CDUMM binary. Handing
    it a script path would re-launch the app instead of running the
    mod's script, so a frozen build must resolve a real interpreter."""
    monkeypatch.setattr(cdplat, "IS_WINDOWS", False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/CDUMM/CDUMM3", raising=False)
    monkeypatch.setattr(cdplat.shutil, "which",
                        lambda n: "/usr/bin/python3" if n == "python3" else None)
    assert cdplat.python_script_command() == ["/usr/bin/python3"]


def test_posix_frozen_without_any_python_reports_none(monkeypatch):
    """No interpreter anywhere is a real possibility on a packaged
    build. Returning None lets the caller say so plainly instead of
    surfacing a bare FileNotFoundError."""
    monkeypatch.setattr(cdplat, "IS_WINDOWS", False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(cdplat.shutil, "which", lambda n: None)
    assert cdplat.python_script_command() is None
