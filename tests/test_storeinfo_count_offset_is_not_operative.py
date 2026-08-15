"""``count_payload_offset`` does not select a layout, and cannot fix one.

This table's history is four rounds of the same mistake. #351/#352
measured a "format regression" on CD 1.16.03 by driving
``parse_stock_list`` with a constant instead of ``locate_stock_list``,
read the resulting garbage as drift, and both authors retracted it --
after two people lost a day. The fix that actually shipped was ca313ab,
"Locate the storeinfo stock list instead of computing its offset". The
branch that proposed moving the count 44 -> 45 was never merged, and the
tests here are why it never needed to be.

Since ca313ab nothing reads ``count_payload_offset`` to find a list.
:func:`locate_stock_list` scans the entry for a span that is anchored on
the store key, parses in the candidate's shape, and re-serializes
byte-exactly. A stock count that moves one byte is therefore found
automatically, and a new layout differing only in that number is a
no-op for every read path.

What a real record-shape change looks like is the opposite: CD 1.11
inserted ``is_restore_item``, 1.13 inserted ``order_index_113``, 1.16
inserted ``_lowPriceThresholdCount``, and each one shows up as a
*different score*, because the reader consumes fields sequentially.
That is the signal. The count offset is not.

If a future build genuinely moves the record shape, these tests keep
passing and ``detect_storeinfo_layout`` picks the new shape on score --
so this pins the invariant without pinning the game.
"""
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from cdumm.engine.storeinfo_native_parser import (
    LAYOUTS,
    StoreLayout,
    _score_layout,
)
from cdumm.semantic.parser import parse_pabgh_index

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla116"

# Measured on the committed CD 1.16 table, and identical under both
# candidates below -- which is the whole point.
EXPECTED_ENTRIES = 397
EXPECTED_RECORDS = 6_376


@pytest.fixture(scope="module")
def table():
    body = zlib.decompress((_FIXTURES / "storeinfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIXTURES / "storeinfo.pabgh.zlib").read_bytes())
    _key_size, offsets = parse_pabgh_index(header, "storeinfo")
    return body, sorted(offsets.values())


@pytest.fixture(scope="module")
def cd116() -> StoreLayout:
    live = [lay for lay in LAYOUTS if lay.label == "CD 1.16"]
    assert live, "CD 1.16 layout must stay in LAYOUTS"
    return live[0]


def test_shipped_layout_decodes_the_committed_table(table, cd116):
    body, offs = table
    assert _score_layout(body, offs, cd116) == (EXPECTED_ENTRIES, EXPECTED_RECORDS)


def test_moving_only_the_count_offset_changes_nothing(table, cd116):
    """The exact change the unmerged 1.16.03 branch proposed: same shape,
    count 44 -> 45. If this ever stops being a no-op, locate_stock_list
    has regressed back to trusting a constant."""
    body, offs = table
    moved = StoreLayout(
        "CD 1.16.03-hypothetical",
        cd116.count_payload_offset + 1,
        cd116.order_index_off,
        cd116.flags_off,
        cd116.is_restore_off,
        cd116.const_off,
        low_price_threshold=cd116.low_price_threshold,
    )
    assert _score_layout(body, offs, moved) == _score_layout(body, offs, cd116)


def test_a_real_shape_change_does_move_the_score(table, cd116):
    """Contrast: drop the u32 CD 1.16 actually inserted and the score
    collapses. This is what genuine drift looks like, and it is why a
    count-offset edit is not a fix for it."""
    body, offs = table
    without = StoreLayout(
        "CD 1.16-without-lowprice",
        cd116.count_payload_offset,
        cd116.order_index_off,
        cd116.flags_off,
        cd116.is_restore_off,
        cd116.const_off,
        low_price_threshold=False,
    )
    entries, _records = _score_layout(body, offs, without)
    assert entries < EXPECTED_ENTRIES


def test_no_two_layouts_share_a_shape():
    """Two candidates identical in every operative field score
    identically, and detect_storeinfo_layout breaks the tie on LAYOUTS
    order alone -- so whichever was listed first would silently own
    DEFAULT_LAYOUT. Adding a build that only renames an existing shape
    is how that happens; this refuses it at import time."""
    shapes = [
        (lay.order_index_off, lay.flags_off, lay.is_restore_off,
         lay.const_off, lay.low_price_threshold)
        for lay in LAYOUTS
    ]
    assert len(shapes) == len(set(shapes)), (
        "two LAYOUTS entries describe the same record shape; detection "
        "cannot tell them apart and the tie falls to list order")
