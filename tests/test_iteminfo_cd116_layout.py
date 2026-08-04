"""CD 1.16 iteminfo layout, pinned on the committed 1.16 table.

Every pre-1.16 layout round-trips 0 of 6,581 records, so field-level item
mods were refused outright on 1.16 until the cd116 layout landed.

The important thing these tests do that a round-trip cannot: check the
decoded VALUES. The cd116 tail inserts a 10-byte opaque run, and putting
it after ``max_endurance`` instead of before ``respawn_time_seconds``
consumes the same 12 bytes either way. Both placements re-serialize
byte-identically, so the round trip scores the same and cannot tell them
apart -- while one of them reads two mod-facing fields out of the wrong
bytes.

That shipped in review. Under the wrong placement, ``max_endurance``
agreed with 1.13 on 0 of 6,494 shared keys and decoded as
``{0: 6463, 256: 31}``; the repo's own canonical Format 3 example (set
max_endurance to 65535 on item 1002862) wrote its two bytes 10 bytes
early into the undecoded block, left durability untouched, and reported
success. Hence: assert the values, not just the byte count.
"""
from __future__ import annotations

import collections

import pytest

import cdumm.engine.iteminfo_native_parser as P
from cdumm.semantic.parser import parse_pabgh_index
from tests.fixture_loaders import (
    has_vanilla116,
    load_vanilla113,
    load_vanilla116,
)

pytestmark = pytest.mark.skipif(
    not has_vanilla116("iteminfo.pabgb"),
    reason="vanilla116 iteminfo fixture not present")


def _decode(body: bytes, header: bytes, fields):
    """key -> record, skipping records this layout cannot place."""
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    order = sorted(offs.items(), key=lambda kv: kv[1])
    starts = [o for _k, o in order]
    out = {}
    for i, (key, off) in enumerate(order):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        try:
            r = P._Reader(body, off, rec_end=end)
            rec = P._read_item(r, fields=fields)
        except Exception:  # noqa: BLE001, S112 -- a record this layout
            continue       # cannot place is the thing being counted
        if rec:
            out[key] = rec
    return out


@pytest.fixture(scope="module")
def t116():
    return (load_vanilla116("iteminfo.pabgb"),
            load_vanilla116("iteminfo.pabgh"))


@pytest.fixture(scope="module")
def t113():
    return (load_vanilla113("iteminfo.pabgb"),
            load_vanilla113("iteminfo.pabgh"))


def test_cd116_is_selected_for_the_116_table(t116):
    body, header = t116
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    chosen = P.detect_iteminfo_layout(body, sorted(offs.values()))
    assert chosen is not None, "1.16 must resolve to a layout"
    names = [s[0] for s in chosen]
    assert "pre_respawn_116" in names, "the cd116 tail must be selected"
    assert "inventory_info" not in names, "1.16 dropped inventory_info"


def test_the_116_table_round_trips(t116):
    """6,567 of 6,581 records re-serialize byte-exact; the remaining 14
    fall back to the opaque path and are carried verbatim, so the file
    is reproduced either way."""
    body, header = t116
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    order = sorted(offs.items(), key=lambda kv: kv[1])
    starts = [o for _k, o in order]
    exact = 0
    for i, (_key, off) in enumerate(order):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        try:
            r = P._Reader(body, off, rec_end=end)
            rec = P._read_item(r, fields=P._ITEM_FIELDS_CD116)
            w = P._Writer()
            P._write_item(w, rec, fields=P._ITEM_FIELDS_CD116)
        except Exception:  # noqa: BLE001, S112 -- see above
            continue
        if bytes(w.buf) == body[off:end]:
            exact += 1
    assert exact >= 6567, f"only {exact} records round-trip byte-exact"


def test_max_endurance_agrees_with_1_13_on_every_shared_key(t116, t113):
    """The test the round trip cannot be: placement is only visible in
    the decoded values.

    Wrong placement gives 0/6494 agreement and a {0, 256} value set.
    """
    d116 = _decode(*t116, P._ITEM_FIELDS_CD116)
    d113 = _decode(*t113, P._ITEM_FIELDS_CD113_ENCHANT)
    shared = [k for k in d113 if k in d116]
    assert len(shared) > 6000, f"only {len(shared)} shared keys"

    agree = sum(1 for k in shared
                if d113[k].get("max_endurance") == d116[k].get("max_endurance"))
    assert agree == len(shared), (
        f"max_endurance agrees with 1.13 on only {agree}/{len(shared)} keys -- "
        f"the cd116 opaque run is on the wrong side of respawn_time_seconds")


def test_max_endurance_carries_the_unbreakable_sentinel(t116):
    """0xFFFF is the documented 'unbreakable' value (see the module
    header). Reading the wrong bytes loses it entirely, which is the
    single clearest signal that the placement is wrong."""
    d116 = _decode(*t116, P._ITEM_FIELDS_CD116)
    dist = collections.Counter(
        r.get("max_endurance") for r in d116.values())
    assert dist.get(65535, 0) > 6000, (
        f"expected the 0xFFFF unbreakable sentinel to dominate, got "
        f"{dict(sorted(dist.items(), key=lambda kv: -kv[1])[:5])}")
    assert dist.get(256, 0) == 0, (
        "256 is the signature of reading max_endurance from the "
        "undecoded 1.16 block")


def test_respawn_time_seconds_stays_in_its_1_13_value_domain(t116):
    """Vanilla holds 0, -1 and 604800 (7 days). Garbage values like
    1099511627777 mean the field is being read across a boundary."""
    d116 = _decode(*t116, P._ITEM_FIELDS_CD116)
    vals = {r.get("respawn_time_seconds") for r in d116.values()}
    assert vals <= {0, -1, 604800}, (
        f"respawn_time_seconds left its known domain: "
        f"{sorted(v for v in vals if v not in (0, -1, 604800))[:5]}")


def test_cd116_does_not_win_on_the_1_13_table(t113):
    """The regression that would corrupt existing installs: a new layout
    scoring high enough on an older table to be chosen for it."""
    body, header = t113
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    chosen = P.detect_iteminfo_layout(body, sorted(offs.values()))
    assert chosen is not None
    names = [s[0] for s in chosen]
    assert "pre_respawn_116" not in names, "cd116 must not win on 1.13"
    assert "inventory_info" in names, "1.13 still carries inventory_info"
