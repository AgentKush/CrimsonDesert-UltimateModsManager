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
    "MaterialMatchInfo": 116,
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
    # ── added 2026-08-12 ──────────────────────────────────────────────────
    # The KEY is the base schema's key, and its lowercase must equal the
    # table's FILE STEM, because _load_schemas iterates the base file and
    # the app asks get_schema() for the stem that
    # identify_table_from_path returns. So the casing here is not sloppy
    # and must not be "tidied": CraftToolInfo reuses the base schema's
    # existing key, while the five lowercase ones are new base entries
    # keyed by a stem that drops the `Info` the class name carries
    # (factiongroup.pabgb <-> FactionGroupInfo). Rename any of them to the
    # class name and CDUMM silently stops finding that table.
    "CraftToolInfo": 19,
    "CraftToolGroupInfo": 12,
    "factiongroup": 7,
    "uisocialaction": 2,
    "factionreblockadinginfo": 108,
    "globalgameeventgroup": 12,
    # ── added 2026-08-12: variable-length list elements ──────────────────
    # Each of these has a CArray whose ELEMENT has no constant width, so it
    # is CArray<Substruct>, with the members taken from the reader's own
    # loop body. All four are FULLY STATIC -- no width came from a data
    # search -- which is what makes a 2-record or 4-record table acceptable
    # here: the data confirmed the layout, it did not referee it.
    "FieldLevelNameTableInfo": 2,
    "PartPrefabDyeTexturePalleteInfo": 11,
    "RelationInfo": 52,
    "royalsupply": 4,
    # ── added 2026-08-13: the .pdata function-bounds sweep ───────────────
    # These became derivable when field_reads stopped sweeping from an
    # arbitrary byte. capstone's disasm is a generator that STOPS at the
    # first undecodable byte, so the old sweep truncated -- on these tables
    # it died before the first field. All eleven are FULLY STATIC: no width
    # came from a data search.
    "AIEventTableInfo": 988,
    "UIMapTextureInfo": 2025,
    "KnowledgeGroupInfo": 600,
    "QuestGaugeInfo": 509,
    "GameAdviceInfo": 472,
    "CategoryInfo": 432,
    "GimmickGateInfo": 396,
    "gimmickgateconnection": 242,
    "EquipTypeInfo": 112,
    "QuestGroupInfo": 42,
    "AIActionAttributeInfo": 2,
    # ── added 2026-08-13 ─────────────────────────────────────────────────
    # LevelActionPointInfo came from re-running the substruct wiring after
    # the .pdata sweep resolved 124 more fields.
    #
    # MaterialBloodDecalInfo is the one table on this branch whose ORDER was
    # corrected against the bytes. The hot-path ranking put the list before
    # _skillKey and tiled 0 of 13; that reading needs an element count of
    # 0x15F90 (89,999), so it cannot be right. See its _meta_note.
    "LevelActionPointInfo": 60,
    "MaterialBloodDecalInfo": 13,
    # Unlocked by resolving register-carried read widths: MSVC hoists a
    # literal into a callee-saved register when a deserialiser does many
    # reads of the same size, so the width is `mov r8d, r15d` rather than an
    # immediate. Resolved by UNIQUE DEFINITION over the function.
    "uitalktreeinfo": 175,
    # ── added 2026-08-13: a struct-shaped FIELD reader ────────────────────
    # _wayPointData is a single struct, not a list element, whose width is
    # not constant because it ends in a counted list. Decomposed by
    # Deriver.reader_parts. Two sibling tables were built the same way and
    # ROLLED BACK for tiling 0 records, which is the distinction that
    # matters: decomposable is not the same as correct.
    "factionwaypoint": 475,
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


# ── the fixed-vs-variable list element trap ──────────────────────────────
#
# Two entries shipped in v3.13 described a list element as a FIXED width when
# the element actually ends in a string. Stage 2 recovers an element width by
# fitting the table data, and `Deriver.candidates()` offers only fixed-width
# elements -- there is no variable-element list shape in that space. So for
# these two the search could not see the competing hypothesis, and
# "unique-or-nothing" reported uniqueness inside an incomplete space.
#
# It fitted because every string in this build happens to be one length:
#   HouseInfo        7 element strings, all 34 bytes -> 12 + 34 == 46
#   FailMessageInfo 18 element strings, all 16 bytes -> 17 + 16 == 33
# Both the fixed and the variable model tile 100% of records EXACTLY, so no
# amount of data from this build separates them. The reader's disassembly
# does: the element makes a CString / LocalizableString read.
#
# These pin the corrected shape so the wrong one cannot come back. A future
# derivation re-running stage 2 on a build where the strings are still
# uniform would fit the fixed width again and look perfectly proven.

#: field -> (struct descriptor it must use, the fixed width it must NOT use)
_VARIABLE_ELEMENT_LISTS = {
    ("HouseInfo", "_houseRegionDataList"):
        ("CArray<HouseInfo_RegionDataEntry>", "CArray<[u8;46]>"),
    ("FailMessageInfo", "_failMessageInfoList"):
        ("CArray<FailMessageInfo_Entry>", "CArray<[u8;33]>"),
}


@pytest.mark.parametrize("key", sorted(_VARIABLE_ELEMENT_LISTS))
def test_variable_length_list_elements_are_not_modelled_as_fixed(key):
    table, field = key
    want, forbidden = _VARIABLE_ELEMENT_LISTS[key]
    got = _overrides()[table][field]["type"]
    assert got != forbidden, (
        f"{table}.{field} is back to {forbidden}. That width only fits "
        f"because every element string in the measured build was one "
        f"length; the element is variable and this will mis-frame the "
        f"record as soon as one string differs.")
    assert got == want, (
        f"{table}.{field} must be {want}, got {got}")


@pytest.mark.parametrize("key", sorted(_VARIABLE_ELEMENT_LISTS))
def test_the_corrected_element_structs_end_in_a_string(key):
    """The correction is only meaningful if the struct really is variable.

    A struct of fixed members would reintroduce the same bug wearing a
    name, so this checks the last member is a string type rather than
    trusting the descriptor's label.
    """
    from cdumm.semantic.pabgb_types import SUBSTRUCT_DEFS
    want, _forbidden = _VARIABLE_ELEMENT_LISTS[key]
    name = want[len("CArray<"):-1]
    assert name in SUBSTRUCT_DEFS, f"{name} is not defined"
    members = SUBSTRUCT_DEFS[name]
    assert members, f"{name} has no members"
    assert members[-1][1] in ("CString", "LocalizableString"), (
        f"{name} must end in a variable-length string read; its last "
        f"member is {members[-1]!r}. If it does not, the element has a "
        f"constant width and the original CArray<[u8;N]> was right.")


@pytest.mark.parametrize("key", sorted(_VARIABLE_ELEMENT_LISTS))
def test_the_correction_is_documented_in_the_provenance(key):
    table, _field = key
    note = _overrides()[table].get("_meta_note", "")
    assert "CORRECTION" in note, (
        f"{table} was shipped with a wrong element width; its note must "
        f"record the correction so the history is not lost")


# ── WantedInfo: the phantom trailing NUL ─────────────────────────────────
#
# WantedInfo shipped with `_ordered_fields` starting at `_increasePrice` and
# with the default (NUL-skipping) entry header. That model tiles 0 of 35
# records: it places _useTargetPrice at offset 16, one byte past the end of a
# 16-byte record. It nonetheless read the right prices, because the wrong
# order and the mis-measured header cancelled on the one field that mattered
# -- _increasePrice sat at offset 7 either way, which is why the
# verified-only pass recorded 30/50/100/500/1500 correctly.
#
# There is no trailing NUL. The name is EMPTY on every record, so there is
# nothing to terminate; the byte at payload offset 6 is _isBlocked, which is
# the first field of essentially every table in this format. With
# _no_null_skip and _isBlocked first, all 16 bytes are accounted for on all
# 35 records: header 6, _isBlocked at 6, _increasePrice at 7..14,
# _useTargetPrice at 15.
#
# Exact tiling could NOT catch this: 1+8+1 and 8+1+1 both consume 10 bytes.
# The order oracle caught it -- field_reads' hot-path branch order had
# WantedInfo as one of 3 tables out of 40 disagreeing with its shipped order.

def test_wantedinfo_puts_isblocked_first_and_skips_no_null():
    w = _overrides()["WantedInfo"]
    assert w["_ordered_fields"] == [
        "_isBlocked", "_increasePrice", "_useTargetPrice"], (
        "WantedInfo's order regressed; _increasePrice-first tiles 0 of 35 "
        "records because _useTargetPrice then lands past the record end")
    assert w.get("_no_null_skip") is True, (
        "WantedInfo has no trailing NUL to skip -- its names are empty, and "
        "the byte previously skipped as one is _isBlocked")


def test_wantedinfo_price_offset_is_unchanged_by_the_correction():
    """The correction must not move the one writable field.

    `_increasePrice` is the only entry in `_verified_fields`, so it is the
    only field a mod can edit here. Under both the old and the corrected
    model it sits at offset 7 (old: 7-byte header + field 0; new: 6-byte
    header + 1-byte _isBlocked). If that ever stops being true, existing
    mods start writing somewhere else.
    """
    w = _overrides()["WantedInfo"]
    assert w["_verified_fields"] == ["_increasePrice"]
    header = 2 + 4                      # key_size 2, name_len u32, name empty
    order = w["_ordered_fields"]
    widths = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}
    off = header
    for name in order:
        if name == "_increasePrice":
            break
        off += widths[w[name]["type"]]
    assert off == 7, f"_increasePrice moved to offset {off}, must stay at 7"
