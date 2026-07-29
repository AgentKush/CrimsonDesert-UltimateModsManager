"""The Apply-blocked banner must say which of two causes it is.

GitHub #326, follow-up to #315 / #323.

Two different situations reach the same block:

* the game really did change under us, or
* the last rescan couldn't read ``CrimsonDesert.exe`` at all, which
  leaves the OLD fingerprint stored and looks identical at the
  comparison.

The banner said "Crimson Desert was updated since your last snapshot"
for both. In the second case that sentence is false: nothing was
updated, CDUMM just couldn't look.

#323 made this worse rather than better, which is the part worth being
straight about. Before it, the flag cleared and users sometimes slipped
past. Now the state is correctly preserved, so the wrong message is
*guaranteed* to appear in that case, while the accurate explanation only
ever existed in a 12-second toast.

The Activity log had the same problem and is more durable than a toast,
so it also has to record what actually happened.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

FLAG = "last_snapshot_version_stamped"
EN = (Path(__file__).resolve().parents[1] / "src" / "cdumm" /
      "translations" / "en.json")
DE = (Path(__file__).resolve().parents[1] / "src" / "cdumm" /
      "translations" / "de.json")


@pytest.fixture
def strings() -> dict:
    return json.loads(EN.read_text(encoding="utf-8"))


def _win(conn=None):
    """Stand-in for the window; the helper only touches ``_db``."""
    return type("W", (), {
        "_db": None if conn is None else type(
            "DB", (), {"connection": conn})()})()


def _stamped(win) -> bool:
    pytest.importorskip("qfluentwidgets")
    from cdumm.gui.fluent_window import CdummWindow
    return CdummWindow._snapshot_stamped_version(win)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
    c.commit()
    return c


# ------------------------------------------------------- which message

def test_a_real_game_update_keeps_the_original_wording(conn, strings):
    """The genuine path must be unchanged."""
    conn.execute("INSERT INTO config VALUES (?, ?)", (FLAG, "1"))
    assert _stamped(_win(conn)) is True
    body = strings["apply.rescan_required_body"]
    assert "was updated" in body


def test_an_unreadable_version_selects_the_other_message(conn, strings):
    conn.execute("INSERT INTO config VALUES (?, ?)", (FLAG, "0"))
    assert _stamped(_win(conn)) is False

    body = strings["apply.version_unreadable_body"]
    # It must not ASSERT an update. Checking for the bare phrase would
    # fail on the sentence that explicitly denies one, so compare
    # against the claim the other message actually makes.
    claim = "Crimson Desert was updated since your last snapshot"
    assert claim in strings["apply.rescan_required_body"]
    assert claim not in body, (
        "this is the claim that was false; it must not reappear")
    assert "does NOT mean the game was updated" in body, (
        "and the wrong conclusion should be headed off, not just omitted")


def test_the_unreadable_message_says_what_happened_and_what_to_do(strings):
    title = strings["apply.version_unreadable_title"]
    body = strings["apply.version_unreadable_body"]
    assert title and body
    low = body.lower()
    assert "antivirus" in low or "steam" in low, "name the likely cause"
    assert "rescan" in low, "name the action that clears it"
    assert "temporary" in low or "not mean" in low, (
        "say it isn't a game update, since that is the wrong conclusion "
        "a user would otherwise draw")


def test_both_apply_messages_exist_in_english_and_german():
    en = json.loads(EN.read_text(encoding="utf-8"))
    de = json.loads(DE.read_text(encoding="utf-8"))
    for key in ("apply.version_unreadable_title",
                "apply.version_unreadable_body",
                "activity.msg_snapshot_no_version"):
        assert key in en, key
        assert key in de, f"{key} missing from de.json (parity is enforced)"


# ------------------------------------------------------- activity log

def test_the_activity_message_reflects_a_failed_stamp(strings):
    ok = strings["activity.msg_snapshot_created"]
    bad = strings["activity.msg_snapshot_no_version"]
    assert ok != bad
    assert "{count}" in bad, "the file count is still useful"
    low = bad.lower()
    assert "could not be read" in low or "couldn't be read" in low
    assert "locked" in low, "say Apply is still blocked, which is the point"


def test_the_activity_log_is_not_a_plain_success_on_failure():
    """The call site must choose between the two, not always log the
    success string. Read the source rather than driving Qt."""
    src = (Path(__file__).resolve().parents[1] / "src" / "cdumm" / "gui" /
           "fluent_window.py").read_text(encoding="utf-8")
    marker = 'self._log_activity(\n            "snapshot",'
    assert marker in src, "the snapshot activity call should be branched"
    tail = src.split(marker, 1)[1][:400]
    assert "activity.msg_snapshot_created" in tail
    assert "activity.msg_snapshot_no_version" in tail
    assert "if stamped" in tail


def test_the_banner_branches_on_the_stamp_flag():
    src = (Path(__file__).resolve().parents[1] / "src" / "cdumm" / "gui" /
           "fluent_window.py").read_text(encoding="utf-8")
    block = src.split("if stale:", 1)[1][:900]
    assert "_snapshot_stamped_version()" in block, (
        "the banner has to ask why it is blocked")
    assert "apply.version_unreadable_title" in block
    assert "apply.rescan_required_title" in block
