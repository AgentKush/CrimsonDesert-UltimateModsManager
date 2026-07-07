"""wantedinfo exposes exactly its two byte-exact-verified scalar fields.

wantedinfo records are a uniform 16 bytes (header 7 + u64 price + u8 flag).
`_increasePrice` (u64 at record byte 7) and `_isBlocked` (u8 at record byte 15,
the record's last byte) each rewrite only their own bytes on a Format 3 `set`,
proven on every sampled record via both the display walker and the real writer.
`_useTargetPrice` would fall at record byte 16 -- past the record end -- so it
never exists in shipped data and must stay gated (the writer no-ops it);
`_stringKey` is variable-length with no stable offset.
"""
from __future__ import annotations

from cdumm.semantic import parser as sem
from cdumm.engine.format3_builder import is_editable_scalar_field

_EXPECTED = frozenset({"_increasePrice", "_isBlocked"})


def _wantedinfo_schema():
    sem.init_schemas()
    return sem.get_schema("wantedinfo")


def test_wantedinfo_verified_fields_match_the_unlocked_set():
    sch = _wantedinfo_schema()
    assert sch is not None
    assert sch.verified_fields == _EXPECTED


def test_unlocked_wantedinfo_fields_are_editable_scalars():
    sch = _wantedinfo_schema()
    by_name = {f.name: f for f in sch.fields}
    for name in _EXPECTED:
        assert name in by_name, f"{name} missing from wantedinfo schema"
        assert is_editable_scalar_field(by_name[name]), (
            f"{name} must be a single-scalar field the maker can edit")


def test_out_of_bounds_and_variable_fields_stay_gated():
    sch = _wantedinfo_schema()
    gated = sch.verified_fields or frozenset()
    # _useTargetPrice's offset lands past the 16-byte record end (no data);
    # _stringKey is variable-length. Neither may be presented as editable.
    assert "_useTargetPrice" not in gated
    assert "_stringKey" not in gated
