"""statusinfo: re-deriving an offset that needed an external artefact.

The DIRECT SPEED presets on Nexus set `stat_level_data[0..15]` on four
"rate" stats. The writer places those elements arithmetically inside a
fixed 212-byte tail, so there is no locate/serialize pair to drive and
this row has a different shape from the others.

The interesting property is the element offset. The writer's docstring
records that 68 was settled only by mod 2511's PAZ overlay -- 68 and 76
both partition the tail into whole uint64s, and both leave every element
a multiple of 2**24, so neither a boundary argument nor the fixed-point
scale tells them apart. The overlay is not in this repo.

The committed table separates them anyway, and that is what makes a
canary row possible here.
"""
from __future__ import annotations

import itertools
import struct
import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine.statusinfo_writer import (
    _FRACTION_BITS,
    _RATE_TAIL_LEN,
    _SLD_COUNT,
    _SLD_TAIL_OFFSET,
)
from cdumm.semantic.parser import parse_pabgh_index

_FIX = Path(__file__).parent / "fixtures" / "vanilla113"
_TABLE = "statusinfo"

pytestmark = pytest.mark.skipif(
    not (_FIX / f"{_TABLE}.pabgb.zlib").exists(),
    reason=f"{_TABLE} fixture not committed")


def _table() -> tuple[bytes, bytes]:
    return (zlib.decompress((_FIX / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((_FIX / f"{_TABLE}.pabgh.zlib").read_bytes()))


def _tails(body: bytes, header: bytes) -> dict[str, bytes]:
    """name -> tail bytes, for the records that carry a ramp."""
    _ks, offs = parse_pabgh_index(header, _TABLE)
    spans = sorted(offs.values()) + [len(body)]
    out = {}
    for off in offs.values():
        end = spans[spans.index(off) + 1]
        name_len = struct.unpack_from("<I", body, off + 4)[0]
        name = body[off + 8:off + 8 + name_len].decode("ascii", "replace")
        tail = body[off + 8 + name_len:end]
        if len(tail) == _RATE_TAIL_LEN:
            out[name] = tail
    return out


def _ramp(tail: bytes, offset: int) -> list[int]:
    return [struct.unpack_from("<Q", tail, offset + i * 8)[0] >> _FRACTION_BITS
            for i in range(_SLD_COUNT)]


def test_the_committed_table_matches_every_documented_figure():
    body, header = _table()
    ok, detail = puc.check_statusinfo_stat_levels(body, header)
    assert ok, detail
    assert "4 carry stat_level_data" in detail
    assert "71 short tails" in detail
    assert "64/64 elements scaled by 2**24" in detail
    assert "4/4 ramps monotonic from zero" in detail
    assert "trailer constant" in detail


def test_the_row_is_pinned_so_it_gates():
    assert (_FIX.name, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating


def test_only_the_four_named_rate_stats_carry_a_ramp():
    """Asserting the NAMES, not just the count. Writing stat_level_data
    into a short tail would corrupt a regular stat, so which records are
    ramp-bearing is the safety property."""
    body, header = _table()
    assert set(_tails(body, header)) == set(puc._STATUSINFO_RATE_STATS)


def test_monotonicity_separates_the_offset_the_overlay_settled():
    """The finding this row is built on.

    68 and 76 are indistinguishable by the two arguments the writer's
    docstring considers: both partition the 212-byte tail into whole
    uint64s, and both leave every element a multiple of 2**24. This test
    asserts BOTH of those non-discriminations explicitly, so the claim
    is not taken on trust -- and then shows that ramp shape does tell
    them apart, 4/4 against 0/4.

    At 76 the window slides one element forward onto the terminator
    slot, so each ramp ends in a drop to zero. That is a property of the
    committed bytes, available without the PAZ overlay the original
    derivation required.
    """
    body, header = _table()
    tails = _tails(body, header)
    scale = 1 << _FRACTION_BITS

    # Neither candidate is excluded by the scale...
    for offset in (_SLD_TAIL_OFFSET, _SLD_TAIL_OFFSET + 8):
        raw = [struct.unpack_from("<Q", t, offset + i * 8)[0]
               for t in tails.values() for i in range(_SLD_COUNT)]
        assert all(v % scale == 0 for v in raw), (
            f"offset {offset} was expected to satisfy the fixed-point "
            f"scale; if it no longer does, this test's premise is stale")

    # ...but ramp shape excludes exactly one of them.
    def monotonic_from_zero(offset: int) -> int:
        n = 0
        for tail in tails.values():
            vals = _ramp(tail, offset)
            if vals[0] == 0 and all(a <= b for a, b
                                    in itertools.pairwise(vals)):
                n += 1
        return n

    assert monotonic_from_zero(_SLD_TAIL_OFFSET) == len(tails)
    assert monotonic_from_zero(_SLD_TAIL_OFFSET + 8) == 0


def test_a_ramp_that_stops_rising_fails_the_row():
    body, header = _table()
    _ks, offs = parse_pabgh_index(header, _TABLE)

    broken = bytearray(body)
    patched = False
    for off in offs.values():
        name_len = struct.unpack_from("<I", body, off + 4)[0]
        name = body[off + 8:off + 8 + name_len].decode("ascii", "replace")
        if name != "DHIT":
            continue
        # Element 8 dropped below element 7 -- still scaled, still in a
        # 212-byte tail, just no longer a rising ramp.
        pos = off + 8 + name_len + _SLD_TAIL_OFFSET + 8 * 8
        struct.pack_into("<Q", broken, pos, 1 << _FRACTION_BITS)
        patched = True
        break
    assert patched, "DHIT not found"

    ok, detail = puc.check_statusinfo_stat_levels(bytes(broken), header)
    assert not ok, detail
    assert "3/4 ramps monotonic" in detail
    assert "one-element-late" in detail


def test_an_unscaled_element_fails_the_row():
    """A changed fixed-point scale would land every preset's write at the
    wrong magnitude rather than failing."""
    body, header = _table()
    _ks, offs = parse_pabgh_index(header, _TABLE)
    spans = sorted(offs.values()) + [len(body)]

    broken = bytearray(body)
    for off in offs.values():
        end = spans[spans.index(off) + 1]
        name_len = struct.unpack_from("<I", body, off + 4)[0]
        if end - (off + 8 + name_len) != _RATE_TAIL_LEN:
            continue
        pos = off + 8 + name_len + _SLD_TAIL_OFFSET + 8
        struct.pack_into("<Q", broken, pos,
                         struct.unpack_from("<Q", body, pos)[0] + 1)
        break

    ok, detail = puc.check_statusinfo_stat_levels(bytes(broken), header)
    assert not ok, detail
    assert "63/64 elements scaled" in detail


def test_the_trailer_is_constant_across_all_four_records():
    """Offset 80 shipped first and ran the array off the end into these
    bytes, which is why they looked like data. Their being identical on
    all four records is what makes that misreading visible."""
    body, header = _table()
    trailers = {t[204:212] for t in _tails(body, header).values()}
    assert len(trailers) == 1, trailers
    # And the terminator slot ahead of it is zero on every record.
    assert all(struct.unpack_from("<Q", t, 196)[0] == 0
               for t in _tails(body, header).values())
