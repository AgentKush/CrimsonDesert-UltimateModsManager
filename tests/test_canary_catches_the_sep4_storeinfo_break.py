"""The 4 Sep 2026 break, checked against the canary that predates it.

Every other "prove it fires" test in this branch synthesises a break.
This one does not: upstream's dedeedd records a real game update that
grew the storeinfo stock record by 8 bytes between `sub_data` and the
`effect_list` count, and its commit message reports the consequence --
16 of 17 stores in donr484's "Shop Smart. Shop H-Mart" refused one at a
time, each naming the mod, with nothing in the log saying the game had
changed.

Two things about that incident are worth pinning here, because both are
claims this branch made before it happened.

1. The const tripwire at record offset 42 did not fire. Upstream calls
   it "the third time in a row a shape change has landed behind it".
   A tripwire only covers the bytes ahead of it.

2. Layout detection is comparative, so it did not degrade cleanly: the
   best pre-update candidate read 17 of 436 entries and left 380
   neither decodable nor provably empty. That is exactly the quantity
   `test_cd20_did_not_move_storeinfo` argues is the real discriminator
   -- completeness, not the located count.
"""
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine import storeinfo_native_parser as snp
from cdumm.semantic.parser import parse_pabgh_index

_FIX = Path(__file__).parent / "fixtures" / "vanilla_b25116796"
_TABLE = "storeinfo"

pytestmark = pytest.mark.skipif(
    not (_FIX / f"{_TABLE}.pabgb.zlib").exists(),
    reason="the 4 Sep build fixture is not committed")


def _table() -> tuple[bytes, bytes]:
    return (zlib.decompress((_FIX / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((_FIX / f"{_TABLE}.pabgh.zlib").read_bytes()))


@pytest.fixture
def without_the_new_layout(monkeypatch):
    """The reader as CDUMM shipped it when the 4 Sep patch landed."""
    kept = tuple(l for l in snp.LAYOUTS if "25116796" not in l.label)
    assert len(kept) == len(snp.LAYOUTS) - 1, (
        "expected exactly one b25116796 layout to remove")
    monkeypatch.setattr(snp, "LAYOUTS", kept)
    return kept


def test_the_new_build_reads_completely_now():
    body, header = _table()
    ok, detail = puc.check_storeinfo(body, header)
    assert ok, detail
    assert "CD b25116796" in detail
    assert "436/436" in detail
    assert "0 not-found" in detail
    assert "0 ambiguous" in detail


def test_the_new_build_is_pinned_so_it_gates():
    assert (_FIX.name, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating, "a verified capture must gate, not merely report"


def test_the_canary_goes_red_on_the_real_break(without_the_new_layout):
    """Driven with the reader that shipped on 4 Sep, the row fails --
    and fails as a reported row, not as a traceback."""
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1
    _label, ok, detail, _gating = row[0]
    assert not ok, "the canary was green on a table it cannot read"
    assert "StoreinfoParseError" in detail or "no known storeinfo layout" in detail


def test_the_pre_update_reader_leaves_most_entries_unaccounted():
    """The number that makes 'completeness, not located count' the right
    discriminator: the winning candidate decoded 17 of 436."""
    body, header = _table()
    starts = sorted(parse_pabgh_index(header, "storeinfo")[1].values())

    kept = tuple(l for l in snp.LAYOUTS if "25116796" not in l.label)
    best_located = 0
    for layout in kept:
        located = 0
        for start in starts:
            try:
                snp.locate_stock_list(body, start, layout)
            except Exception:                       # noqa: BLE001, S110
                pass                # a refusal is the expected outcome here
            else:
                located += 1
        best_located = max(best_located, located)

    assert best_located < len(starts) // 4, (
        f"the best pre-update layout located {best_located} of "
        f"{len(starts)}; the incident report says 17")


def test_a_tripwire_only_covers_the_bytes_ahead_of_it():
    """Why the const at record offset 42 missed this, stated as a
    property rather than as history: the eight new bytes landed after
    it, so every layout still reads 1 there and the tripwire is silent
    on a table none of them can decode."""
    body, header = _table()
    kept = tuple(l for l in snp.LAYOUTS if "25116796" not in l.label)
    cd116 = next(l for l in kept if l.label == "CD 1.16")

    starts = sorted(parse_pabgh_index(header, "storeinfo")[1].values())
    # The tripwire is intact on the very table it fails to describe.
    intact = sum(1 for s in starts[:50]
                 if body[s + cd116.const_off:s + cd116.const_off + 1] == b"\x01")
    assert intact > 0, (
        "expected the const tripwire to still read 1 on the changed "
        "table -- that is what made this break silent")
