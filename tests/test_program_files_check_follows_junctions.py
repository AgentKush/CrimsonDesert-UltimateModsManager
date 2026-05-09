"""Program-Files-write-warning checks must follow Windows junctions
to the real on-disk location.

Bug 2026-05-09 (DemonBigj781, GitHub #69): Their Steam library lives
at C:\\Program Files (x86)\\Steam\\steamapps where ``steamapps`` is
a junction to C:\\Users\\<user>\\Documents\\steamapps. The actual
game data sits in their Documents folder, which has full user write
permissions, so the Program-Files restricted-writes warning does
not apply. CDUMM nonetheless fires the warning because the raw
string contains 'program files'.

The two checkers (apply_watchdog.is_game_in_program_files and
bug_report._is_program_files) must resolve junctions/symlinks
before deciding whether the path is really inside Program Files.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def _mklink_junction(junc: Path, target: Path) -> bool:
    """Create a Windows junction junc → target. Returns True on
    success. Junctions don't require admin on most systems."""
    if sys.platform != "win32":
        return False
    target.mkdir(parents=True, exist_ok=True)
    junc.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junc), str(target)],
        capture_output=True, text=True)
    return r.returncode == 0


def test_apply_watchdog_follows_junction_out_of_program_files(tmp_path):
    """A junction sitting under <fake>/Program Files (x86)/... that
    points at <fake>/Documents/... must NOT be flagged as Program
    Files: the real data is under Documents."""
    from cdumm.gui.apply_watchdog import is_game_in_program_files

    fake_pf = tmp_path / "Program Files (x86)" / "Steam" / "steamapps"
    real_target = tmp_path / "Documents" / "steamapps"
    real_target.mkdir(parents=True)

    if not _mklink_junction(fake_pf, real_target):
        pytest.skip("mklink /J unavailable in this environment")

    game_dir = fake_pf / "common" / "Crimson Desert"
    game_dir.mkdir(parents=True)

    assert is_game_in_program_files(game_dir) is False, (
        f"junction-under-Program-Files pointing at "
        f"{real_target} was flagged as Program Files; the real "
        f"data is under Documents and Windows write permissions "
        f"do not apply"
    )


def test_apply_watchdog_still_flags_real_program_files(tmp_path):
    """Sanity: a path that is REALLY under Program Files (no junction
    indirection) must still trip the warning."""
    from cdumm.gui.apply_watchdog import is_game_in_program_files

    real_pf = tmp_path / "Program Files (x86)" / "Steam" / "steamapps" / "common" / "Crimson Desert"
    real_pf.mkdir(parents=True)

    assert is_game_in_program_files(real_pf) is True


def test_bug_report_follows_junction_out_of_program_files(tmp_path):
    from cdumm.gui.bug_report import _is_program_files

    fake_pf = tmp_path / "Program Files (x86)" / "Steam" / "steamapps"
    real_target = tmp_path / "Documents" / "steamapps"
    real_target.mkdir(parents=True)

    if not _mklink_junction(fake_pf, real_target):
        pytest.skip("mklink /J unavailable in this environment")

    game_dir = fake_pf / "common" / "Crimson Desert"
    game_dir.mkdir(parents=True)

    assert _is_program_files(game_dir) is False, (
        "bug_report._is_program_files must follow junctions; "
        "the data behind this junction lives in Documents")


def test_bug_report_still_flags_real_program_files(tmp_path):
    from cdumm.gui.bug_report import _is_program_files

    real_pf = tmp_path / "Program Files" / "Game"
    real_pf.mkdir(parents=True)
    assert _is_program_files(real_pf) is True
