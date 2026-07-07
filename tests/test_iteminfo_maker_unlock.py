"""iteminfo unlocks its byte-exact-verified scalar fields in the mod maker.

Every unlocked field decodes via raxjinn's native 1.13 parser (#252), and a
Format 3 `set` rewrites only that field's bytes with zero collateral -- proven
on the live install by serializing the edited record and re-parsing (the
whole-table writer is just the concatenation of per-record serializations, so a
byte-exact record edit is a byte-exact table edit). Post-drift fields, which the
generic grid mis-reads on CD 1.13, are shown via the native overlay; leading
fields decode correctly in the generic grid. Deep array fields (`_priceList`,
`_itemName`, ...) whose offsets shift with the layout drift stay gated, and
`_unk_*` fields -- decoded but unnamed -- are never exposed.
"""
from __future__ import annotations

from cdumm.semantic import parser as sem
from cdumm.engine.format3_builder import is_editable_scalar_field

# Modder-facing headline fields that must stay editable (stack size, prices,
# cooldown, durability, tier, level req, dye/sell flags, charges, respawn, type).
_MUST_INCLUDE = frozenset({
    "_maxStackCount", "_isBlocked", "_cooltime", "_maxEndurance", "_itemTier",
    "_price", "_equipableLevel", "_isDyeable", "_isBlockedStoreSell",
    "_respawnTimeSeconds", "_maxChargedUseableCount", "_itemType",
})
_EXPECTED_COUNT = 63


def _iteminfo_schema():
    sem.init_schemas()
    return sem.get_schema("iteminfo")


def test_iteminfo_headline_fields_are_verified():
    sch = _iteminfo_schema()
    assert sch is not None
    assert _MUST_INCLUDE <= sch.verified_fields


def test_iteminfo_verified_count_matches_the_unlocked_batch():
    sch = _iteminfo_schema()
    assert len(sch.verified_fields) == _EXPECTED_COUNT


def test_every_verified_iteminfo_field_is_an_editable_scalar():
    sch = _iteminfo_schema()
    by = {f.name: f for f in sch.fields}
    for name in sch.verified_fields:
        assert name in by, f"{name} verified but absent from iteminfo schema"
        assert is_editable_scalar_field(by[name]), (
            f"{name} is verified but not an editable scalar the maker can write")


def test_unnamed_and_deep_fields_stay_gated():
    sch = _iteminfo_schema()
    v = sch.verified_fields or frozenset()
    # reverse-engineered placeholders (unknown purpose) must never be exposed
    assert not any(n.startswith("_unk_") for n in v)
    # variable-length / deep fields whose offsets shift with 1.13 drift
    for gated in ("_priceList", "_itemName", "_occupiedEquipSlotDataList"):
        assert gated not in v
