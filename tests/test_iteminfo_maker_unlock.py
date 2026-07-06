"""iteminfo is unlocked in the mod maker for its two leading scalar fields.

`_maxStackCount` and `_isBlocked` sit at fixed offsets before the 1.13 layout
drift, decode identically in the generic grid parser and raxjinn's native
parser (verified 3,167/3,167 byte-exact on a live 1.13 install), and apply via
the iteminfo whole-table writer. They are the only iteminfo fields marked
verified, so the maker exposes exactly these two as editable and masks the
other 111 as `(unverified)`. Deep fields (e.g. `_priceList`) must stay gated —
their offsets shift with the equipment-layout drift #252 doesn't decode yet.
"""
from __future__ import annotations

from cdumm.semantic import parser as sem
from cdumm.engine.format3_builder import is_editable_scalar_field

_EXPECTED = frozenset(
    {"_maxStackCount", "_isBlocked", "_cooltime", "_maxEndurance", "_itemTier"})


def _iteminfo_schema():
    sem.init_schemas()
    return sem.get_schema("iteminfo")


def test_iteminfo_verified_fields_match_the_unlocked_set():
    sch = _iteminfo_schema()
    assert sch is not None
    assert sch.verified_fields == _EXPECTED


def test_unlocked_iteminfo_fields_are_editable_scalars():
    sch = _iteminfo_schema()
    by_name = {f.name: f for f in sch.fields}
    for name in _EXPECTED:
        assert name in by_name, f"{name} missing from iteminfo schema"
        assert is_editable_scalar_field(by_name[name]), (
            f"{name} must be a single-scalar field the maker can edit")


def test_deep_iteminfo_fields_stay_gated():
    # A variable-length / deep field whose offset moves with 1.13 drift must
    # never be presented as editable — writing it blind could corrupt bytes.
    sch = _iteminfo_schema()
    for gated in ("_priceList", "_itemName", "_occupiedEquipSlotDataList"):
        assert gated not in (sch.verified_fields or frozenset())
