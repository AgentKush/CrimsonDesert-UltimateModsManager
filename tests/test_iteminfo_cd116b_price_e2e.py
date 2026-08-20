"""End-to-end regression for donr484's "Cheap Gold Bars": a nested
``price_list[0].price.price`` intent on an item whose PrefabData has
at least one element (so it was carried opaque under cd116, and only
the leading-scalar byte-patch fields -- not price_list -- were
writable) now applies via the real Format 3 path.

Pinned on the committed ``vanilla_b24773079`` fixture rather than the
mod's own JSON, so this runs in CI without redistributing donr484's
file (CONTRIBUTING.md: real third-party mods aren't committed).
"""
from __future__ import annotations

import pytest

from cdumm.engine.format3_handler import Format3Intent
from cdumm.engine.iteminfo_native_parser import (
    detect_iteminfo_layout,
    parse_iteminfo_from_bytes,
)
from cdumm.engine.iteminfo_writer import build_iteminfo_intent_change
from cdumm.semantic.parser import parse_pabgh_index
from tests.fixture_loaders import has_vanilla_b24773079, load_vanilla_b24773079

pytestmark = pytest.mark.skipif(
    not has_vanilla_b24773079("iteminfo.pabgb"),
    reason="vanilla_b24773079 iteminfo fixture not present")


def test_gold_bar_price_applies_end_to_end():
    body = load_vanilla_b24773079("iteminfo.pabgb")
    header = load_vanilla_b24773079("iteminfo.pabgh")
    _, offsets = parse_pabgh_index(header, "iteminfo")
    starts = sorted(offsets.values())
    fields = detect_iteminfo_layout(body, starts)
    assert fields is not None

    items = parse_iteminfo_from_bytes(body, record_offsets=starts, fields=fields)
    gold_bar = next(it for it in items if it["key"] == 53)
    assert gold_bar["string_key"] == "GoldBar"
    # Confirms the fixture actually exercises the bug: at least one
    # PrefabData element, which is exactly what made this item opaque
    # under cd116 (and every earlier layout) on this table.
    assert len(gold_bar["prefab_data_list"]) >= 1
    old_price = gold_bar["price_list"][0]["price"]["price"]
    assert old_price != 1

    intent = Format3Intent(
        entry="", key=53, field="price_list[0].price.price", op="set", new=1)
    change = build_iteminfo_intent_change(body, [intent], vanilla_header=header)
    assert change is not None, (
        "the intent was dropped as an unwritable/opaque field -- the "
        "cd116b PrefabData fix regressed")

    new_bytes = bytes.fromhex(change["patched"])
    new_items = parse_iteminfo_from_bytes(
        new_bytes, record_offsets=starts, fields=fields)
    new_gold_bar = next(it for it in new_items if it["key"] == 53)
    assert new_gold_bar["price_list"][0]["price"]["price"] == 1
    # Everything else about the record, prefab_data_list included,
    # survives untouched.
    assert new_gold_bar["prefab_data_list"] == gold_bar["prefab_data_list"]
