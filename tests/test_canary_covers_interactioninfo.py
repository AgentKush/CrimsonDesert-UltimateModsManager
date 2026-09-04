"""interactioninfo: a positional scan always finds *something*.

"Fast Pickup - Increase Range" sets interaction_pivot_list[0].raw_a/raw_b
on five records. `_interactionPivotList` is field #26 of InteractionInfo
with type `None` in the schema -- no descriptor -- so the generic walker
reaches zero records and the pair has to be found positionally: scan for
a spot where the preceding 16 bytes are zero and the next two f32 are a
sane range.

That is a heuristic over bytes. A plausible answer is always available,
which makes "the row is green" nearly content-free on its own. These
tests pin the two properties that give it content, and demonstrate the
case neither structural gate can see.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine.interactioninfo_writer import (
    _record_bounds,
    locate_pivot_pair,
)

_FIX = Path(__file__).parent / "fixtures" / "vanilla115"
_TABLE = "interactioninfo"

pytestmark = pytest.mark.skipif(
    not (_FIX / f"{_TABLE}.pabgb.zlib").exists(),
    reason=f"{_TABLE} fixture not committed")


def _table() -> tuple[bytes, bytes]:
    return (zlib.decompress((_FIX / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((_FIX / f"{_TABLE}.pabgh.zlib").read_bytes()))


def _anchor_offset(body: bytes, header: bytes, want: str) -> int:
    for lo, hi in _record_bounds(header, body).values():
        name_len = struct.unpack_from("<I", body, lo + 4)[0]
        if body[lo + 8:lo + 8 + name_len].decode("ascii", "replace") == want:
            pos = locate_pivot_pair(body, lo, hi)
            assert pos is not None, f"{want} does not resolve in vanilla"
            return pos
    pytest.skip(f"{want} not present in this table")
    raise AssertionError  # unreachable


def test_the_committed_table_matches_every_documented_figure():
    body, header = _table()
    ok, detail = puc.check_interactioninfo_pivot_pair(body, header)
    assert ok, detail
    assert "295/393 records resolve uniquely" in detail
    assert "98 refused" in detail
    assert "590/590 located values quantised" in detail
    assert "5/5 anchors at their known ranges" in detail


def test_the_row_is_pinned_so_it_gates():
    assert (_FIX.name, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating


def test_the_anchors_catch_a_drift_both_structural_gates_absorb():
    """The reason the anchors exist, demonstrated.

    Rewrite the Gimmick_PickUp pair to a DIFFERENT but entirely
    plausible range -- 7.5/7.5, inside [0.01, 100.0] and an exact
    multiple of 0.05. The 16-zero frame is untouched, so the record
    still resolves uniquely; the value is still quantised, so that gate
    is still satisfied. Both structural gates report identical numbers
    to vanilla.

    Only the anchor notices, and that is precisely the shape a layout
    drift takes here: the scan keeps finding *a* pair of floats, just
    not the right one, and a Fast Pickup write lands on an unrelated
    field.
    """
    body, header = _table()
    pos = _anchor_offset(body, header, "Gimmick_PickUp")

    broken = bytearray(body)
    struct.pack_into("<ff", broken, pos, 7.5, 7.5)
    broken = bytes(broken)

    ok, detail = puc.check_interactioninfo_pivot_pair(broken, header)
    assert not ok, detail
    # The structural gates are untouched -- that is the whole point.
    assert "295/393 records resolve uniquely" in detail
    assert "590/590 located values quantised" in detail
    assert "4/5 anchors at their known ranges" in detail
    assert "unrelated field" in detail


def test_a_non_quantised_value_fails_the_row():
    """Quantisation is a property of the data, not of the locator's
    [0.01, 100.0] filter, so losing it means the scan has drifted."""
    body, header = _table()
    pos = _anchor_offset(body, header, "Gimmick_PickUp")

    broken = bytearray(body)
    struct.pack_into("<ff", broken, pos, 2.53, 2.5)   # sane, not a 0.05 step

    ok, detail = puc.check_interactioninfo_pivot_pair(bytes(broken), header)
    assert not ok, detail
    assert "589/590 located values quantised" in detail


def test_a_destroyed_frame_is_refused_not_relocated():
    """Breaking the 16-zero frame must cost that record its match rather
    than pushing the scan onto some other pair of floats."""
    body, header = _table()
    pos = _anchor_offset(body, header, "Gimmick_PickUp")

    broken = bytearray(body)
    broken[pos - 16:pos - 8] = b"\x11" * 8

    ok, detail = puc.check_interactioninfo_pivot_pair(bytes(broken), header)
    assert not ok, detail
    assert "294/393 records resolve uniquely" in detail
    assert "99 refused" in detail


def test_refusals_are_expected_and_not_gated():
    """98 of 393 refusing is correct, not a defect: the 16-zero frame
    only holds where upper height and goto offset are both zero. The row
    must not treat that as failure, or it would push someone to relax
    the locator until it guesses."""
    body, header = _table()
    ok, _detail = puc.check_interactioninfo_pivot_pair(body, header)
    assert ok

    bounds = _record_bounds(header, body)
    refused = sum(1 for lo, hi in bounds.values()
                  if locate_pivot_pair(body, lo, hi) is None)
    assert refused == 98
    assert refused / len(bounds) > 0.2, (
        "a quarter of the table refusing is the documented state")


def test_the_quantisation_evidence_is_a_property_of_the_data():
    """The locator tests only that a value is in [0.01, 100.0]. It never
    mentions 0.05. So all 590 located values landing exactly on a 0.05
    step is evidence about the table, not a tautology about the filter.

    Compared f32-aware: 0.05 is not exactly representable, so the test
    is whether float32(n * 0.05) reproduces the stored bits.
    """
    body, header = _table()

    def f32(x: float) -> float:
        return struct.unpack("<f", struct.pack("<f", x))[0]

    total = stepped = 0
    for lo, hi in _record_bounds(header, body).values():
        pos = locate_pivot_pair(body, lo, hi)
        if pos is None:
            continue
        for v in struct.unpack_from("<ff", body, pos):
            total += 1
            stepped += f32(round(v / 0.05) * 0.05) == v

    assert total == 590
    assert stepped == total
