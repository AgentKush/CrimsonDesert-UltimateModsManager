"""buffinfo: the item walk must prove it landed where the layout says.

GitHub #313. ``locate_buff_field`` walks ``buff_data_list`` using the
hardcoded sizes in ``_VARIANT_TAIL_SIZES``, which were derived offline
from one game version's table. Pearl Abyss does change these layouts
between versions -- equipslotinfo's record block moved 66 -> 63 across
1.10 -> 1.15 -- and until now the walk returned an offset without ever
checking it had landed somewhere sensible. A drifted size means writes
go to the wrong bytes, silently, which is the corrupt-the-user's-game
failure mode.

The region carries its own check: ``buff_data_count`` items must fill
``[buff_data_list_offset, min_level_offset)`` with no slack and no
remainder.

The subtlety, and why this file exists rather than a one-line assert:
**a walk that stops early is not the same as a walk that lands wrong.**

* Stopping on a tag that isn't in the size table is a coverage gap. It
  says nothing about the items already placed, each of which was reached
  over known-size predecessors. 63 of the 290 records on the real 1.15
  table stop this way, and **34 of those stop on the LAST item** -- which
  leaves the collected offsets full-length and indistinguishable from a
  clean walk if only the count is checked. Refusing those would drop
  writes that are provably correct.
* Walking every item with a known size and *still* not landing on
  ``min_level_offset`` is a contradiction: the sizes no longer describe
  this table. Nothing derived from them is safe, not even item 0, since
  a wrong ``buff_data_list_offset`` presents identically.

So the walk reports which of the two happened, and only the second
refuses the record.
"""
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from cdumm._vendor import buffinfo_parser as bp
from cdumm.semantic.parser import parse_pabgh_index

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla115"

pytestmark = pytest.mark.skipif(
    not (_FIXTURES / "buffinfo.pabgb.zlib").exists(),
    reason="vanilla115 buffinfo fixture absent")

#: Measured on the committed 1.15 table. Pinned so a change to
#: _VARIANT_TAIL_SIZES has to explain itself: a size that starts
#: contradicting the table shows up here as records moving into
#: `mismatch`, which is exactly the drift this guard exists to catch.
EXPECTED_OUTCOMES = {bp.WALK_TILES: 227, bp.WALK_BLOCKED: 63}

ITEM_PATHS = ("buff_data_list[{n}].absent_flag",
              "buff_data_list[{n}].leading_lookup",
              "buff_data_list[{n}].data.base.tag",
              "buff_data_list[{n}].data.variant.type")


def _load(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / (name + ".zlib")).read_bytes())


def _entries() -> dict[int, bytes]:
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    _keys, offs = parse_pabgh_index(header, "buffinfo")
    ordered = sorted(offs.items(), key=lambda kv: kv[1])
    out = {}
    for i, (key, off) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(body)
        out[key] = body[off:end]
    return out


def _resolvable(entries: dict[int, bytes]) -> dict:
    """Every (key, n, path) the parser will currently place."""
    out = {}
    for key, eb in entries.items():
        try:
            count = bp.parse_entry(eb).buff_data_count
        except Exception:  # noqa: BLE001, S112 -- entry parsing isn't the point
            continue
        for n in range(min(count, 12)):
            for p in ITEM_PATHS:
                got = bp.locate_buff_field(eb, p.format(n=n))
                if got is not None:
                    out[(key, n, p)] = got
    return out


# ----------------------------------------------------- the walk contract

def test_walk_outcomes_on_the_real_table():
    entries = _entries()
    assert len(entries) == 290
    seen: dict[str, int] = {}
    for eb in entries.values():
        _starts, outcome = bp.walk_buff_data_items(eb)
        seen[outcome] = seen.get(outcome, 0) + 1
    assert seen == EXPECTED_OUTCOMES


def test_no_record_currently_contradicts_the_size_table():
    """The guard must cost nothing on a healthy table: a mismatch here
    would mean CDUMM is already writing to wrong bytes somewhere."""
    for key, eb in _entries().items():
        _starts, outcome = bp.walk_buff_data_items(eb)
        assert outcome != bp.WALK_MISMATCH, key


def test_a_tiling_walk_places_every_item():
    entries = _entries()
    for key, eb in entries.items():
        starts, outcome = bp.walk_buff_data_items(eb)
        if outcome != bp.WALK_TILES:
            continue
        assert len(starts) == bp.parse_entry(eb).buff_data_count, key
        assert starts == sorted(starts), key
        assert starts[0] == bp.parse_entry(eb).buff_data_list_offset, key


def test_blocked_walks_still_place_the_items_they_reached():
    """The coverage that must NOT be lost. A record blocked on a later
    item still knows exactly where the earlier ones are."""
    blocked = 0
    for eb in _entries().values():
        starts, outcome = bp.walk_buff_data_items(eb)
        if outcome != bp.WALK_BLOCKED:
            continue
        blocked += 1
        assert starts, "item 0 needs no walking and is always placeable"
        assert bp.locate_buff_field(
            eb, "buff_data_list[0].absent_flag") is not None
    assert blocked == EXPECTED_OUTCOMES[bp.WALK_BLOCKED]


def test_blocked_on_the_last_item_is_not_treated_as_a_mismatch():
    """The trap: 34 records stop on their final item, so the offsets
    collected are full-length. Checking only the count would call these
    contradictions and refuse them."""
    full_length_but_blocked = 0
    for eb in _entries().values():
        starts, outcome = bp.walk_buff_data_items(eb)
        if outcome != bp.WALK_BLOCKED:
            continue
        if len(starts) == bp.parse_entry(eb).buff_data_count:
            full_length_but_blocked += 1
            assert bp.locate_buff_field(
                eb, "buff_data_list[0].absent_flag") is not None
    assert full_length_but_blocked == 34


# --------------------------------------------------- the drift it catches

def test_a_drifted_size_that_still_parses_is_refused(monkeypatch):
    """The case nothing else catches.

    A wrong size usually makes the next header unreadable, so the walk
    stops and the offsets are refused anyway. The dangerous drift is the
    one that stays plausible to the end of the list and simply lands on
    the wrong byte. Perturbing tag 0 by one byte produces exactly that on
    several records: every step has a known size, and the total no longer
    reaches ``min_level_offset``.

    Before this guard the parser handed back offsets for those records.
    It must now refuse them outright -- including item 0, whose position
    is only trustworthy if the model of the record holds.
    """
    entries = _entries()
    before = _resolvable(entries)
    tiled_before = {k for k, eb in entries.items()
                    if bp.walk_buff_data_items(eb)[1] == bp.WALK_TILES}

    sizes = dict(bp._VARIANT_TAIL_SIZES)
    sizes[0] = sizes[0] + 1
    monkeypatch.setattr(bp, "_VARIANT_TAIL_SIZES", sizes)

    outcomes = {k: bp.walk_buff_data_items(eb)[1]
                for k, eb in entries.items()}
    drifted = {k for k, o in outcomes.items() if o == bp.WALK_MISMATCH}
    assert drifted, "the perturbation must actually reach the mismatch case"

    after = _resolvable(entries)
    assert not [k for k in after if k[0] in drifted], (
        "a record whose sizes contradict the table must resolve nothing")

    # Records the perturbation didn't disturb at all -- still tiling -- must
    # keep every offset they had. (Records that merely *block* on the
    # drifted tag are expected to lose the items past it; that is the walk
    # refusing to step over something it can no longer measure.)
    untouched = {k for k in tiled_before
                 if outcomes[k] == bp.WALK_TILES}
    assert len(untouched) > 200, "the blast radius should be small"
    lost = [k for k in before if k[0] in untouched and k not in after]
    assert not lost, "records that still tile must be unaffected"

    # and item 0 specifically, which needs no walking and would otherwise
    # look safe
    for key in drifted:
        assert bp.locate_buff_field(
            entries[key], "buff_data_list[0].absent_flag") is None, key
