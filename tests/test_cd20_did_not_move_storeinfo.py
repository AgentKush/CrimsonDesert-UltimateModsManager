"""CD 2.0 rewrote iteminfo and left storeinfo alone. Both are findings.

The 26 Aug build (Steam buildid 24934353) is a major version bump that
broke iteminfo outright -- every pre-2.0 layout carries that table 100%
opaque (#377). It would be easy to assume a bump that large moved
everything. It did not: #393 committed the CD 2.0 storeinfo table, and
the CD 1.16.1 layout reads it exactly, with nothing left over.

That negative result is worth an assertion rather than a shrug. If a
later patch *does* move storeinfo, the thing that tells us is this test
going red on a table it used to read; and if someone ever proposes a
"CD 2.0" store layout, these numbers say what it would have to beat.

Also pins the npcinfo dye-list tiling rate, which nothing else does.
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

from cdumm.engine.storeinfo_native_parser import (
    LAYOUTS,
    StoreListNotFound,
    _entry_payload,
    _score_layout,
    locate_stock_list,
)
from cdumm.semantic.parser import parse_pabgh_index

_FIX = Path(__file__).resolve().parent / "fixtures" / "vanilla_b24934353"

pytestmark = pytest.mark.skipif(
    not (_FIX / "storeinfo.pabgb.zlib").exists(),
    reason="CD 2.0 storeinfo fixture not present")

#: Measured on the committed CD 2.0 table.
CD20_ENTRIES = 436
CD20_LOCATED = 397
CD20_EMPTY = 39
CD20_RECORDS = 6_376


@pytest.fixture(scope="module")
def table():
    body = zlib.decompress((_FIX / "storeinfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIX / "storeinfo.pabgh.zlib").read_bytes())
    _ks, offs = parse_pabgh_index(header, "storeinfo")
    return body, offs


def _split(body, offs, layout):
    starts = sorted(offs.values())
    spans = starts + [len(body)]
    located = empty = not_found = ambiguous = 0
    for key, off in offs.items():
        end = spans[spans.index(off) + 1]
        try:
            locate_stock_list(body, _entry_payload(body, off), end, key, layout)
        except StoreListNotFound as exc:
            if exc.provably_empty:
                empty += 1
            elif exc.ambiguous:
                ambiguous += 1
            else:
                not_found += 1
            continue
        except Exception:  # noqa: BLE001 - any parse failure is not-found
            not_found += 1
            continue
        located += 1
    return located, empty, not_found, ambiguous


def test_cd_1_16_1_reads_the_cd20_table_exactly(table):
    """The finding. Not "mostly" -- 436 of 436, nothing unexplained."""
    body, offs = table
    assert len(offs) == CD20_ENTRIES
    lay = next(x for x in LAYOUTS if x.label == "CD 1.16.1")
    located, empty, not_found, ambiguous = _split(body, offs, lay)
    assert (located, empty, not_found, ambiguous) == (
        CD20_LOCATED, CD20_EMPTY, 0, 0)
    assert located + empty == len(offs)
    assert _score_layout(body, sorted(offs.values()), lay) == (
        CD20_LOCATED, CD20_RECORDS)


def test_only_cd_1_16_1_leaves_nothing_unexplained(table):
    """What separates the winner, stated as the property that does it.

    NOT the located count. CD 1.13 locates 319 of 397 on this table --
    80% of the winner, close enough that a "most entries decode" test
    would call it a plausible fit. It is not one: it leaves 78 entries
    it cannot account for, and an entry that neither decodes nor is
    provably empty is a store whose stock a mod would silently fail to
    edit.

    Completeness is the discriminator. located + provably_empty must
    equal the entry count, with nothing in the not-found or ambiguous
    buckets, and CD 1.16.1 is the only shipped layout that manages it.
    """
    body, offs = table
    accounted = {}
    for lay in LAYOUTS:
        located, empty, not_found, ambiguous = _split(body, offs, lay)
        accounted[lay.label] = (located + empty == len(offs)
                                and not_found == 0 and ambiguous == 0)
    assert accounted["CD 1.16.1"] is True
    assert [k for k, v in accounted.items() if v] == ["CD 1.16.1"], accounted

    # And the near-miss is a near-miss for a reason worth keeping visible.
    located, empty, not_found, ambiguous = _split(
        body, offs, next(x for x in LAYOUTS if x.label == "CD 1.13"))
    assert (located, not_found) == (319, 78)


def test_the_canary_row_agrees(table):
    """Driven through the canary itself, not a reimplementation."""
    body = zlib.decompress((_FIX / "storeinfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIX / "storeinfo.pabgh.zlib").read_bytes())
    ok, detail = puc.check_storeinfo(body, header)
    assert ok, detail
    assert "CD 1.16.1" in detail
    assert f"{CD20_LOCATED} located" in detail
    assert "0 not-found" in detail


@pytest.mark.skipif(not (_FIX / "npcinfo.pabgb.zlib").exists(),
                    reason="CD 2.0 npcinfo fixture not present")
def test_npcinfo_dye_list_tiling_rate_is_pinned():
    """A table-wide rate that otherwise lives only in a commit message.

    #393's message reports "452 of 542 NPCs tile"; driving the production
    path over the committed bytes gives 462, and no upstream test pins
    either. Whichever figure was meant, a number nothing asserts drifts.
    This is the one the bytes support.

    The refusals are not failures: most NPCs are not Dyers and do not
    carry the four-blob anchor, and refusing is the designed outcome. The
    property that matters is that every entry which DOES tile reproduces
    its own bytes exactly.
    """
    body = zlib.decompress((_FIX / "npcinfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((_FIX / "npcinfo.pabgh.zlib").read_bytes())
    ok, detail = puc.check_npcinfo_dye_lists(body, header)
    assert ok, detail
    assert "462/542 entries tile" in detail
    assert "80 refused" in detail
    assert "0 mis-round-tripped" in detail
    assert "11 carry dye lists" in detail
