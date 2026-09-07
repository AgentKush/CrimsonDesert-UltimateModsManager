"""buildid 25116796 grew the populated default_sub_item by 4 bytes.

The 4 September 2026 update -- the same one that renamed the tables in
#402 and grew the storeinfo stock record in #403 -- also put four zero
bytes inside ``default_sub_item``, on the ``type_id == 0`` shape only.
392 of the 6,813 item records carry that shape and every one of them
stopped reading correctly. 387 raised mid-record and were carried
opaque; the other 5 stayed readable 4 bytes out of step and ended early,
leaving 96 to 163 bytes of ``_tail_slack`` and wrong values on every
field past ``default_sub_item``. Both failures round-trip byte-exact, so
nothing was damaged and no error surfaced -- the records simply became
uneditable (or editable at the wrong offset), and a Format 3 mod
touching one silently applied nothing.

HOW THE PLACEMENT WAS PINNED
----------------------------
Against the committed b24934353 table: exactly 394 records grew, all by
exactly 4 bytes, and every one of the 392 the retry claims reads
``default_sub_item.type_id == 0``. On the 305 whose bytes are otherwise
unchanged, the first differing byte is at offset 17 of an 18-byte field,
the four new bytes are zero, and the old byte at that position is
non-zero -- so the insert does not sit in a run of padding where several
placements would look alike.

WHY A PER-RECORD RETRY AND NOT A NEW LAYOUT
-------------------------------------------
``detect_iteminfo_layout`` breaks ties in favour of the later layout on
the stated assumption that a more specific layout round-trips a superset
of what an earlier one does. That does not hold here: the 18-byte and
22-byte shapes are mutually exclusive on the records that carry them, so
a sibling layout would win the tie on a b24934353 table too -- its sample
of seven records draws a type_id==0 record only about a third of the time
-- and carry those records opaque on the older build instead. The
retry decides per record, on a byte-exact round-trip, so both builds read
fully from the same layout.
"""
from __future__ import annotations

import pytest

import cdumm.engine.iteminfo_native_parser as IP
from cdumm.semantic.parser import parse_pabgh_index
from tests.fixture_loaders import (
    has_vanilla_b24934353,
    has_vanilla_b25116796,
    load_vanilla_b24934353,
    load_vanilla_b25116796,
)

_need_new = pytest.mark.skipif(
    not has_vanilla_b25116796("iteminfo.pabgb"),
    reason="b25116796 iteminfo fixture absent")
_need_old = pytest.mark.skipif(
    not has_vanilla_b24934353("iteminfo.pabgb"),
    reason="b24934353 iteminfo fixture absent")


def _parse(load):
    body = load("iteminfo.pabgb")
    header = load("iteminfo.pabgh")
    _ks, offs = parse_pabgh_index(header, "iteminfo")
    starts = sorted(offs.values())
    fields = IP.detect_iteminfo_layout(body, starts)
    items = IP.parse_iteminfo_from_bytes(body, starts, fields=fields)
    return body, header, fields, starts, items


@_need_new
def test_every_record_of_the_new_build_decodes_and_round_trips():
    """THE test: no record falls back to opaque carry, bytes unchanged.

    Byte-exact round-trip alone proves nothing here (an opaque carry
    round-trips too), so the opaque count is asserted alongside it.
    """
    body, _h, fields, _starts, items = _parse(load_vanilla_b25116796)
    assert len(items) == 6813
    opaque = [it["key"] for it in items if it.get("_opaque_record")]
    assert not opaque, (
        f"{len(opaque)} records still carried opaque, e.g. {opaque[:5]}")
    assert IP.serialize_iteminfo(items, fields=fields) == body


@_need_new
def test_the_four_bytes_land_only_on_the_type_id_0_shape():
    """Pins what the retry is allowed to claim.

    A wrong placement that still round-trips shows up here as ``unk_d``
    appearing on records that did not grow, or missing from ones that
    did -- the value-domain gate that caught the prefab misplacement in
    #369 and the repair-field one in #377.
    """
    _b, _h, _f, _s, items = _parse(load_vanilla_b25116796)
    padded = [it for it in items
              if "unk_d" in (it.get("default_sub_item") or {})]
    assert len(padded) == 392
    assert {it["default_sub_item"]["type_id"] for it in padded} == {0}
    assert {it["default_sub_item"]["unk_d"] for it in padded} == {0}
    # and nothing else on the table carries the padded shape
    others = [it for it in items
              if (it.get("default_sub_item") or {}).get("type_id") == 0
              and "unk_d" not in it["default_sub_item"]]
    assert not others, f"{len(others)} type_id==0 records read unpadded"
    # and the walk now accounts for every byte of every record
    assert not [it for it in items if it.get("_tail_slack")]


@_need_old
def test_the_previous_build_still_decodes_every_record_unpadded():
    """The retry must not claim a b24934353 record. Those read the
    18-byte shape and stay editable there."""
    body, _h, fields, _starts, items = _parse(load_vanilla_b24934353)
    assert len(items) == 6810
    assert not [it for it in items if it.get("_opaque_record")]
    assert not [it for it in items
                if "unk_d" in (it.get("default_sub_item") or {})]
    assert IP.serialize_iteminfo(items, fields=fields) == body


@_need_new
def test_a_stack_edit_applies_to_a_previously_opaque_record():
    """The user-facing capability: a Format 3 edit on one of the 387
    lands, size-preserved, and re-parses with the new value. Before this
    fix the record was carried opaque and the edit applied nothing."""
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

    body, header, fields, starts, items = _parse(load_vanilla_b25116796)
    target = next(it for it in items
                  if "unk_d" in (it.get("default_sub_item") or {})
                  and it.get("max_stack_count") not in (None, 999))
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
    items2 = IP.parse_iteminfo_from_bytes(bytes(patched), starts,
                                          fields=fields)
    got = next(it for it in items2 if it["key"] == target["key"])
    assert got["max_stack_count"] == 999
