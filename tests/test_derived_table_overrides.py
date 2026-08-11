"""The 13 derived table layouts must stay loadable and stay ungated.

Their on-disk order and field widths were derived by
``tools/derive_table_layout.py`` and proven by exact tiling on 100% of each
table's records, then re-verified through CDUMM's own walker before being
written here.

Tiling cannot run in CI -- it needs a game install -- so this pins the parts
that can be checked from the repo alone:

* every table still loads (a malformed override makes the loader refuse the
  table and return None, which would otherwise fail silently),
* every field carries a type descriptor the walker understands, because a
  field with no width shifts every later field's offset,
* ``_verified_fields`` stays EMPTY. That is the load-bearing one: tiling
  proves the record structure and each field's offset, and says nothing
  about what a field MEANS. Gating them keeps derived values out of the
  Game Data grid as facts and out of the writers.

Run ``tools/derive_table_layout.py --game-dir <game>`` on a machine with the
game to re-check the tiling itself.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdumm.semantic.pabgb_types import is_known_type
from cdumm.semantic.parser import get_schema

_OVERRIDES = (Path(__file__).resolve().parents[1] / "schemas"
              / "pabgb_type_overrides.json")

#: Derived on game buildid 24613230, with the record count each was proven
#: against. The counts are here so a table that silently loses records --
#: or an override copied to the wrong table -- shows up as a mismatch.
DERIVED = {
    "AIDialogTypeInfo": 108,
    "BreakableObjectInfo": 15,
    "CategoryGroupInfo": 92,
    "DetectInfo": 37,
    "DialogVoiceInfo": 519,
    "FailMessageInfo": 11,
    "GameAdviceGroupInfo": 8,
    "GamePlayVariableInfo": 56,
    "GimmickEventTableInfo": 1068,
    "JobInfo": 209,
    "LocalStringInfo": 43863,
    "MaterialRelationInfo": 11,
    "VibratePatternInfo": 28,
    "CharacterAppearanceIndexInfo": 8344,
    "MercenaryInfo": 21,
    "SocketInfo": 2,
    "TerrainRegionNaviInfo": 19,
    "AIMemoryInfo": 3,
    "ContentsPhaseInfo": 3,
    "FactionOperationGroupInfo": 9,
    "HouseInfo": 4,
    "ZoneInfo": 6,
}


def _overrides() -> dict:
    return json.loads(_OVERRIDES.read_text(encoding="utf-8-sig"))


def test_every_derived_table_is_present_with_an_order():
    ovr = _overrides()
    for table in DERIVED:
        assert table in ovr, f"{table} lost its override"
        entry = ovr[table]
        order = entry.get("_ordered_fields")
        assert order, f"{table} has no _ordered_fields"
        assert len(order) == len(set(order)), f"{table} repeats a field"


@pytest.mark.parametrize("table", sorted(DERIVED))
def test_derived_table_loads_and_every_field_has_a_known_width(table):
    """A refused override returns None, and a widthless field shifts
    every field after it -- both are silent, so both are pinned."""
    schema = get_schema(table)
    assert schema is not None, (
        f"the loader refused {table}'s override -- check its error log")
    order = _overrides()[table]["_ordered_fields"]
    assert [f.name for f in schema.fields] == order, (
        f"{table} loaded in a different order than _ordered_fields")
    for f in schema.fields:
        desc = getattr(f, "type_descriptor", None)
        assert desc, f"{table}.{f.name} has no type descriptor"
        assert is_known_type(desc), (
            f"{table}.{f.name} descriptor {desc!r} is not a type the walker "
            f"knows, so its width is unusable")


@pytest.mark.parametrize("table", sorted(DERIVED))
def test_derived_tables_stay_gated_as_unverified(table):
    """The important one.

    Exact tiling proves structure and offsets, NOT semantics -- nothing
    here establishes that a field called ``_limitDistance`` is a distance.
    An empty ``_verified_fields`` is what keeps these out of the Game Data
    grid as facts and out of the writers. Filling it in requires
    cross-checking values against real records first.
    """
    entry = _overrides()[table]
    assert entry.get("_verified_fields") == [], (
        f"{table} has gained verified_fields. Derived layouts must stay "
        f"gated until their VALUES are cross-checked -- tiling does not "
        f"establish what a field means.")


@pytest.mark.parametrize("table", sorted(DERIVED))
def test_derived_tables_declare_no_null_skip(table):
    """The derivation put the payload at key_size + 4 + name_len with no
    trailing-NUL skip. If the loader disagrees, every field lands one byte
    late and the whole record mis-reads -- which is exactly the class of
    bug the tiling proof exists to prevent."""
    assert _overrides()[table].get("_no_null_skip") is True, (
        f"{table} must set _no_null_skip; the layout was derived without a "
        f"NUL skip")


def test_the_derivation_provenance_is_recorded():
    """Each entry has to say where it came from and what proves it.

    An override without provenance is indistinguishable from a guess a year
    from now, and this project has the verification harness because
    "someone said so" was wrong on 18 of 24 entries.
    """
    ovr = _overrides()
    for table, n in DERIVED.items():
        note = ovr[table].get("_meta_note", "")
        assert "derive_table_layout" in note, f"{table} note lacks the tool"
        assert str(n) in note, (
            f"{table} note should state the {n} records it was proven "
            f"against")
        assert "_verified_fields is EMPTY" in note, (
            f"{table} note must state why it is gated")
