"""CD 1.16 broke storeinfo completely, and nothing went red.

Under the CD 1.13 shape the live 1.16 table decodes ZERO entries, so
every store mod silently stopped applying. That is the third time this
table's record shape has moved (1.11 inserted ``is_restore_item``, 1.13
inserted ``order_index_113``, 1.16 inserts ``_lowPriceThresholdCount``),
and the second time it went unnoticed until a user reported it.

The reason it keeps going unnoticed is that the only 1.16-era table lived
on whoever's machine had the game installed. So the table is committed
here, next to the 1.13 one, and both are pinned by the same assertions.

Every number below was measured on the committed fixture. Nothing is
asserted that isn't byte-exact.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cdumm.engine.storeinfo_native_parser import (
    LAYOUTS,
    ORDER_ELEM_SIZE,
    StoreinfoParseError,
    StoreListNotFound,
    _score_layout,
    detect_storeinfo_layout,
    locate_stock_list,
    serialize_stock_list,
)
from cdumm.semantic.parser import parse_pabgh_index

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla116"

# Measured on the real CD 1.16 table (tests/fixtures/vanilla116/storeinfo).
TOTAL_ENTRIES = 432
LOCATED_ENTRIES = 397
LOCATED_RECORDS = 6_376
PROVABLY_EMPTY_ENTRIES = 35
ORDER_ELEMENTS = 1_063


def _vanilla(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / f"{name}.zlib").read_bytes())


@pytest.fixture(scope="module")
def table():
    body = _vanilla("storeinfo.pabgb")
    header = _vanilla("storeinfo.pabgh")
    _key_size, offsets = parse_pabgh_index(header, "storeinfo")
    return body, offsets


@pytest.fixture(scope="module")
def layout(table):
    body, offsets = table
    return detect_storeinfo_layout(body, sorted(offsets.values()))


def _payload(body: bytes, off: int) -> int:
    return off + 6 + struct.unpack_from("<I", body, off + 2)[0] + 1


def _locate_all(body, offsets, layout):
    spans = sorted(offsets.values()) + [len(body)]
    found, empty = {}, []
    for key, off in offsets.items():
        end = spans[spans.index(off) + 1]
        try:
            found[key] = locate_stock_list(
                body, _payload(body, off), end, key, layout)
        except StoreinfoParseError:
            empty.append(key)
    return found, empty


# ── the break ───────────────────────────────────────────────────────────

def test_the_previous_layout_decodes_essentially_nothing(table):
    """The regression this fixes, stated as a number.

    CD 1.13's shape finds 3 single-record lists in 432 entries — 3 of the
    6,376 records that are really there, 0.05%. They are accidents that
    happen to satisfy every check, which is why detection is comparative
    (scores are ranked) rather than a pass/fail on one candidate.
    """
    body, offsets = table
    old = next(c for c in LAYOUTS if c.label == "CD 1.13")
    entries, records = _score_layout(body, sorted(offsets.values()), old)
    assert (entries, records) == (3, 3)
    assert records < LOCATED_RECORDS / 1000


def test_the_real_table_is_detected_as_1_16(layout):
    assert layout.label == "CD 1.16"
    assert layout.low_price_threshold is True
    # the insert landed before the opaque interior, as every previous one
    # did, so the tail did not move
    assert layout.head_size == 118          # was 114 on CD 1.13


def test_detection_beats_every_other_candidate(table, layout):
    body, offsets = table
    offs = sorted(offsets.values())
    best = _score_layout(body, offs, layout)
    assert best == (LOCATED_ENTRIES, LOCATED_RECORDS)
    for cand in LAYOUTS:
        if cand is not layout:
            assert _score_layout(body, offs, cand) < best


# ── every entry is accounted for ────────────────────────────────────────

def test_every_located_entry_round_trips_byte_exact(table, layout):
    body, offsets = table
    found, empty = _locate_all(body, offsets, layout)
    for recs, start, end in found.values():
        assert serialize_stock_list(recs, layout) == body[start:end]
    assert len(found) == LOCATED_ENTRIES
    assert sum(len(r) for r, _s, _e in found.values()) == LOCATED_RECORDS
    assert len(empty) == PROVABLY_EMPTY_ENTRIES


def test_no_entry_is_left_unexplained(table, layout):
    """The claim that matters: located byte-exact, or provably too small
    to hold a record. There is no third category — a "we could not read
    this one" bucket is exactly how 71 stocked stores were being reported
    as empty before."""
    body, offsets = table
    found, empty = _locate_all(body, offsets, layout)
    assert len(found) + len(empty) == TOTAL_ENTRIES == len(offsets)
    spans = sorted(offsets.values()) + [len(body)]
    for key in empty:
        off = offsets[key]
        with pytest.raises(StoreListNotFound) as ei:
            locate_stock_list(body, _payload(body, off),
                              spans[spans.index(off) + 1], key, layout)
        assert ei.value.provably_empty


def test_the_anchor_holds_for_every_located_list(table, layout):
    """What makes locating the list safe: StockData._storeInfo is a u16
    back-reference to the owning store, so the first record of an entry's
    list carries that entry's own key."""
    body, offsets = table
    found, _empty = _locate_all(body, offsets, layout)
    for key, (recs, _s, _e) in found.items():
        assert recs[0].lookup_a == key


def test_order_count_lists_decode_rather_than_refuse(table, layout):
    """_orderCountDataList used to be refused outright, costing whole
    entries. Its element size was derived by exact tiling, so the proof
    that 12 is right is that these all round-trip byte-exact above."""
    body, offsets = table
    found, _empty = _locate_all(body, offsets, layout)
    seen = [el for recs, _s, _e in found.values()
            for rec in recs for el in rec.effect_list]
    assert len(seen) == ORDER_ELEMENTS
    assert all(len(el) == ORDER_ELEM_SIZE for el in seen)
