"""The storeinfo record layout moved again in CD 1.13, and CDUMM missed it.

Reported by @Srimk1 on GitHub #259: donr484's "Shop Smart. Shop H-Mart"
(Nexus 2626) half-applied — **10 of its 14 stores were dropped** with

    store entry 3101: vanilla stock list does not match the verified layout
    (const byte at record offset 34 is 0 (expected 1) at byte 106)

Cause: CD 1.13 inserted a u32 ``order_index_113`` at record offset 30,
which pushed the flags and the const tripwire down four bytes (head 110
-> 114). CDUMM was still reading the CD 1.11 shape, so the tripwire fired
on the very first record of the table and the writer refused the batch.

The tripwire did exactly its job — it stopped a misread record being
written into a table whose only integrity check is the game crashing on
store open. But the layout had been HARDCODED, and this is the *second*
time a patch has moved it (CD 1.11 inserted ``is_restore_item``). So the
layout is now detected from the file, and these tests pin that:

  * the real CD 1.13 table is detected as CD 1.13, not 1.11;
  * detection is decided by a BYTE-EXACT round-trip, not by "it parsed";
  * a table in an older shape still detects as that older shape;
  * a shape we don't know refuses instead of guessing.

The mod itself is the corroborating witness: donr484 had already RE'd the
field and ships it as ``order_index_113`` = 0xFFFFFFFF, which is its value
in all 3,661 vanilla records.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cdumm.engine.storeinfo_native_parser import (
    LAYOUTS, VGAP_SIZE, StoreinfoParseError, _score_layout,
    detect_storeinfo_layout, locate_stock_list, serialize_stock_list)
from cdumm.semantic.parser import parse_pabgh_index

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla113"


def _vanilla(name: str) -> bytes:
    """The real CD 1.13 storeinfo, committed (57 KB zlib) rather than read
    from an env var — the tests that depended on a local extract skipped
    silently in CI, which is how this table's layout drifted two game
    versions without anything going red."""
    return zlib.decompress((_FIXTURES / f"{name}.zlib").read_bytes())

# Measured on the real CD 1.13 table (tests/fixtures/vanilla113/storeinfo).
#
# These numbers moved once already, for a reason worth recording: they
# used to read 268 / 3,661, because the parser located the stock list at
# one fixed payload offset. 82 entries keep it somewhere else, so their
# lists were being missed -- as a *successful* parse of a different u32
# that happened to be 0, i.e. "this store is empty", with no error. The
# entry count went DOWN because provably-empty stores are no longer
# counted as decoded, and the record count went UP by 1,782 because
# those 82 stores are not empty at all.
TOTAL_ENTRIES = 293
DECODED_ENTRIES = 263          # entries with a real, located stock list
DECODED_RECORDS = 5_443
PROVABLY_EMPTY_ENTRIES = 30    # too small to hold one record, at any offset

HERNAND_GENERAL = 3101         # the store that failed first in the report


@pytest.fixture(scope="module")
def table():
    body = _vanilla("storeinfo.pabgb")
    header = _vanilla("storeinfo.pabgh")
    _key_size, offsets = parse_pabgh_index(header, "storeinfo")
    return body, header, offsets


@pytest.fixture(scope="module")
def layout(table):
    body, _header, offsets = table
    return detect_storeinfo_layout(body, sorted(offsets.values()))


def _payload(body: bytes, off: int) -> int:
    name_len = struct.unpack_from("<I", body, off + 2)[0]
    return off + 6 + name_len + 1


# ── the shape of CD 1.13 ────────────────────────────────────────────────

def test_the_real_table_is_detected_as_1_13_not_1_11(layout):
    assert layout.label == "CD 1.13"
    assert layout.order_index_off == 30      # the u32 that moved everything
    assert layout.const_off == 38            # was 34 on CD 1.11
    assert layout.head_size == 114           # was 110
    # the opaque interior did NOT move -- the insert landed before it
    assert layout.head_size - layout.vgap_off == VGAP_SIZE


def test_the_1_11_layout_barely_decodes_this_table(table):
    """The regression this fixes. If CDUMM's old hardcoded layout could
    read the 1.13 table, there would have been no bug — it can't."""
    body, _header, offsets = table
    offs = sorted(offsets.values())
    old = next(c for c in LAYOUTS if c.label == "CD 1.11")
    entries, records = _score_layout(body, offs, old)
    assert records < 10, (
        f"the CD 1.11 layout round-trips {records} records on a 1.13 table; "
        f"if that were a real number the bug would not exist")


def test_detection_beats_every_other_candidate(table, layout):
    body, _header, offsets = table
    offs = sorted(offsets.values())
    best = _score_layout(body, offs, layout)
    assert best == (DECODED_ENTRIES, DECODED_RECORDS)
    for cand in LAYOUTS:
        if cand is layout:
            continue
        assert _score_layout(body, offs, cand) < best


# ── detection is decided by byte-exactness, not by "it parsed" ──────────

def _locate_all(body, offsets, layout):
    """Every entry's located list, plus the ones with no list at all."""
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


def test_every_located_entry_round_trips_byte_exact(table, layout):
    """A wrong layout can consume a plausible-looking span and still be
    misreading it. Byte-exactness is the only test that can't be fooled —
    and it is what both location and detection score on."""
    body, _header, offsets = table
    found, empty = _locate_all(body, offsets, layout)
    for recs, start, end in found.values():
        assert serialize_stock_list(recs, layout) == body[start:end]
    assert len(found) == DECODED_ENTRIES
    assert len(empty) == PROVABLY_EMPTY_ENTRIES


def test_the_stores_with_no_located_list_are_provably_empty(table, layout):
    """The distinction that makes the scan safe. "We could not find a
    list" is only acceptable when the entry is too short to hold one
    record at ANY offset — otherwise we would be silently skipping stock
    the way the old fixed offset did."""
    body, _header, offsets = table
    _found, empty = _locate_all(body, offsets, layout)
    spans = sorted(offsets.values()) + [len(body)]
    for key in empty:
        off = offsets[key]
        end = spans[spans.index(off) + 1]
        with pytest.raises(StoreinfoParseError) as ei:
            locate_stock_list(body, _payload(body, off), end, key, layout)
        assert getattr(ei.value, "provably_empty", False), (
            f"store {key} has room for a record but no list was located")


def test_the_anchor_holds_for_every_located_list(table, layout):
    """Why the scan can trust itself: StockData._storeInfo is a u16 back-
    reference to the owning store, so the first record of an entry's list
    carries that entry's own key. True on all 263, not just a sample."""
    body, _header, offsets = table
    found, _empty = _locate_all(body, offsets, layout)
    for key, (recs, _s, _e) in found.items():
        assert recs[0].lookup_a == key


def test_order_index_is_ffffffff_in_every_vanilla_record(table, layout):
    """0xFFFFFFFF on all of them — which is exactly what the mod supplies
    as `order_index_113`. Two independent sources agreeing is the evidence
    that this field is real and correctly placed."""
    body, _header, offsets = table
    found, _empty = _locate_all(body, offsets, layout)
    seen = 0
    for recs, _s, _e in found.values():
        for rec in recs:
            assert rec.order_index == 0xFFFFFFFF
            assert rec.const33 == 1
            seen += 1
    assert seen == DECODED_RECORDS


def test_the_store_that_broke_now_decodes(table, layout):
    """Store_Her_General (3101) is the entry the report failed on, at the
    very first record."""
    body, _header, offsets = table
    off = offsets[HERNAND_GENERAL]
    spans = sorted(offsets.values()) + [len(body)]
    recs, start, end = locate_stock_list(
        body, _payload(body, off), spans[spans.index(off) + 1],
        HERNAND_GENERAL, layout)
    assert len(recs) == 40
    assert serialize_stock_list(recs, layout) == body[start:end]
    # the flags are clean booleans in the shifted positions
    for rec in recs:
        assert rec.flag_a in (0, 1)
        assert rec.flag_b in (0, 1)
        assert rec.flag_c in (0, 1)
        assert rec.is_restore_item in (0, 1)


# ── old builds still work; unknown builds refuse ────────────────────────

def test_an_older_table_still_detects_as_that_older_shape(table, layout):
    """Detection must not simply always answer "the newest". Re-emit one
    entry's list in the CD 1.11 shape and check it is recognised as 1.11."""
    body, _header, offsets = table
    old = next(c for c in LAYOUTS if c.label == "CD 1.11")
    off = offsets[HERNAND_GENERAL]
    spans = sorted(offsets.values()) + [len(body)]
    recs, _s, _e = locate_stock_list(
        body, _payload(body, off), spans[spans.index(off) + 1],
        HERNAND_GENERAL, layout)

    # A synthetic entry body in the 1.11 shape: header + payload + list.
    name = b"Store_Her_General"
    head = struct.pack("<H", HERNAND_GENERAL) + struct.pack(
        "<I", len(name)) + name + b"\x00"
    payload = bytearray(b"\x00" * old.count_payload_offset)
    synth = bytes(head) + bytes(payload) + serialize_stock_list(recs, old)

    assert detect_storeinfo_layout(synth, [0]).label == "CD 1.11"


def test_a_shape_we_do_not_know_refuses_rather_than_guesses():
    """The whole point of the const tripwire. A table in no known shape
    must raise, not best-effort — a misread record written back is a
    corrupt storeinfo, and the game crashes on store open.

    Note the count must be non-zero: a table whose stock lists are all
    EMPTY is a valid table that happens to decode under every layout, not
    an unknown one, and is asserted separately below.
    """
    name = b"Shop"
    head = struct.pack("<H", 2) + struct.pack("<I", len(name)) + name + b"\x00"
    payload = bytes(44)                    # up to the count
    count = struct.pack("<I", 3)           # claims 3 records...
    garbage = b"\x7f" * 400                # ...that are in no known shape
    with pytest.raises(StoreinfoParseError, match="no known storeinfo"):
        detect_storeinfo_layout(head + payload + count + garbage, [0])


def test_an_all_empty_table_is_valid_not_unknown():
    """A store with no stock is unknowable by construction — nothing in
    it identifies the layout. Treating that as "unknown shape" would turn
    an empty store into a hard error, so detection separates "too small
    to hold a record" from "big enough, but nothing decodes"."""
    name = b"Shop"
    head = struct.pack("<H", 2) + struct.pack("<I", len(name)) + name + b"\x00"
    empty = head + bytes(44) + struct.pack("<I", 0)
    assert detect_storeinfo_layout(empty, [0]) in LAYOUTS
