"""What identifies a storeinfo layout, and what only describes one.

``count_payload_offset`` describes; it does not identify. Since ca313ab
nothing reads it to find a list -- :func:`locate_stock_list` scans for a
span anchored on the store key that parses and re-serializes byte-exactly
-- so a stock count that moves one byte is found on its own, and a layout
differing only in that number is a no-op for every read path. That is
what #351/#352 retracted after measuring through ``parse_stock_list``
instead of ``locate_stock_list``.

The offsets do not identify a layout either, and CD 1.16.1 is the proof.
The 15 Aug 2026 patch (#365) took four bytes out of the *opaque value
interior*, 71 -> 67, and moved nothing ahead of the const tripwire. So
CD 1.16.1 and CD 1.16 carry byte-identical offsets -- (34, 38, 41, 42),
both with ``low_price_threshold=True`` -- and differ only in
``vgap_size``. Any shape key built from the offsets, or from which
optional fields are present, collapses those two into one.

Measured here on the committed vanilla1161 table, they are not remotely
the same reader:

    CD 1.16.1  vgap=67   398 located, 39 empty,   0 not-found
    CD 1.16    vgap=71     3 located, 39 empty, 395 not-found

So the shape key is presence *and* interior width. A future build that
moves a field ahead of the const still shows up in the presence half;
one that eats the interior shows up in the width half.
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

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Measured on the committed CD 1.16 table.
CD116_ENTRIES = 397
CD116_RECORDS = 6_376

# Measured on the committed CD 1.16.1 table (the 15 Aug 2026 patch).
CD1161_ENTRIES = 398
CD1161_RECORDS = 6_378


def _table(version: str):
    d = _FIXTURES / version
    body = zlib.decompress((d / "storeinfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((d / "storeinfo.pabgh.zlib").read_bytes())
    _key_size, offsets = parse_pabgh_index(header, "storeinfo")
    return body, sorted(offsets.values())


def _layout(label: str) -> StoreLayout:
    return next(lay for lay in LAYOUTS if lay.label == label)


def _operative_shape(lay: StoreLayout) -> tuple:
    """What the reader actually keys on.

    Presence of the optional fields, because the reader consumes fields
    sequentially and the numbers are descriptive -- plus the interior
    width, because that is what CD 1.16.1 changed while leaving every
    offset alone.

    Four fields are deliberately excluded, all for the same reason: they
    describe *writing* a new record, not reading an existing one.
    ``raw_e_off`` / ``raw_g_off`` / ``raw_q_off`` are where the writer
    maps three values inside the opaque interior, and
    ``vgap_map_verified`` says whether those indices are trusted on this
    build. An existing record's interior is carried through verbatim, so
    none of the four changes how a byte is read -- and two layouts that
    differed only there would still be indistinguishable from the file,
    which is what this key exists to detect.

    (#367 moved 1.16.1's three to 37/53/55 and flipped the flag to True,
    which is precisely a write-side change with no read-side effect.)
    """
    return (lay.order_index_off is None,
            lay.is_restore_off is None,
            lay.low_price_threshold,
            lay.vgap_size)


@pytest.fixture(scope="module")
def cd116_table():
    return _table("vanilla116")


@pytest.fixture(scope="module")
def cd1161_table():
    return _table("vanilla1161")


def test_shipped_layouts_decode_their_own_tables(cd116_table, cd1161_table):
    body, offs = cd116_table
    assert _score_layout(body, offs, _layout("CD 1.16")) == (
        CD116_ENTRIES, CD116_RECORDS)
    body, offs = cd1161_table
    assert _score_layout(body, offs, _layout("CD 1.16.1")) == (
        CD1161_ENTRIES, CD1161_RECORDS)


def test_moving_only_the_count_offset_changes_nothing(cd116_table):
    """The change #352 proposed: same shape, count 44 -> 45. If this ever
    stops being a no-op, locate_stock_list has regressed back to trusting
    a constant."""
    body, offs = cd116_table
    cd116 = _layout("CD 1.16")
    moved = StoreLayout(
        "CD 1.16-count-moved",
        cd116.count_payload_offset + 1,
        cd116.order_index_off,
        cd116.flags_off,
        cd116.is_restore_off,
        cd116.const_off,
        low_price_threshold=cd116.low_price_threshold,
        vgap_size=cd116.vgap_size,
    )
    assert _score_layout(body, offs, moved) == _score_layout(body, offs, cd116)


def test_the_interior_width_is_operative(cd1161_table):
    """The #365 lesson, measured. CD 1.16 and CD 1.16.1 differ in nothing
    but vgap_size, and on the 15 Aug table that is the difference between
    reading it and not reading it at all."""
    body, offs = cd1161_table
    good = _score_layout(body, offs, _layout("CD 1.16.1"))
    stale = _score_layout(body, offs, _layout("CD 1.16"))
    assert good == (CD1161_ENTRIES, CD1161_RECORDS)
    assert stale[0] < 10, (
        "the pre-1.16.1 interior width must not decode this table; if it "
        "does, vgap_size has stopped mattering and this guard is moot")


def test_no_two_layouts_share_a_shape():
    """Two candidates identical in every operative field score
    identically, and detection then has nothing to separate them.

    This is keyed on presence AND interior width because #365 defeated
    both weaker keys: CD 1.16.1's offset tuple is byte-identical to
    CD 1.16's, so a number-keyed check misses it, and its optional-field
    presence is identical too, so a presence-only key misses it as well.
    """
    shapes = [_operative_shape(lay) for lay in LAYOUTS]
    assert len(shapes) == len(set(shapes)), (
        "two LAYOUTS entries describe the same record shape; detection "
        "cannot tell them apart on the evidence in the file")


def test_dropping_the_1_16_u32_would_reproduce_the_cd113_reader(cd116_table):
    """Guards the shape key's presence half.

    This is a hypothetical, NOT what the 15 Aug patch did -- that one ate
    the interior and is covered above. But a layout carrying 1.16's
    offsets with low_price_threshold=False is byte-for-byte the CD 1.13
    reader while its offset tuple (34,38,41,42) differs from CD 1.13's
    (30,34,37,38), so it is the case the presence half exists to catch.
    """
    body, offs = cd116_table
    cd116 = _layout("CD 1.16")
    cd113 = _layout("CD 1.13")
    dropped = StoreLayout(
        "CD 1.16-minus-lowprice",
        cd116.count_payload_offset,
        cd116.order_index_off,
        cd116.flags_off,
        cd116.is_restore_off,
        cd116.const_off,
        low_price_threshold=False,
        vgap_size=cd113.vgap_size,
    )
    assert _operative_shape(dropped) == _operative_shape(cd113)
    assert _score_layout(body, offs, dropped) == _score_layout(body, offs, cd113)
