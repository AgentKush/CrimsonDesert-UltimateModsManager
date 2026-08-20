"""The 17 Aug patch broke iteminfo, and the canary said iteminfo was fine.

Steam buildid 24773079 added a trailing u32 to each ``prefab_data_list``
element (#369). Under the ``cd116`` layout every item with a non-empty
prefab list stopped decoding and fell back to the opaque byte-carry
path: 5,548 of 6,573 items, 84%. An opaque record accepts only
``is_blocked`` and ``max_stack_count``, so a mod editing prices imported
cleanly, validated cleanly, and then silently applied nothing.

``post_update_check.py`` had an iteminfo row throughout. It reported
"median 109/109 fields, 88% of 6,573 records complete" -- healthy, and
byte-identical to what it reports on the *unbroken* CD 1.16 table. It
scores the ``select_order`` schema walk, which this patch did not touch;
the editor's native parser, which it did, had no row at all.

Two readers over one table fail independently, so one row cannot cover
both. These pin that: the ordered walk is blind to this break by
measurement, not by assertion, and the new native row catches it.
"""
from __future__ import annotations

import sys
import zlib
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

puc = pytest.importorskip("post_update_check")

import cdumm.engine.iteminfo_native_parser as P
from cdumm.semantic.parser import parse_pabgh_index

_FIX = Path(__file__).resolve().parent / "fixtures" / "vanilla_b24773079"

pytestmark = pytest.mark.skipif(
    not (_FIX / "iteminfo.pabgb.zlib").exists(),
    reason="vanilla_b24773079 iteminfo fixture not present")

#: Measured under each shipped layout on the committed b24773079 table.
BROKEN_OPAQUE = 5_548
TOTAL_ITEMS = 6_573


@pytest.fixture(scope="module")
def table():
    body = zlib.decompress((_FIX / "iteminfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIX / "iteminfo.pabgh.zlib").read_bytes())
    return body, header


def _opaque_under(body, header, label):
    _ks, offs = parse_pabgh_index(header, "iteminfo")
    fields = next(f for lab, f in P._ITEM_LAYOUTS if lab == label)
    items = P.parse_iteminfo_from_bytes(body, sorted(offs.values()),
                                        fields=fields)
    return sum(1 for it in items if it.get("_opaque_record")), len(items)


def test_the_stale_layout_really_did_lose_84_percent_of_the_table(table):
    """The break, reproduced. Not quoted from the issue -- measured."""
    body, header = table
    opaque, total = _opaque_under(body, header, "cd116")
    assert (opaque, total) == (BROKEN_OPAQUE, TOTAL_ITEMS)
    assert opaque / total > 0.8


def test_the_ordered_walk_cannot_see_it(table):
    """Why a second row was needed.

    The ordered check passes on the broken table, and passes with the
    same numbers it reports on the healthy CD 1.16 one. It is not that
    it was mis-pinned; it measures a reader this patch did not touch.
    """
    body, header = table
    ok, detail = puc.check_ordered_table(
        "ItemInfo", body, header,
        puc._FIXTURE_ORDER_BASELINE[("vanilla_b24773079", "ItemInfo")])
    assert ok, "the ordered walk is green on the broken build -- that is the point"
    assert "109/109" in detail

    prev = zlib.decompress(
        (_FIX.parent / "vanilla116" / "iteminfo.pabgb.zlib").read_bytes())
    prev_h = zlib.decompress(
        (_FIX.parent / "vanilla116" / "iteminfo.pabgh.zlib").read_bytes())
    _ok, prev_detail = puc.check_ordered_table(
        "ItemInfo", prev, prev_h,
        puc._FIXTURE_ORDER_BASELINE[("vanilla116", "ItemInfo")])
    assert detail.split("fields")[0] == prev_detail.split("fields")[0], (
        "the ordered walk reports the same depth on the broken build as on "
        "the healthy one; if that ever stops being true it has gained some "
        "sensitivity to this and this test should be revisited")


def test_the_native_check_would_have_caught_it(monkeypatch):
    """The row that closes the gap, driven through the canary's own check.

    Pinning the stale layout is how the pre-#369 state is reproduced
    without reverting code: detection now picks cd116b, so the only way
    to ask "what did the canary see the day the patch landed" is to take
    cd116b away from it.
    """
    body = zlib.decompress((_FIX / "iteminfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIX / "iteminfo.pabgh.zlib").read_bytes())

    ok, detail = puc.check_iteminfo_native(body, header)
    assert ok, detail
    assert "0 opaque" in detail

    stale = tuple(e for e in P._ITEM_LAYOUTS if e[0] != "cd116b")
    monkeypatch.setattr(P, "_ITEM_LAYOUTS", stale)
    ok, detail = puc.check_iteminfo_native(body, header)
    assert not ok, (
        "without cd116b the canary must fail on this table -- otherwise the "
        "new row would have stayed green through #369 too")
    assert "84.4%" in detail
    assert "no-ops" in detail, "the message has to say what the user loses"


def test_a_byte_exact_round_trip_is_not_enough_on_its_own(monkeypatch):
    """Why the check cannot just assert round-trip.

    The opaque fallback carries undecodable records through verbatim, so
    the whole-table round-trip stays byte-exact *while* the table is
    unusable. That is exactly what made this break silent.
    """
    body = zlib.decompress((_FIX / "iteminfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIX / "iteminfo.pabgh.zlib").read_bytes())
    stale = tuple(e for e in P._ITEM_LAYOUTS if e[0] != "cd116b")
    monkeypatch.setattr(P, "_ITEM_LAYOUTS", stale)

    _ok, detail = puc.check_iteminfo_native(body, header)
    assert "round-trip byte-exact" in detail, (
        "84% of the table opaque and the round-trip still passes -- if this "
        "ever fails instead, round-trip has become a sufficient signal and "
        "the opaque ceiling could be relaxed")
