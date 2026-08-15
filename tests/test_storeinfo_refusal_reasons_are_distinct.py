"""A refused store has three meanings, and they are not interchangeable.

``locate_stock_list`` refuses an entry for three different reasons:

  * **provably empty** -- too short to hold one record at any offset, so
    the store genuinely has no stock. Correct answer, not a gap.
  * **not found** -- the entry is big enough, but no span satisfies all
    four acceptance conditions. The record SHAPE is wrong.
  * **ambiguous** -- two or more spans each satisfied all four. The
    shape is right (it parsed and round-tripped, twice); the SCAN is
    what needs narrowing.

The last two demand opposite fixes, and the canary could not tell them
apart: it bucketed on ``"too" in str(exc) or "provably" in str(exc)``,
so "ambiguous, refusing" was counted as not-found. Upstream #365 reports
78 entries under one "not-found" label on the 15 Aug table; which of the
two they are decides whether the record layout gets re-derived or the
search gets bounded.

Substring-matching an exception message is the underlying fault --
rewording a message silently changes the counts. These pin the flags as
the contract instead.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cdumm.engine.storeinfo_native_parser import (
    LAYOUTS,
    StoreListNotFound,
    _entry_payload,
    _min_list_bytes,
    locate_stock_list,
)
from cdumm.semantic.parser import parse_pabgh_index

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla116"


@pytest.fixture(scope="module")
def table():
    body = zlib.decompress((_FIXTURES / "storeinfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIXTURES / "storeinfo.pabgh.zlib").read_bytes())
    _key_size, offsets = parse_pabgh_index(header, "storeinfo")
    return body, offsets


@pytest.fixture(scope="module")
def cd116():
    return next(lay for lay in LAYOUTS if lay.label == "CD 1.16")


def test_the_three_refusals_are_mutually_exclusive_flags():
    """The contract the canary reads. Default is the plain not-found."""
    plain = StoreListNotFound("no span")
    empty = StoreListNotFound("too short", provably_empty=True)
    ambig = StoreListNotFound("two spans", ambiguous=True)

    assert (plain.provably_empty, plain.ambiguous) == (False, False)
    assert (empty.provably_empty, empty.ambiguous) == (True, False)
    assert (ambig.provably_empty, ambig.ambiguous) == (False, True)


def test_provably_empty_entries_carry_the_flag_not_just_the_wording(
        table, cd116):
    """Measured, not constructed: the committed CD 1.16 table has 35
    entries too small to hold one record. Each must set the flag, so the
    canary never has to read the message to bucket them."""
    body, offsets = table
    starts = sorted(offsets.values())
    spans = starts + [len(body)]

    flagged = 0
    for key, off in offsets.items():
        end = spans[spans.index(off) + 1]
        try:
            locate_stock_list(body, _entry_payload(body, off), end, key, cd116)
        except StoreListNotFound as exc:
            assert not exc.ambiguous, (
                "no entry in the committed table is ambiguous -- exactly one "
                "span satisfies all four conditions per entry (#338)")
            if exc.provably_empty:
                flagged += 1
                room = end - _entry_payload(body, off)
                assert room < _min_list_bytes(cd116), (
                    "provably_empty must mean the entry cannot hold a record "
                    "at ANY offset, not merely that none was found")
    assert flagged == 35


def test_every_located_span_is_anchored_where_the_scan_says(table, cd116):
    """The invariant the ambiguity branch rests on.

    PR #338: "exactly one offset per entry satisfies all four
    conditions -- never two." That is what makes a scan deterministic
    rather than a best guess, and it is measured here on all 397 located
    entries, so a future build that starts producing two qualifying
    spans shows up as a real change and not as noise.

    Each accepted span must begin with its own u32 count followed by the
    owning store's key -- the anchor. Checking it here means the count
    the scan settled on is the one it actually parsed, not a coincidence
    further along the entry.
    """
    body, offsets = table
    starts = sorted(offsets.values())
    spans = starts + [len(body)]

    located = 0
    for key, off in offsets.items():
        end = spans[spans.index(off) + 1]
        try:
            recs, start, stop = locate_stock_list(
                body, _entry_payload(body, off), end, key, cd116)
        except StoreListNotFound:
            continue
        located += 1
        assert struct.unpack_from("<I", body, start)[0] == len(recs), (
            "the located span must open with the record count it parsed")
        assert struct.unpack_from("<H", body, start + 4)[0] == key, (
            "the first record's lookup_a must be the owning store key -- "
            "the anchor that rejects false-positive spans")
        assert _entry_payload(body, off) <= start < stop <= end
    assert located == 397
