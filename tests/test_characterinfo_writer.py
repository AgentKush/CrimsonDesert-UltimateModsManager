"""Tests for the characterinfo.pabgb field writer (GitHub #150).

These build a synthetic characterinfo record to the exact shape the
parser walk expects, so the test is fast and self-contained. The
real-file verification (all 7027 records parse, the Female Animations
mod's 15 intents apply to the exact bytes the Damian record proves)
was done against the vanilla 1.07.00 dump during development.

The five fields the writer supports all sit relative to the
action-chart / skeleton block:
  upper_chart.group_lookup  block + 0   u32
  lower_chart.group_lookup  block + 4   u32
  skeleton_name             block + 20  u32
  lookup_25                 block + 24  u32
  flag_c                    block + 62  u8
"""
from __future__ import annotations

import struct

from cdumm.engine.characterinfo_writer import build_characterinfo_changes


def _make_record(key: int, name: str, *, upper: int, lower: int,
                 gameplay: int, appearance: int, prefab: int,
                 skeleton: int, skelvar: int, flag_c: int,
                 vehicle: int = 0, cool: int = 0, spawn: int = 0,
                 merctype: int = 0, spawnfix: int = 0,
                 catchable: int = 0) -> bytes:
    """Build one characterinfo record matching the parser walk in
    characterinfo_full_parser.parse_entry."""
    nb = name.encode("latin-1")
    r = bytearray()
    r += struct.pack("<I", key)               # entry_key
    r += struct.pack("<I", len(nb)) + nb      # name CString
    r += b"\x00"                              # _isBlocked u8
    r += b"\x00" + b"\x00" * 8 + b"\x00" * 4  # locstr 1 (len 0)
    r += b"\x00" + b"\x00" * 8 + b"\x00" * 4  # locstr 2 (len 0)
    r += b"\x00" * 4 + b"\x00" * 4            # two u32
    r += b"\x00" * 4                          # CString (len 0)
    r += struct.pack("<BB", spawnfix, catchable)  # _spawnFixType,_isRemoteCatchable
    r += b"\x00" * 4 + b"\x00" * 4            # two u32 (_keyLookup_a,_b)
    r += struct.pack("<H", vehicle)           # _vehicleInfo u16
    r += struct.pack("<Q", cool)              # _callMercenaryCoolTime
    r += struct.pack("<Q", spawn)             # _callMercenarySpawnDuration
    r += struct.pack("<B", merctype)          # _mercenaryCoolTimeType u8
    r += b"\x00" * 6                          # u32 + u16
    r += b"\x00" * 6                          # u32 + u16
    r += b"\x00" * 4                          # u32
    # action-chart / skeleton block: 7 u32
    r += struct.pack("<IIIIIII", upper, lower, gameplay, appearance,
                     prefab, skeleton, skelvar)
    # post-block fixed run: u32 + u64 + 5*u32 = 32 bytes
    r += b"\x00" * 32
    # four u8: flag_c is index 2
    r += bytes([0, 0, flag_c & 0xFF, 0])
    r += b"\x00" + b"\x00" * 8 + b"\x00" * 4  # locstr (len 0)
    r += b"\x00" * 4                          # u32
    r += b"\x00"                              # u8
    r += b"\x00" * 2                          # u16
    r += b"\x00" * 40                         # bool block
    return bytes(r)


def _make_table(records: list[bytes]) -> tuple[bytes, bytes]:
    pabgb = bytearray()
    entries: list[tuple[int, int]] = []
    for rec in records:
        key = struct.unpack_from("<I", rec, 0)[0]
        entries.append((key, len(pabgb)))
        pabgb += rec
    pabgh = bytearray(struct.pack("<H", len(entries)))
    for key, off in entries:
        pabgh += struct.pack("<II", key, off)
    return bytes(pabgb), bytes(pabgh)


def _apply(body: bytes, changes: list[dict]) -> bytes:
    work = bytearray(body)
    for c in changes:
        off = c["offset"]
        orig = bytes.fromhex(c["original"])
        patched = bytes.fromhex(c["patched"])
        assert work[off:off + len(orig)] == orig, "original mismatch"
        work[off:off + len(patched)] = patched
    return bytes(work)


def _vanilla_kwargs() -> dict:
    return dict(upper=11, lower=22, gameplay=33, appearance=44,
                prefab=55, skeleton=66, skelvar=77, flag_c=1)


def test_writer_locates_and_patches_all_five_fields():
    rec = _make_record(1, "Kliff", **_vanilla_kwargs())
    pabgb, pabgh = _make_table([rec])
    intents = [
        ("Kliff", 0, "upper_chart.group_lookup", 1767116530),
        ("Kliff", 0, "lower_chart.group_lookup", 3755051597),
        ("Kliff", 0, "skeleton_name", 2831867940),
        ("Kliff", 0, "lookup_25", 3511542393),
        ("Kliff", 0, "flag_c", 2),
    ]
    changes = build_characterinfo_changes(pabgb, pabgh, intents)
    assert len(changes) == 5
    patched = _apply(pabgb, changes)
    assert len(patched) == len(pabgb), "writes must not resize the record"
    # block starts at a known offset for this synthetic record; verify
    # by re-reading every field through the parser instead.
    from cdumm.archive.format_parsers.characterinfo_full_parser import (
        parse_pabgh_index, parse_entry,
    )
    idx = parse_pabgh_index(pabgh)
    r = parse_entry(patched, idx[1], len(patched))
    assert r["_upperActionChartPackageGroupName_key"] == 1767116530
    assert r["_lowerActionChartPackageGroupName_key"] == 3755051597
    assert r["_skeletonName_key"] == 2831867940
    assert r["_skeletonVariationName_key"] == 3511542393
    assert r["_flagC"] == 2


def test_writer_resolves_by_numeric_key_when_name_misses():
    rec = _make_record(4242, "Real_Name", **_vanilla_kwargs())
    pabgb, pabgh = _make_table([rec])
    intents = [("Wrong_Name", 4242, "skeleton_name", 999)]
    changes = build_characterinfo_changes(pabgb, pabgh, intents)
    assert len(changes) == 1
    from cdumm.archive.format_parsers.characterinfo_full_parser import (
        parse_pabgh_index, parse_entry,
    )
    patched = _apply(pabgb, changes)
    r = parse_entry(patched, parse_pabgh_index(pabgh)[4242], len(patched))
    assert r["_skeletonName_key"] == 999


def test_writer_only_touches_targeted_records():
    recs = [
        _make_record(1, "Kliff", **_vanilla_kwargs()),
        _make_record(2, "Untouched", **_vanilla_kwargs()),
    ]
    pabgb, pabgh = _make_table(recs)
    intents = [("Kliff", 0, "flag_c", 2)]
    changes = build_characterinfo_changes(pabgb, pabgh, intents)
    assert len(changes) == 1
    patched = _apply(pabgb, changes)
    # record 2 is byte-identical
    assert patched[len(recs[0]):] == pabgb[len(recs[0]):]


def test_writer_skips_unsupported_field_and_bad_value():
    rec = _make_record(1, "Kliff", **_vanilla_kwargs())
    pabgb, pabgh = _make_table([rec])
    intents = [
        ("Kliff", 0, "not_a_real_field", 5),
        ("Kliff", 0, "flag_c", "two"),          # non-integer
        ("Kliff", 0, "flag_c", 999),            # out of u8 range
        ("Kliff", 0, "skeleton_name", 12345),   # the one good intent
    ]
    changes = build_characterinfo_changes(pabgb, pabgh, intents)
    assert len(changes) == 1
    assert changes[0]["label"] == "Kliff.skeleton_name"


def test_writer_skips_intent_for_missing_record():
    rec = _make_record(1, "Kliff", **_vanilla_kwargs())
    pabgb, pabgh = _make_table([rec])
    intents = [("Ghost", 9999, "skeleton_name", 1)]
    changes = build_characterinfo_changes(pabgb, pabgh, intents)
    assert changes == []


def test_writer_patches_lookup_22_appearance_and_lookup_24_prefab():
    """GitHub #192 (Yorivel): mesh / visual-swap mods set the appearance
    hash (lookup_22 -> _appearanceName at block+12) and the model path
    (lookup_24 -> _characterPrefabPath at block+16). Both are plain u32
    name-hash slots in the same action-chart block as the five #150
    fields, so they resolve through the same parser-walk mechanism."""
    rec = _make_record(1, "Kliff", **_vanilla_kwargs())
    pabgb, pabgh = _make_table([rec])
    intents = [
        ("Kliff", 0, "lookup_22", 1234567890),  # appearance hash
        ("Kliff", 0, "lookup_24", 987654321),   # prefab path hash
    ]
    changes = build_characterinfo_changes(pabgb, pabgh, intents)
    assert len(changes) == 2, (
        "both lookup_22 and lookup_24 must resolve to a write")
    patched = _apply(pabgb, changes)
    assert len(patched) == len(pabgb), "writes must not resize the record"
    from cdumm.archive.format_parsers.characterinfo_full_parser import (
        parse_pabgh_index, parse_entry,
    )
    r = parse_entry(patched, parse_pabgh_index(pabgh)[1], len(patched))
    assert r["_appearanceName_key"] == 1234567890
    assert r["_characterPrefabPath_key"] == 987654321
    # The neighbouring slots (gameplay at block+8, skeleton at block+20)
    # must be untouched, proving the offsets are exact.
    assert r["_skeletonName_key"] == 66      # vanilla skeleton value
    assert r["_upperActionChartPackageGroupName_key"] == 11


def test_lookup_22_24_in_format3_characterinfo_accept_set():
    """The format3_handler validator must accept lookup_22 / lookup_24
    on characterinfo, otherwise the writer never sees the intents. This
    pins the accept-set against the writer's SUPPORTED_FIELDS so the two
    cannot drift apart again (the recurring maintenance hazard the #150
    comment warned about)."""
    from cdumm.engine.characterinfo_writer import SUPPORTED_FIELDS
    assert "lookup_22" in SUPPORTED_FIELDS
    assert "lookup_24" in SUPPORTED_FIELDS


# --- Mount / vehicle fields (2026-07-07) -----------------------------------
# These four scalars are located by the schema `_ordered_fields` walk, NOT by
# parse_entry (which fails on real mount records). Verified byte-exact on the
# live 1.13 game: 18/18 real mounts, all four fields, + a 33/33 vehicleinfo
# foreign-key cross-check. The synthetic-record write test below doubles as a
# guard that the writer's walk lands on the same bytes the record carries.
_MOUNT_FIELDS = (
    "_vehicleInfo", "_callMercenaryCoolTime",
    "_callMercenarySpawnDuration", "_mercenaryCoolTimeType",
)
# Two clean gameplay enums that precede the mount block; same walk resolution.
_LEADING_ENUMS = ("_spawnFixType", "_isRemoteCatchable")
_VERIFIED_CHARACTERINFO = frozenset(_MOUNT_FIELDS + _LEADING_ENUMS)


def test_mount_fields_in_characterinfo_accept_set():
    from cdumm.engine.characterinfo_writer import SUPPORTED_FIELDS, _FIELD_MAP
    for f in _MOUNT_FIELDS + _LEADING_ENUMS:
        assert f in SUPPORTED_FIELDS, f"{f} must be accepted by the writer"
        # they resolve via the schema walk, not the parse_entry offset map
        assert f not in _FIELD_MAP


def test_characterinfo_mount_fields_verified_and_editable():
    from cdumm.semantic import parser as sem
    from cdumm.engine.format3_builder import is_editable_scalar_field
    sem.init_schemas()
    sch = sem.get_schema("characterinfo")
    assert sch is not None
    assert sch.verified_fields == _VERIFIED_CHARACTERINFO
    by = {f.name: f for f in sch.fields}
    for f in _VERIFIED_CHARACTERINFO:
        assert is_editable_scalar_field(by[f]), f"{f} must be an editable scalar"


def test_writer_patches_mount_fields_byte_exact():
    # distinctive live-like values (camel: vehicleInfo 16978, cool 300)
    rec = _make_record(30067, "Animal_Camel_Wild_30067",
                       vehicle=16978, cool=300, spawn=600, merctype=1,
                       **_vanilla_kwargs())
    pabgb, pabgh = _make_table([rec])
    cases = {
        "_vehicleInfo": ("<H", 16978, 16999),
        "_callMercenaryCoolTime": ("<Q", 300, 12345),
        "_callMercenarySpawnDuration": ("<Q", 600, 54321),
        "_mercenaryCoolTimeType": ("<B", 1, 2),
    }
    for field, (fmt, old, new) in cases.items():
        changes = build_characterinfo_changes(
            pabgb, pabgh, [("Animal_Camel_Wild_30067", 30067, field, new)])
        assert len(changes) == 1, f"{field} did not resolve to a write"
        # the writer's walk must land on the byte holding the value we set
        assert bytes.fromhex(changes[0]["original"]) == struct.pack(fmt, old), (
            f"{field}: writer read the wrong offset")
        assert bytes.fromhex(changes[0]["patched"]) == struct.pack(fmt, new)
        patched = _apply(pabgb, changes)
        assert len(patched) == len(pabgb)
    # a full re-parse sees the new values and nothing else moved
    from cdumm.semantic import parser as sem
    sem.init_schemas()
    all_changes = []
    for field, (fmt, old, new) in cases.items():
        all_changes += build_characterinfo_changes(
            pabgb, pabgh, [("Animal_Camel_Wild_30067", 30067, field, new)])
    patched = _apply(pabgb, all_changes)
    r = sem.parse_records_display("characterinfo", patched, pabgh)[30067]
    assert r["_vehicleInfo"] == 16999
    assert r["_callMercenaryCoolTime"] == 12345
    assert r["_callMercenarySpawnDuration"] == 54321
    assert r["_mercenaryCoolTimeType"] == 2


def test_writer_patches_leading_enums_byte_exact():
    # _spawnFixType / _isRemoteCatchable precede the mount block; the walk must
    # land on them the same way (offsets transitively verified by the mount FK).
    rec = _make_record(1234, "NPC", spawnfix=5, catchable=3, **_vanilla_kwargs())
    pabgb, pabgh = _make_table([rec])
    cases = {"_spawnFixType": (5, 7), "_isRemoteCatchable": (3, 1)}
    all_changes = []
    for field, (old, new) in cases.items():
        changes = build_characterinfo_changes(
            pabgb, pabgh, [("NPC", 1234, field, new)])
        assert len(changes) == 1, f"{field} did not resolve to a write"
        assert bytes.fromhex(changes[0]["original"]) == struct.pack("<B", old), (
            f"{field}: writer read the wrong offset")
        all_changes += changes
    from cdumm.semantic import parser as sem
    sem.init_schemas()
    patched = _apply(pabgb, all_changes)
    r = sem.parse_records_display("characterinfo", patched, pabgh)[1234]
    assert r["_spawnFixType"] == 7
    assert r["_isRemoteCatchable"] == 1
