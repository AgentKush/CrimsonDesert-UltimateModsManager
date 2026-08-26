"""CD 2.0 (buildid 24934353) iteminfo layout -- GitHub #377.

The 26 August 2026 update made two independent changes to the item
record, and every pre-2.0 layout decodes 0 of the table's 6,810
records as a result:

* a 4-byte field (a u16 pair) inserted between ``max_endurance`` and
  ``repair_data_list``, on every record;
* the SubItem *None* sentinel renumbered 17 -> 18 inside
  ``drop_default_data`` (6,806 of 6,810 records carry the sentinel;
  the other 4 carry a populated tag-0 SubItem and were the alignment
  anchors that made the derivation unambiguous).

HOW THE PLACEMENT WAS PINNED
----------------------------
Byte-diffing against the committed b24773079 fixture could not locate
the insertion: 2.0 also renumbered item_use_info key values table-wide,
so first-divergence always landed on a changed VALUE, not the changed
structure. The placement came from a field-prefix walk instead: on the
4 tag-0 records (where the sentinel renumber cannot confound the walk),
every cd116b field position matches the old build byte-for-byte up to
``repair_data_list``, and the new build carries exactly 4 extra bytes
there before an otherwise identical tail.

WHY A u16 PAIR AND NOT ONE u32
------------------------------
Value domain across all 6,810 records: the first u16 is {0: 6055,
0xFFFF: 755}; the second is 0xFFFF on 6,688 with small real values on
the rest (20, 100, 50, 15, 30 ...) -- the same sentinel-plus-seconds
shape as ``respawn_time_seconds``. Read as one u32 the common value
would be the meaningless 0xFFFF0000.
"""
from __future__ import annotations

import pytest

import cdumm.engine.iteminfo_native_parser as IP
from cdumm.semantic.parser import parse_pabgh_index
from tests.fixture_loaders import (
    has_vanilla_b24773079,
    has_vanilla_b24934353,
    load_vanilla_b24773079,
    load_vanilla_b24934353,
)

_need_new = pytest.mark.skipif(
    not has_vanilla_b24934353("iteminfo.pabgb"),
    reason="b24934353 iteminfo fixture absent")
_need_old = pytest.mark.skipif(
    not has_vanilla_b24773079("iteminfo.pabgb"),
    reason="b24773079 iteminfo fixture absent")


def _detect(body: bytes, header: bytes):
    _ks, offs = parse_pabgh_index(header, "iteminfo")
    starts = sorted(offs.values())
    fields = IP.detect_iteminfo_layout(body, starts)
    label = next((lb for lb, f in IP._ITEM_LAYOUTS if f is fields),
                 "default" if fields is None else "?")
    return label, fields, starts


@_need_new
def test_the_20_table_detects_cd20_and_round_trips_byte_exact():
    """THE test: all 6,810 records decode and re-serialize identically.

    Byte-exact round-trip alone cannot prove a layout (an opaque carry
    round-trips too), so the decode count is asserted with it: every
    record must expose its real fields, not carried bytes.
    """
    body = load_vanilla_b24934353("iteminfo.pabgb")
    header = load_vanilla_b24934353("iteminfo.pabgh")
    label, fields, starts = _detect(body, header)
    assert label == "cd20"
    items = IP.parse_iteminfo_from_bytes(body, starts, fields)
    assert len(items) == 6810
    assert IP.serialize_iteminfo(items, fields=fields) == body
    decoded = sum(1 for it in items if it.get("max_stack_count") is not None)
    assert decoded == 6810, (
        f"only {decoded} records decode real fields -- the rest fell "
        f"back to opaque carry, which is exactly the #377 failure")


@_need_new
def test_the_new_u16_pair_has_its_measured_value_domain():
    """Pins the domains that justified reading the 4 bytes as two u16s.

    A wrong placement that still round-trips shows up here as the
    domains going incoherent -- the same value-domain gate that caught
    the prefab misplacement in #369.
    """
    body = load_vanilla_b24934353("iteminfo.pabgb")
    header = load_vanilla_b24934353("iteminfo.pabgh")
    _label, fields, starts = _detect(body, header)
    items = IP.parse_iteminfo_from_bytes(body, starts, fields)
    a_vals = {it["unk_pre_repair_20_a"] for it in items}
    b_common = sum(1 for it in items if it["unk_pre_repair_20_b"] == 0xFFFF)
    assert a_vals <= {0, 0xFFFF}, sorted(a_vals)[:8]
    assert b_common > len(items) * 0.9


@_need_new
def test_the_20_subitem_sentinel_is_18():
    body = load_vanilla_b24934353("iteminfo.pabgb")
    header = load_vanilla_b24934353("iteminfo.pabgh")
    _label, fields, starts = _detect(body, header)
    items = IP.parse_iteminfo_from_bytes(body, starts, fields)
    tags = {it["drop_default_data"]["default_sub_item"]["type_id"]
            for it in items
            if isinstance(it.get("drop_default_data"), dict)}
    assert 18 in tags
    assert 17 not in tags, (
        "tag 17 reappeared -- the CD20 None set excludes it on purpose "
        "so a reuse as a populated tag fails loudly; re-derive before "
        "widening the set")


@_need_old
def test_the_previous_build_still_selects_cd116b():
    """cd20 and cd116b must never compete: each scores 0 on the other's
    table, so adding cd20 cannot regress b24773079 installs."""
    body = load_vanilla_b24773079("iteminfo.pabgb")
    header = load_vanilla_b24773079("iteminfo.pabgh")
    label, _fields, _starts = _detect(body, header)
    assert label == "cd116b"


@_need_new
def test_a_stack_edit_applies_end_to_end_on_20():
    """The user-facing capability #377 is about: a Format 3 stack edit
    lands, size-preserved, and re-parses with the new value."""
    from dataclasses import dataclass
    from typing import Any

    import cdumm.engine.iteminfo_writer as IW

    @dataclass
    class _Intent:
        entry: str
        key: int
        field: str
        op: str = "set"
        new: Any = None
        old: Any = None

    body = load_vanilla_b24934353("iteminfo.pabgb")
    header = load_vanilla_b24934353("iteminfo.pabgh")
    _label, fields, starts = _detect(body, header)
    items = IP.parse_iteminfo_from_bytes(body, starts, fields)
    target = next(it for it in items
                  if it.get("max_stack_count") not in (None, 999))
    change = IW.build_iteminfo_intent_change(
        body,
        [_Intent(entry="", key=target["key"], field="max_stack_count",
                 new=999)],
        vanilla_header=header)
    assert change is not None
    patched = bytearray(body)
    off = change["offset"]
    orig = bytes.fromhex(change["original"])
    assert patched[off:off + len(orig)] == orig
    patched[off:off + len(orig)] = bytes.fromhex(change["patched"])
    assert len(patched) == len(body), "edit must be size-preserving"
    items2 = IP.parse_iteminfo_from_bytes(bytes(patched), starts, fields)
    got = next(it for it in items2 if it["key"] == target["key"])
    assert got["max_stack_count"] == 999
