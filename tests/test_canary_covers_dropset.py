"""dropsetinfo: the parse gate and the round-trip gate are independent.

`dropset_writer` is the port of NattKh's dropset_editor, and it is what
Format 3 `{op:set, field:drops}` intents go through. The DropSet layout
is the most variable of the tables this canary checks: two embedded
length-prefixed strings, a counted drops list whose elements carry
optional trailing fields, and a trailer.

Two properties are asserted, and the interesting result is that neither
subsumes the other -- measured below on a real record.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine.dropset_writer import (
    parse_dropset_record,
    serialize_dropset_record,
)
from cdumm.semantic.parser import parse_pabgh_index

_FIX = Path(__file__).parent / "fixtures" / "vanilla113"
_TABLE = "dropsetinfo"

pytestmark = pytest.mark.skipif(
    not (_FIX / f"{_TABLE}.pabgb.zlib").exists(),
    reason=f"{_TABLE} fixture not committed")


def _table() -> tuple[bytes, bytes]:
    return (zlib.decompress((_FIX / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((_FIX / f"{_TABLE}.pabgh.zlib").read_bytes()))


def _records(body: bytes, header: bytes) -> list[tuple[int, bytes]]:
    _ks, offs = parse_pabgh_index(header, _TABLE)
    starts = sorted(offs.values())
    spans = starts + [len(body)]
    return [(key, body[off:spans[spans.index(off) + 1]])
            for key, off in offs.items()]


def _first_multi_drop_record(body: bytes, header: bytes) -> bytes:
    for _key, rec in _records(body, header):
        if len(parse_dropset_record(rec).drops) >= 2:
            return rec
    pytest.skip("no record with two or more drops")
    raise AssertionError  # unreachable, satisfies the type checker


def test_the_committed_table_parses_and_round_trips_completely():
    body, header = _table()
    ok, detail = puc.check_dropset_records(body, header)
    assert ok, detail
    assert "14575/14575 records parse" in detail
    assert "0 failed" in detail
    assert "0 mis-round-tripped" in detail
    assert "17920 drops" in detail


def test_the_row_is_pinned_so_it_gates():
    assert (_FIX.name, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating


def test_the_writer_docstring_count_matches_the_bytes():
    """`build_drop_append_change` cites "all 14,575 vanilla records".
    The committed 1.13 table has exactly that, so the claim is verified
    against bytes rather than left to drift the way npcinfo's 452 did."""
    body, header = _table()
    assert len(_records(body, header)) == 14575


def test_one_unparseable_record_is_enough_to_fail():
    """Completeness, not a majority: every record here is a DropSet."""
    body, header = _table()
    _ks, offs = parse_pabgh_index(header, _TABLE)
    off = min(offs.values())

    # Walk the header to the drop_count and inflate it by one, so the
    # element walk overruns and the parser refuses to consume exactly.
    name_len = struct.unpack_from("<I", body, off + 4)[0]
    pos = off + 8 + name_len + 1 + 1 + 4
    dcs_len = struct.unpack_from("<I", body, pos)[0]
    pos += 4 + dcs_len + 4                       # skip dcs + tag hash

    broken = bytearray(body)
    struct.pack_into("<I", broken, pos,
                     struct.unpack_from("<I", body, pos)[0] + 1)

    ok, detail = puc.check_dropset_records(bytes(broken), header)
    assert not ok, f"14574 of 14575 was accepted: {detail}"
    assert "14574/14575 records parse" in detail
    assert "1 failed" in detail


def test_a_shifted_record_boundary_fails_every_record():
    """Inserting a byte desyncs every offset after it, so the whole
    table stops parsing rather than degrading quietly."""
    body, header = _table()
    _ks, offs = parse_pabgh_index(header, _TABLE)
    off = min(offs.values())
    name_len = struct.unpack_from("<I", body, off + 4)[0]

    broken = bytearray(body)
    broken[off + 8 + name_len:off + 8 + name_len] = b"\x00"

    ok, detail = puc.check_dropset_records(bytes(broken), header)
    assert not ok
    assert "0/14575 records parse" in detail


def test_round_trip_catches_what_the_parse_gate_does_not():
    """The two gates are independent, measured rather than assumed.

    Flip each byte of one real record in turn. Some flips make the
    parser refuse; a DISJOINT set parse perfectly well and then fail to
    rebuild their own bytes. If round-trip were implied by a successful
    parse, that second set would be empty.
    """
    body, header = _table()
    rec = _first_multi_drop_record(body, header)

    parse_fail = rt_fail = clean = 0
    for i in range(len(rec)):
        mutated = bytearray(rec)
        mutated[i] ^= 0xFF
        mutated = bytes(mutated)
        try:
            parsed = parse_dropset_record(mutated)
        except Exception:                        # noqa: BLE001
            parse_fail += 1
            continue
        if serialize_dropset_record(parsed) != mutated:
            rt_fail += 1
        else:
            clean += 1

    assert parse_fail > 0, "no flip in this record broke the parser"
    assert rt_fail > 0, (
        "every flip that parsed also round-tripped, so the round-trip "
        "gate would be redundant on this table")
    assert parse_fail + rt_fail + clean == len(rec)


def test_a_non_ascii_string_is_lossy_and_the_row_would_see_it():
    """Why that second gate is worth keeping, concretely.

    The parser decodes the name and drop_condition_string with
    ``errors="replace"``, which is lossy: a non-ASCII byte becomes
    U+FFFD and re-encodes as something else, so the record stops
    round-tripping.

    Nothing is wrong today -- all 14,575 vanilla records have ASCII
    names and NOT ONE carries a condition string at all. The asymmetry
    is what matters for the future. ``build_drops_replacement_change``
    guards the header (key + name_len + name) and returns None if it
    changed, so a non-ASCII *name* is caught there. A condition string
    sits in the BODY, past that guard, so if a later build introduced a
    non-ASCII one the header check would pass and the body would be
    written back with the string mangled -- a silent corruption rather
    than a silent no-op.

    This row is what would catch it, so the property is pinned here.
    """
    body, header = _table()
    rec = _first_multi_drop_record(body, header)

    # Byte 8 is the first character of the name (key u32 + name_len u32).
    mutated = bytearray(rec)
    mutated[8] ^= 0xFF
    mutated = bytes(mutated)

    parsed = parse_dropset_record(mutated)        # parses fine
    assert "�" in parsed.name, "expected a replacement character"
    assert serialize_dropset_record(parsed) != mutated, (
        "the lossy decode round-tripped, so this test no longer "
        "describes the writer")

    # And the vanilla table is genuinely clean, which is why this is
    # latent rather than live.
    assert all(
        not any(ord(c) > 127 for c in ds.name + ds.drop_condition_string)
        for ds in (parse_dropset_record(r) for _k, r in _records(body, header))
    )
