"""Nested-path editing for iteminfo item prices on CD 1.13.

The 1.13 relocated-layout writer (`_build_change_relocated_layout`) gained a
nested-path branch so item-price mods apply on the current game the same way
the full-schema writer already handled them. `price_list` decodes as
``[{"key": N, "price": {"price": P, ...}}]``, so the sell/buy value is the
path ``price_list[0].price.price``.

These pin the resolution + shape-gate the branch relies on (CI-runnable, no
game fixture). The whole-table byte-exact apply is verified live against the
installed 1.13 iteminfo (key 2200, price 5 -> 12345, 0 collateral records),
matching how test_iteminfo_layout.py splits CI mechanics from the live check.
"""
from __future__ import annotations

from cdumm.engine.iteminfo_writer import _resolve_path_target, shape_matches


def _item():
    # shape exactly as the native parser emits for a decoded 1.13 record
    return {
        "key": 2200,
        "price_list": [
            {"key": 1, "price": {"price": 5, "sym_no": 0, "item_info_wrapper": 1}}
        ],
    }


def test_price_path_resolves_to_the_inner_price_value():
    it = _item()
    target = _resolve_path_target(it, "price_list[0].price.price")
    assert target is not None
    parent, seg = target
    # parent is the nested price dict; seg the final key -> assignable
    assert parent is it["price_list"][0]["price"]
    assert seg == "price"
    assert parent[seg] == 5


def test_price_path_assignment_sets_the_value():
    it = _item()
    parent, seg = _resolve_path_target(it, "price_list[0].price.price")
    parent[seg] = 12345  # exactly what the writer branch does
    assert it["price_list"][0]["price"]["price"] == 12345


def test_price_value_shape_gate():
    # int -> int passes; a wrong-shaped new value is refused before serialize
    assert shape_matches(5, 12345) is True
    assert shape_matches(5, [1, 2]) is False
    assert shape_matches(5, {"x": 1}) is False


def test_price_path_bad_segments_return_none():
    it = _item()
    assert _resolve_path_target(it, "price_list[9].price.price") is None   # index OOR
    assert _resolve_path_target(it, "price_list[0].nope.price") is None    # missing key


def test_iteminfo_price_is_a_synthetic_editable_field():
    # The maker exposes a flat `_price` column whose intent targets the nested
    # path; the writer resolves the path (verified byte-exact on the live game).
    from cdumm.semantic import parser as sem
    from cdumm.engine.format3_builder import is_editable_scalar_field
    sem.init_schemas()
    sch = sem.get_schema("iteminfo")
    by = {f.name: f for f in sch.fields}
    p = by.get("_price")
    assert p is not None, "synthetic _price field missing"
    assert p.struct_fmt == "Q"                       # u64 == PriceFloor.price
    assert p.intent_path == "price_list[0].price.price"
    assert is_editable_scalar_field(p)
    assert "_price" in (sch.verified_fields or frozenset())
    # the real CArray field is left intact and stays gated (not verified)
    assert "_priceList" in by
    assert "_priceList" not in (sch.verified_fields or frozenset())
