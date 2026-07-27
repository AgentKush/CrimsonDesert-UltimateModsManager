"""buffinfo intents the item walk can't place must be REPORTED.

Ultra Hard Mode (Nexus 2295) sets
``buff_data_list[N].data.variant.body.f01`` on
``BuffLevel_Difficulty_Boss``. CDUMM validated all 5 intents as
supported, wrote **zero bytes**, and the only user-facing message was
the generic "the targeted entry may not exist in this game version" --
which is false. The entry exists; the buffinfo item walk stalls inside
it, at item 0's variant body.

Why the validator can't screen these out by name: resolvability is
**per record**, not per path shape. The exact same path resolves fine
on most buffinfo entries (measured across the corpus: it resolves on
entries used by 46 other mod intents) and stalls only on this one. The
validator has no table bytes, so the honest place to report is the
apply layer, which does.

This module pins that ``_buffinfo_intents_to_changes`` hands back the
intents it dropped, so the caller can name them instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cdumm.engine.format3_apply import (
    BuffinfoUnresolved,
    _buffinfo_intents_to_changes,
)


@dataclass
class _Intent:
    entry: str
    key: int
    field: str
    op: str
    new: Any


def test_unresolved_out_is_optional():
    """Existing callers pass three args; that must keep working."""
    changes = _buffinfo_intents_to_changes(b"", b"", [])
    assert changes == []


def test_unplaceable_intent_is_reported_not_swallowed():
    """A key that isn't even in the (empty) PABGH must not crash, and
    with no index there is nothing to report."""
    out: list[BuffinfoUnresolved] = []
    changes = _buffinfo_intents_to_changes(
        b"", b"", [_Intent("X", 1, "buff_data_list[9].data.variant."
                           "body.f01", "set", 1)], out)
    assert changes == []


def test_namedtuple_carries_field_entry_and_key():
    u = BuffinfoUnresolved("buff_data_list[4].data.variant.body.f01",
                           "BuffLevel_Difficulty_Boss", 1000277)
    assert u.field.endswith(".f01")
    assert u.entry == "BuffLevel_Difficulty_Boss"
    assert u.key == 1000277


def test_apply_layer_names_the_unplaced_fields(caplog):
    """The warning must name the fields, and must NOT claim the entry
    is missing -- that was the misleading message this replaces."""
    import logging

    from cdumm.engine import format3_apply

    calls: list = []

    def _fake(body, header, intents, unresolved_out=None):
        if unresolved_out is not None:
            unresolved_out.append(BuffinfoUnresolved(
                "buff_data_list[4].data.variant.body.f01",
                "BuffLevel_Difficulty_Boss", 1000277))
        calls.append(True)
        return []

    orig = format3_apply._buffinfo_intents_to_changes
    format3_apply._buffinfo_intents_to_changes = _fake
    try:
        with caplog.at_level(logging.WARNING,
                             logger="cdumm.engine.format3_apply"):
            format3_apply._intents_to_v2_changes(
                "buffinfo.pabgb", b"", b"", [])
    finally:
        format3_apply._buffinfo_intents_to_changes = orig

    assert calls, "buffinfo route did not run"
    text = caplog.text
    assert "buff_data_list[4].data.variant.body.f01" in text
    assert "1000277" in text
    assert "may not exist" not in text
