"""equipslotinfo: a canary row for a layout constant that is not constant.

#190 (Character Creator's Female Rapier and Shield Module) needed a
writer for ``entries[N].etl_hashes``. The hard part was that the opaque
per-record block is 66 bytes on CD 1.10 and 63 on CD 1.15, so the
originally hardcoded 66 desynced at the second record of every
multi-record entry: the writer refused every intent and the mod applied
nothing WHILE REPORTING NO SKIPS.

That is the silent-no-op shape this whole canary exists for, and the
table had no row. These tests add one and prove it is not vacuous.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine.equipslotinfo_writer import (
    EquipslotWriteRefused,
    _entry_spans,
    derive_fixed_block,
    parse_entry_records,
    serialize_entry_payload,
)

_FIX = Path(__file__).parent / "fixtures" / "vanilla115"
_TABLE = "equipslotinfo"

pytestmark = pytest.mark.skipif(
    not (_FIX / f"{_TABLE}.pabgb.zlib").exists(),
    reason=f"{_TABLE} fixture not committed")


def _table() -> tuple[bytes, bytes]:
    return (zlib.decompress((_FIX / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((_FIX / f"{_TABLE}.pabgh.zlib").read_bytes()))


def test_the_committed_table_reads_completely():
    body, header = _table()
    ok, detail = puc.check_equipslotinfo_records(body, header)
    assert ok, detail
    assert "block 63" in detail
    assert "17/17 entries parse" in detail
    assert "0 refused" in detail
    assert "0 mis-round-tripped" in detail
    assert "223 records" in detail


def test_the_row_is_pinned_so_it_gates():
    assert (_FIX.name, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating, "the row runs but does not gate the exit code"


def test_the_block_size_is_uniquely_determined():
    """63 is not merely *a* size that works -- it is the only one.

    This is what makes `derive_fixed_block` trustworthy rather than
    lucky. If several sizes tiled the whole table the derivation would
    be picking arbitrarily, and the writer would sometimes position a
    write correctly by coincidence. Swept over the writer's full search
    bound, exactly one candidate survives.
    """
    body, header = _table()
    spans = _entry_spans(body, header)
    winners = []
    for block in range(256):
        for _key, payload, end in spans:
            try:
                unk, recs, footer = parse_entry_records(
                    body, payload, end, block)
            except EquipslotWriteRefused:
                break
            if serialize_entry_payload(
                    unk, recs, footer, block) != body[payload:end]:
                break
        else:
            winners.append(block)
    assert winners == [63], f"block size is not uniquely determined: {winners}"
    assert derive_fixed_block(body, header) == 63


def test_the_hardcoded_66_really_does_break_this_table():
    """The #190 regression, driven over the bytes rather than described.

    66 is the CD 1.10 size and was the hardcoded default. On this CD 1.15
    table it must not silently produce a plausible parse -- that is what
    made the original bug invisible.
    """
    body, header = _table()
    spans = _entry_spans(body, header)
    survived = 0
    for _key, payload, end in spans:
        try:
            unk, recs, footer = parse_entry_records(body, payload, end, 66)
        except EquipslotWriteRefused:
            continue
        if serialize_entry_payload(unk, recs, footer, 66) == body[payload:end]:
            survived += 1
    assert survived < len(spans), (
        "the legacy 66-byte block round-tripped every entry, so the "
        "#190 break would not be detectable on this table")


def test_a_broken_record_walk_fails_the_row():
    """A green row that stays green through a break is worse than no row."""
    body, header = _table()
    spans = _entry_spans(body, header)
    _key, payload, _end = spans[0]

    broken = bytearray(body)
    # An etl_count past the writer's plausibility bound: the shape a
    # desynced walk produces when it lands mid-record.
    struct.pack_into("<I", broken, payload + 6, 999)

    ok, detail = puc.check_equipslotinfo_records(bytes(broken), header)
    assert not ok, f"a desynced record walk was accepted: {detail}"


def test_an_underivable_block_size_names_the_consequence():
    """When the size stops being derivable the row must say what the
    user loses, not just that a function raised."""
    body, header = _table()
    # Truncating the body leaves no candidate tiling every entry.
    ok, detail = puc.check_equipslotinfo_records(body[:len(body) // 2], header)
    assert not ok
    assert "derivable" in detail or "refused" in detail
    assert "apply nothing" in detail or "refused rather than applied" in detail
