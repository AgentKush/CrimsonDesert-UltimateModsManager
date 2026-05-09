"""Regression: when CDUMM has to drop one of N mods touching the
same entry because they change the file size and can't merge, the
user-facing warning must NAME the mods that were dropped (GioGr on
Nexus had to dig through the conflict viewer, and his conflict
viewer didn't even show the silently-lost mod).

Earlier the message wore developer-style 'Active:' / 'Dropped:'
labels; per Faisal's plain-language rule (no code jargon for end
users) the message was rewritten in plain English: '<mod> could
not be applied ... move it higher in the mod list'. The
dropped-name machinery is still required, just the user-facing
phrasing changed. This test pins the machinery and the plain-
English shape together.

The relevant code lives in the OVERLAY-merge size_changed branch
(the second `if size_changed:` block), not the byte-merge branch
which uses a simpler 'kept the highest-priority' line.
"""
from __future__ import annotations

import re
from pathlib import Path


def _src() -> str:
    return (Path(__file__).resolve().parents[1]
            / "src" / "cdumm" / "engine" / "apply_engine.py"
            ).read_text(encoding="utf-8")


def _find_overlay_size_changed_branch(src: str) -> str:
    """Return the body of the overlay-merge size_changed branch — the
    one that actually collects dropped mod names. We anchor on the
    distinctive 'Overlay merge:' log line that opens this branch and
    end at the result.append(entries[winner]) that closes it.
    """
    anchor_marker = "Overlay merge: %s has size-changing entries"
    anchor = src.find(anchor_marker)
    assert anchor != -1, (
        f"expected '{anchor_marker}' log line in apply_engine.py "
        f"to mark the overlay-merge size_changed branch")
    # Walk backwards to the enclosing `if size_changed:` line.
    if_pos = src.rfind("if size_changed:", 0, anchor)
    assert if_pos != -1, "if size_changed: not found before overlay log"
    end = src.find("result.append(entries[winner]", if_pos)
    assert end != -1, "branch closer result.append(entries[winner] missing"
    return src[if_pos:end]


def test_message_lists_dropped_mod_names():
    body = _find_overlay_size_changed_branch(_src())
    assert "dropped_names" in body, (
        "the branch must collect dropped mod names into a list "
        "(was previously only counting them)")
    assert re.search(r"if i == winner:\s*\n\s*continue", body), (
        "must skip the winner index when collecting dropped names")
    assert re.search(r'\.get\("mod_name"', body), (
        "must read mod_name from each entry's metadata so the "
        "message names actual mods, not indices")


def test_message_caps_long_list_with_and_more():
    body = _find_overlay_size_changed_branch(_src())
    assert "[:5]" in body, (
        "dropped list must be capped at 5 names to keep banners "
        "readable on huge conflict sets")
    assert "more" in body, (
        "must show an 'and N more' suffix when the list is capped")


def test_message_uses_plain_english_not_dev_labels():
    """Faisal's plain-language rule: the user-facing string must read
    as a sentence to a non-developer, NOT as a structured key/value
    diagnostic. Specifically the old 'Active:' / 'Dropped:' labels
    must NOT come back, and the message must contain the human
    sentence about the mod failing to apply."""
    body = _find_overlay_size_changed_branch(_src())
    # The old dev-style labels must not be re-introduced.
    assert "Active:" not in body, (
        "user-facing message must not use developer-style "
        "'Active:' label — switch to plain English")
    assert "Dropped:" not in body, (
        "user-facing message must not use developer-style "
        "'Dropped:' label — switch to plain English")
    # The plain-English sentence must name the failure mode.
    assert "could not be applied" in body, (
        "message must say the dropped mod 'could not be applied' "
        "in a way a non-developer reads as a normal sentence")


def test_message_tells_user_how_to_change_winner():
    body = _find_overlay_size_changed_branch(_src())
    # The user must learn what UI action fixes it: raising the mod
    # higher in the load order. 'mod list' is the on-screen label
    # in CDUMM for that, 'load order' is the term mod-manager users
    # know — accept either as long as the action is concrete.
    msg_has_action = (
        "mod list" in body
        or "load order" in body
        or "drag" in body.lower()
    )
    assert msg_has_action, (
        "message must tell the user the actual UI action — move "
        "the desired mod higher in the mod list / load order")
