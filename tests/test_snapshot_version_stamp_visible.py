"""A rescan that couldn't read the game version must say so.

GitHub #315. #309 blocks Apply when the live game fingerprint differs from
the stored one, and tells the user to run Rescan Game Files. There is one
path where that advice doesn't work:

1. ``detect_game_version`` returns ``None`` during the snapshot -- a
   transient, e.g. antivirus or Steam briefly holding CrimsonDesert.exe.
2. The worker logs "not stamping" and leaves the OLD fingerprint stored.
3. The caller cleared the ``game_updated`` flag anyway.
4. Detection succeeds on the next Apply click, live now differs from the
   stale stored value, and Apply is blocked -- immediately after the user
   did exactly what the banner asked.

Recoverable (rescanning again once detection works clears it), so not a
lockout. But the user ran the fix, it appeared to succeed, and the same
message came back. The only evidence was a log line.

The snapshot now records whether the stamp actually happened, in the same
transaction as the snapshot itself, and the caller stops pretending.
"""
from __future__ import annotations

import sqlite3

import pytest

FLAG = "last_snapshot_version_stamped"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
    c.commit()
    return c


def _stamped(win) -> bool:
    """The real implementation, called unbound against a stand-in."""
    pytest.importorskip("qfluentwidgets")
    from cdumm.gui.fluent_window import CdummWindow
    return CdummWindow._snapshot_stamped_version(win)


class _Win:
    """Stands in for the window: the helper only touches ``self._db``."""

    def __init__(self, conn=None):
        self._db = None if conn is None else type(
            "DB", (), {"connection": conn})()


def test_reads_a_successful_stamp(conn):
    conn.execute("INSERT INTO config VALUES (?, ?)", (FLAG, "1"))
    assert _stamped(_Win(conn)) is True


def test_reads_a_failed_stamp(conn):
    conn.execute("INSERT INTO config VALUES (?, ?)", (FLAG, "0"))
    assert _stamped(_Win(conn)) is False


def test_an_older_database_without_the_flag_is_treated_as_fine(conn):
    """Upgrading must not start warning about snapshots that were fine."""
    assert _stamped(_Win(conn)) is True


def test_no_database_is_treated_as_fine():
    assert _stamped(_Win(None)) is True


def test_a_broken_config_table_never_breaks_the_callback():
    c = sqlite3.connect(":memory:")   # no config table at all
    assert _stamped(_Win(c)) is True


# --------------------------------------------------- the worker's record

def _run_stamp_block(conn, detected: str | None, raises: bool = False):
    """Reproduce the worker's stamp step against a real config table.

    Mirrors the block in ``snapshot_manager``: stamp when a fingerprint
    comes back, and record the outcome either way.
    """
    stamped = False
    try:
        if raises:
            raise OSError("exe locked")
        fp = detected
        if fp:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("game_version_fingerprint", fp))
            stamped = True
    except Exception:  # noqa: BLE001 -- matches the worker's handler
        stamped = False
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (FLAG, "1" if stamped else "0"))
    conn.commit()
    return stamped


def _get(conn, key):
    row = conn.execute(
        "SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def test_a_readable_version_stamps_and_records_success(conn):
    assert _run_stamp_block(conn, "abc123") is True
    assert _get(conn, "game_version_fingerprint") == "abc123"
    assert _get(conn, FLAG) == "1"
    assert _stamped(_Win(conn)) is True


def test_an_unreadable_version_leaves_the_old_fingerprint_and_records_it(conn):
    """The exact sequence from the issue: the stale value survives, which
    is why the flag has to exist -- the stored fingerprint alone cannot
    tell you the snapshot failed to refresh it."""
    conn.execute("INSERT INTO config VALUES (?, ?)",
                 ("game_version_fingerprint", "OLD-1.14"))
    conn.commit()

    assert _run_stamp_block(conn, None) is False
    assert _get(conn, "game_version_fingerprint") == "OLD-1.14", (
        "the stale fingerprint is deliberately left alone")
    assert _get(conn, FLAG) == "0"
    assert _stamped(_Win(conn)) is False


def test_a_raising_detector_is_recorded_as_not_stamped(conn):
    assert _run_stamp_block(conn, "abc", raises=True) is False
    assert _get(conn, FLAG) == "0"
    assert _stamped(_Win(conn)) is False


def test_a_later_good_rescan_clears_the_warning(conn):
    _run_stamp_block(conn, None)
    assert _stamped(_Win(conn)) is False
    _run_stamp_block(conn, "abc123")
    assert _stamped(_Win(conn)) is True


# ------------------------------------------------------ the user-facing bit

def test_the_warning_strings_exist_and_say_what_to_do():
    """Read en.json directly: tr() falls back to English anyway, and a
    bare test process has no language loaded, so going through it would
    assert nothing."""
    import json
    from pathlib import Path

    en = json.loads(
        (Path(__file__).resolve().parents[1] / "src" / "cdumm" /
         "translations" / "en.json").read_text(encoding="utf-8"))

    assert "infobar.snapshot_no_version" in en
    body = en["infobar.snapshot_no_version_msg"]
    assert "{count}" in body, "the file count still gets reported"
    lowered = body.lower()
    assert "rescan" in lowered, "must name the action that fixes it"
    assert "antivirus" in lowered or "steam" in lowered, (
        "must name the likely cause, since 'try again' alone is not advice")
