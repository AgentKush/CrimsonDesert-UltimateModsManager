"""stringinfo: round-trip is necessary and nowhere near sufficient.

#224 (Female Armor Module and the character-creator supplements) needed
a writer for stringinfo's variable-length ``_buffer`` field. Editing one
record changes its length, so the companion .pabgh offsets are rebuilt
-- a two-file contract like storeinfo and multichangeinfo.

The reason this table needs its own row, rather than trusting the
round-trip the writer already documents, is proved below: a layout
change can leave EVERY record unreadable while the no-op round-trip
stays byte-exact, because the no-op path re-emits records verbatim and
never inspects the buffer framing. That is the third distinct mechanism
in this canary producing the same silent no-op -- after iteminfo's
opaque fallback and #190's desynced record walk.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine.stringinfo_writer import (
    _record_bounds,
    apply_stringinfo,
    build_pabgh,
    parse_pabgh,
)

_FIXTURES = ("vanilla110", "vanilla115")
_TABLE = "stringinfo"
_ROOT = Path(__file__).parent / "fixtures"


def _table(version: str) -> tuple[bytes, bytes]:
    d = _ROOT / version
    return (zlib.decompress((d / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((d / f"{_TABLE}.pabgh.zlib").read_bytes()))


def _have(version: str) -> bool:
    return (_ROOT / version / f"{_TABLE}.pabgb.zlib").exists()


@pytest.mark.parametrize("version", _FIXTURES)
def test_every_committed_build_reaches_the_round_trip_floor(version):
    if not _have(version):
        pytest.skip(f"{version}/{_TABLE} fixture not committed")
    body, header = _table(version)
    ok, detail = puc.check_stringinfo_records(body, header)
    assert ok, detail
    assert "empty-intent round-trip byte-exact" in detail
    assert "pabgh rebuild byte-exact" in detail


@pytest.mark.parametrize("version", _FIXTURES)
def test_both_builds_are_pinned_so_they_gate(version):
    if not _have(version):
        pytest.skip(f"{version}/{_TABLE} fixture not committed")
    assert (version, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(version,))
    row = [r for r in rows if r[0] == f"{version}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating


def test_the_writer_docstring_build_matches_the_committed_1_10_table():
    """The writer cites 30,940 records for build 23831243. The 1.10
    fixture has exactly that, so the prose is confirmed against bytes
    rather than left to drift the way npcinfo's 452 did."""
    if not _have("vanilla110"):
        pytest.skip("vanilla110 stringinfo fixture not committed")
    _body, header = _table("vanilla110")
    assert len(parse_pabgh(header)) == 30940


def test_round_trip_alone_would_miss_a_total_layout_break():
    """The point of the layout-match gate, demonstrated.

    Insert one byte into the first record. Every subsequent record's
    declared buffer length now fails to consume its record, so NOTHING
    is editable -- yet apply_stringinfo with no intents still returns
    the input byte-for-byte, because the no-op path re-emits records
    verbatim and never reads the buffer framing.

    A row that gated only on round-trip would stay green through this.
    """
    if not _have("vanilla110"):
        pytest.skip("vanilla110 stringinfo fixture not committed")
    body, header = _table("vanilla110")
    bounds = _record_bounds(body, header)
    start = min(s for s, _e in bounds.values())

    broken = bytearray(body)
    broken[start + 9:start + 9] = b"\x00"
    broken = bytes(broken)

    # The round-trip is still perfect...
    new_body, new_header = apply_stringinfo(broken, header, {})
    assert new_body == broken and new_header == header, (
        "the premise of this test is that round-trip survives; it did not")

    # ...and yet the row must fail, because nothing is editable.
    ok, detail = puc.check_stringinfo_records(broken, header)
    assert not ok, detail
    assert "0/30940 match" in detail
    assert "silently dropped" in detail


def test_one_unreadable_record_is_enough_to_fail():
    """Completeness, not a majority. Every record here is a string
    record, so there is no population that legitimately fails to match."""
    if not _have("vanilla110"):
        pytest.skip("vanilla110 stringinfo fixture not committed")
    body, header = _table("vanilla110")
    bounds = _record_bounds(body, header)
    start = min(s for s, _e in bounds.values())

    broken = bytearray(body)
    struct.pack_into("<I", broken, start + 9, 999999)

    ok, detail = puc.check_stringinfo_records(bytes(broken), header)
    assert not ok, f"30939 of 30940 was accepted: {detail}"
    assert "30939/30940 match" in detail


def test_a_moved_index_framing_fails_the_row():
    """The .pabgh is the other half of the contract; if its framing
    moves, offsets resolve to the wrong records and every edit lands in
    the wrong string."""
    if not _have("vanilla110"):
        pytest.skip("vanilla110 stringinfo fixture not committed")
    body, header = _table("vanilla110")
    broken = bytearray(header)
    struct.pack_into("<H", broken, 0,
                     struct.unpack_from("<H", header, 0)[0] - 1)

    ok, detail = puc.check_stringinfo_records(body, bytes(broken))
    assert not ok, detail
    assert "BROKEN" in detail


def test_the_index_round_trips_on_its_own():
    if not _have("vanilla110"):
        pytest.skip("vanilla110 stringinfo fixture not committed")
    _body, header = _table("vanilla110")
    assert build_pabgh(parse_pabgh(header)) == header
