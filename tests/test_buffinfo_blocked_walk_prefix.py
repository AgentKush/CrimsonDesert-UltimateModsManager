"""A blocked walk may only publish what it can vouch for.

GitHub #325, follow-up to #313 / #321.

#321 refused ``WALK_MISMATCH`` on the reasoning that items placed before
a block were each reached over known-size predecessors. That reasoning is
wrong when the drifted size **is** one of those predecessors: the walk
loses its place early, then stops later for an unrelated reason (an
unknown tag), reports ``WALK_BLOCKED``, and hands back offsets it had
already got wrong.

The issue's own example, record 1000078 with tag 80 drifted one byte::

    truth item starts = [33, 162, 291, 420, 549, 678]
    drift item starts = [33, 163, 168]

Item 1 comes back as 163 instead of 162 -- a one-byte-off write target
returned as valid, which is exactly #313's failure mode.

Refusing only the items at or **after** the block point does not help:
the damage is upstream of it. Measured over every single-tag perturbation
of the shipped table, that rule returns exactly as many wrong offsets as
no rule at all.

So a blocked walk publishes item 0 -- which needs no walking and is
correct whatever the sizes say -- and nothing else, *unless* the single
missing tail is the last one. Then it isn't a lookup at all: the list has
to reach ``min_level_offset``, so the tail is forced, and a prefix that
drifted makes the forced value negative or absurd. That check is what
keeps the 34 records blocking on their final item working.
"""
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from cdumm._vendor import buffinfo_parser as bp
from cdumm.semantic.parser import parse_pabgh_index

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla115"

pytestmark = pytest.mark.skipif(
    not (_FIXTURES / "buffinfo.pabgb.zlib").exists(),
    reason="vanilla115 buffinfo fixture absent")

#: The issue's worked example. It quotes the first six of nine items.
DRIFT_TAG = 80
DRIFT_RECORD = 1000078
TRUTH_STARTS = [33, 162, 291, 420, 549, 678, 807, 936, 1065]
DRIFTED_WALK = [33, 163, 168]

#: Measured on the committed table. Pinned so a change in the size table
#: has to account for its effect on what CDUMM is willing to write.
BLOCKED_RECORDS = 63
BLOCKED_ON_LAST_ITEM = 34


def _load(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / (name + ".zlib")).read_bytes())


def _entries() -> dict[int, bytes]:
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    _keys, offs = parse_pabgh_index(header, "buffinfo")
    ordered = sorted(offs.items(), key=lambda kv: kv[1])
    out = {}
    for i, (key, off) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(body)
        out[key] = body[off:end]
    return out


def _starts(eb: bytes) -> list[int]:
    """Item starts as locate_buff_field would hand them out.

    ``leading_lookup`` resolves to the item's first byte; ``absent_flag``
    is four bytes further in, so this is the path that compares directly
    against the walk's own offsets.
    """
    out = []
    for n in range(bp.parse_entry(eb).buff_data_count):
        got = bp.locate_buff_field(eb, f"buff_data_list[{n}].leading_lookup")
        if got is None:
            break
        out.append(got[0])
    return out


# ------------------------------------------------- resolvable_item_count

def test_a_tiling_walk_publishes_everything():
    for eb in _entries().values():
        starts, outcome = bp.walk_buff_data_items(eb)
        if outcome == bp.WALK_TILES:
            assert bp.resolvable_item_count(eb, starts, outcome) == len(starts)


def test_a_mismatched_walk_publishes_nothing():
    eb = next(iter(_entries().values()))
    starts, _o = bp.walk_buff_data_items(eb)
    assert bp.resolvable_item_count(eb, starts, bp.WALK_MISMATCH) == 0


def test_a_blocked_walk_publishes_item_0_at_minimum():
    entries = _entries()
    blocked = 0
    for eb in entries.values():
        starts, outcome = bp.walk_buff_data_items(eb)
        if outcome != bp.WALK_BLOCKED:
            continue
        blocked += 1
        assert bp.resolvable_item_count(eb, starts, outcome) >= 1
        assert bp.locate_buff_field(
            eb, "buff_data_list[0].absent_flag") is not None
    assert blocked == BLOCKED_RECORDS


def test_records_blocking_on_their_last_item_keep_their_earlier_items():
    """Issue acceptance #1. These are the records the derived-tail check
    exists for -- without it they would drop to item 0 only."""
    entries = _entries()
    rescued = kept_offsets = 0
    for eb in entries.values():
        starts, outcome = bp.walk_buff_data_items(eb)
        if outcome != bp.WALK_BLOCKED:
            continue
        if len(starts) != bp.parse_entry(eb).buff_data_count:
            continue
        rescued += 1
        n = bp.resolvable_item_count(eb, starts, outcome)
        assert n == len(starts), "the last tail is derivable, so all resolve"
        kept_offsets += max(0, n - 1)
    assert rescued == BLOCKED_ON_LAST_ITEM
    assert kept_offsets > 0


# --------------------------------------------------- the drift it closes

def test_the_issue_example_no_longer_returns_a_shifted_offset(monkeypatch):
    """Record 1000078, tag 80 drifted by one byte. Item 1 used to come
    back as 163 against a true 162."""
    eb = _entries()[DRIFT_RECORD]
    assert _starts(eb) == TRUTH_STARTS, "precondition: undrifted truth"

    sizes = dict(bp._VARIANT_TAIL_SIZES)
    sizes[DRIFT_TAG] += 1
    monkeypatch.setattr(bp, "_VARIANT_TAIL_SIZES", sizes)

    starts, outcome = bp.walk_buff_data_items(eb)
    assert outcome == bp.WALK_BLOCKED, "precondition: this drift blocks"
    assert starts == DRIFTED_WALK, "the walk itself still goes astray"

    published = _starts(eb)
    assert published == [33], "only item 0 may be published"
    assert bp.locate_buff_field(
        eb, "buff_data_list[1].absent_flag") is None
    assert bp.locate_buff_field(
        eb, "buff_data_list[1].leading_lookup") is None


def test_no_drifted_offset_is_published_for_any_blocked_record(monkeypatch):
    """Issue acceptance #2, over every tag the perturbation blocks.

    Kept to a single delta so it stays a fast test; the full sweep over
    six deltas of every tag was run offline and also reaches zero.
    """
    entries = _entries()
    truth = {k: _starts(eb) for k, eb in entries.items()}

    wrong = 0
    for tag in sorted(bp._VARIANT_TAIL_SIZES):
        sizes = dict(bp._VARIANT_TAIL_SIZES)
        sizes[tag] += 1
        monkeypatch.setattr(bp, "_VARIANT_TAIL_SIZES", sizes)
        for k, eb in entries.items():
            _s, outcome = bp.walk_buff_data_items(eb)
            if outcome != bp.WALK_BLOCKED:
                continue          # tiles: not this issue; mismatch: refused
            for i, off in enumerate(_starts(eb)):
                if i >= len(truth[k]) or truth[k][i] != off:
                    wrong += 1
        monkeypatch.undo()
    assert wrong == 0


def test_the_guard_only_ever_tightens(monkeypatch):
    """Nothing becomes resolvable that wasn't before -- a safety check
    that widens coverage would mean it isn't a safety check."""
    entries = _entries()
    before = {k: _starts(eb) for k, eb in entries.items()}
    for k, eb in entries.items():
        starts, outcome = bp.walk_buff_data_items(eb)
        n = bp.resolvable_item_count(eb, starts, outcome)
        assert len(before[k]) <= len(starts)
        assert len(before[k]) == min(n, len(starts))
