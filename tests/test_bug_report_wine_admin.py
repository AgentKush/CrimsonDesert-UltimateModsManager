"""GitHub #388 (NonDScript): Wine prefixes always run programs with the
admin token, so the bug report's 'running as administrator' TL;DR flag
is a false alarm on every Wine/Proton setup. Under Wine the report now
labels it a Wine artifact instead of leading with bogus advice."""
from __future__ import annotations

from pathlib import Path


def test_admin_flag_suppressed_under_wine():
    src = (Path(__file__).parent.parent / "src" / "cdumm" / "gui" /
           "bug_report.py").read_text(encoding="utf-8")
    assert "under_wine = is_wine()" in src
    # The admin TL;DR flag must be gated on NOT running under Wine.
    flag_pos = src.index("CDUMM is running as administrator")
    gate_pos = src.index("if under_wine:")
    assert gate_pos < flag_pos
    assert "Wine artifact" in src
