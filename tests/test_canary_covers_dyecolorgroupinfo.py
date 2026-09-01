"""dyecolorgroupinfo had a writer and committed bytes but no canary row.

That is the exact gap npcinfo was in before #393 got a row: a table CDUMM
can write, whose layout nothing re-derives when the game patches. #191
added the writer (AerowynX's dye addon appends 22 colours to each of the
ten groups) and #397 committed the b24994088 capture, so the bytes to
check against were already in the repo.

These tests pin the row and, more importantly, pin that it is not
vacuous -- a green row that stays green through a moved layout is worse
than no row, which is the lesson the `iteminfo` ordered walk taught on
buildid 24773079.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine.dyecolorgroupinfo_writer import (
    DyecolorgroupinfoWriteRefused,
    locate_color_list,
)
from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index

_FIX = Path(__file__).parent / "fixtures" / "vanilla_b24994088"
_TABLE = "dyecolorgroupinfo"

pytestmark = pytest.mark.skipif(
    not (_FIX / f"{_TABLE}.pabgb.zlib").exists(),
    reason=f"{_TABLE} fixture not committed")


def _table() -> tuple[bytes, bytes]:
    return (zlib.decompress((_FIX / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((_FIX / f"{_TABLE}.pabgh.zlib").read_bytes()))


def _payloads(body: bytes, header: bytes) -> dict[int, int]:
    """key -> payload offset, via the production header index."""
    key_size, offs = parse_pabgh_index(header, _TABLE)
    return {key: _parse_entry_header(body, off, key_size)[2]
            for key, off in offs.items()}


def test_the_committed_table_reads_completely():
    """Ten groups, all ten tile, 1,090 colours, nothing refused."""
    body, header = _table()
    ok, detail = puc.check_dyecolorgroupinfo_color_lists(body, header)
    assert ok, detail
    assert "10/10 groups tile" in detail
    assert "0 refused" in detail
    assert "0 mis-round-tripped" in detail
    assert "1090 colours" in detail


def test_the_row_is_pinned_so_it_gates():
    """Present but unpinned would report without failing anything."""
    assert (_FIX.name, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating, "the row runs but does not gate the exit code"


def test_the_writer_docstring_agrees_with_the_bytes():
    """npcinfo's commit message said 452 and the bytes said 462. Here the
    LAYOUT note says "every vanilla group has exactly 109 colours" and
    "exact tiling on all 10 groups" -- assert that, so it cannot drift
    into being wrong the way the npcinfo figure did."""
    body, header = _table()
    payloads = _payloads(body, header)
    key_size, offs = parse_pabgh_index(header, _TABLE)
    spans = sorted(offs.values()) + [len(body)]

    assert len(offs) == 10, f"the table is not ten groups: {len(offs)}"
    for key, off in offs.items():
        end = spans[spans.index(off) + 1]
        _s, stop, elems = locate_color_list(body, payloads[key], end, key)
        assert len(elems) == 109, f"group {key} carries {len(elems)}"
        # The tail is carried verbatim; its only variation is the length
        # of the group's name string, so it stays inside a narrow band.
        assert 33 <= end - stop <= 37, f"group {key} tail {end - stop}"


def test_one_broken_group_is_enough_to_fail():
    """The gate is completeness, NOT "something tiled".

    This is where the row differs from npcinfo's, and the difference is
    the point. npcinfo is 542 NPCs of which ~80 legitimately refuse
    because they are not Dyers, so its gate can only be "not everything
    refuses". Every entry in this table IS a colour group, so a single
    refusal already means the layout moved -- and a dropped append is
    silent: the addon installs and validates, the player just never sees
    the new swatches.
    """
    body, header = _table()
    payloads = _payloads(body, header)
    victim = sorted(payloads)[0]

    broken = bytearray(body)
    # A count that cannot tile the payload -- the shape a moved layout
    # produces when the reader lands on some other field.
    struct.pack_into("<I", broken, payloads[victim], 0xFFFF)

    ok, detail = puc.check_dyecolorgroupinfo_color_lists(bytes(broken), header)
    assert not ok, f"nine of ten tiling was accepted: {detail}"
    assert "9/10 groups tile" in detail
    assert "1 refused" in detail
    assert "dye-addon appends will be dropped" in detail


def test_the_layout_is_not_loosely_satisfiable():
    """A four-byte shift in EITHER direction refuses all ten groups.

    Worth pinning because it is what makes the row trustworthy: the
    check is not merely finding some plausible count somewhere near the
    payload. If the engine inserts or removes a u32 ahead of the list --
    the exact thing CD 2.0 did to iteminfo -- every group refuses and
    the canary says so, rather than tiling on garbage.
    """
    body, header = _table()
    payloads = _payloads(body, header)
    key_size, offs = parse_pabgh_index(header, _TABLE)
    spans = sorted(offs.values()) + [len(body)]

    for shift in (-4, 4, 8):
        refused = 0
        for key, off in offs.items():
            end = spans[spans.index(off) + 1]
            try:
                locate_color_list(body, payloads[key] + shift, end, key)
            except DyecolorgroupinfoWriteRefused:
                refused += 1
        assert refused == len(offs), (
            f"shifting the list start by {shift} still tiled "
            f"{len(offs) - refused} group(s); the layout is loose")
