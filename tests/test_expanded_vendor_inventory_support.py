"""GitHub #191 (Damascinas): AerowynX's Expanded Vendor Inventory Rebuilt
V3 (Nexus 3290) and its Dye Addon were rejected at import ("intent #0 is
missing 'new'") and, past that, needed three things CDUMM lacked:

* DMM's ``array_append`` carries the element under ``value``
* append + per-slot edits (``stock_data_list[N].raw_c`` / ``.sub_data``)
  on storeinfo stock lists, and append on npcinfo dye lists
* a writer for dyecolorgroupinfo's colour list

Held to the committed CD 2.00.01 bytes.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cdumm.engine.dyecolorgroupinfo_writer import (
    DyecolorgroupinfoWriteRefused,
    build_dyecolorgroupinfo_changes,
    locate_color_list,
)
from cdumm.engine.format3_handler import parse_format3_mod_targets, validate_intents
from cdumm.engine.npcinfo_writer import build_npcinfo_changes, locate_dye_lists
from cdumm.engine.storeinfo_native_parser import (
    detect_storeinfo_layout,
    locate_stock_list,
)
from cdumm.engine.storeinfo_writer import StoreinfoWriteRefused, build_storeinfo_changes
from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index
from tests.fixture_loaders import has_vanilla_b24994088, load_vanilla_b24994088

pytestmark = pytest.mark.skipif(
    not has_vanilla_b24994088("dyecolorgroupinfo.pabgb"),
    reason="CD 2.00.01 fixtures absent")

KWE_GROUP = 1586656          # Kwe_Color_Group_I
THEORIC = 1000221            # NHM_Unique_Theoric_npc
DYE_STORE = 3803             # Store_Her_Dye_00


def _intent(key, field, op, val):
    return SimpleNamespace(key=key, entry="", field=field, op=op, new=val,
                           match=None, clone=None, old=None)


def _apply(body, header, changes, pabgh):
    buf = bytearray(body)
    for c in sorted(changes, key=lambda c: -c["offset"]):
        o = c["offset"]
        orig = bytes.fromhex(c["original"])
        assert buf[o:o + len(orig)] == orig
        buf[o:o + len(orig)] = bytes.fromhex(c["patched"])
    return bytes(buf), (bytes.fromhex(pabgh["patched"]) if pabgh else header)


def _entry(body, header, table, key):
    ks, offs = parse_pabgh_index(header, table)
    so = sorted(offs.values()) + [len(body)]
    off = offs[key]
    end = so[so.index(off) + 1]
    _e, _n, payload = _parse_entry_header(body, off, ks)
    return payload, end


# ── parser: DMM array_append shape ──────────────────────────────────

def test_parser_accepts_value_as_new_for_array_append(tmp_path):
    mod = {"format": 3, "targets": [{"file": "npcinfo.pabgb", "intents": [
        {"entry": "NHM_Unique_Theoric_npc", "key": THEORIC, "op": "array_append",
         "field": "dye_color_group_data_list",
         "value": {"dye_color_group_key": 3693560950, "dye_target_key": 0}}]}]}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(mod), encoding="utf-8")
    pairs = parse_format3_mod_targets(p)
    (_t, intents), = pairs
    assert intents[0].op == "array_append"
    assert intents[0].new == {"dye_color_group_key": 3693560950, "dye_target_key": 0}


def test_validator_accepts_appends_and_slot_edits():
    v = validate_intents("storeinfo.pabgb", [
        _intent(DYE_STORE, "stock_data_list", "array_append", {"value": {}}),
        _intent(DYE_STORE, "stock_data_list[2].raw_c", "set", 999),
        _intent(DYE_STORE, "stock_data_list[2].sub_data", "set", None),
    ])
    assert not v.skipped, v.skipped
    v2 = validate_intents("dyecolorgroupinfo.pabgb", [
        _intent(KWE_GROUP, "dye_color_data_list", "array_append",
                {"raw_color": 1, "texture_lookup": 2})])
    assert not v2.skipped, v2.skipped


# ── dyecolorgroupinfo ───────────────────────────────────────────────

def test_every_vanilla_group_has_109_colours():
    body = load_vanilla_b24994088("dyecolorgroupinfo.pabgb")
    header = load_vanilla_b24994088("dyecolorgroupinfo.pabgh")
    _ks, offs = parse_pabgh_index(header, "dyecolorgroupinfo")
    assert len(offs) == 10
    for key in offs:
        payload, end = _entry(body, header, "dyecolorgroupinfo", key)
        _s, _e, elems = locate_color_list(body, payload, end, key)
        assert len(elems) == 109
        assert len({t for _c, t in elems}) == 6  # six texture lookups per group


def test_append_22_colours_to_a_group_keeps_everything_else():
    body = load_vanilla_b24994088("dyecolorgroupinfo.pabgb")
    header = load_vanilla_b24994088("dyecolorgroupinfo.pabgh")
    intents = [_intent(KWE_GROUP, "dye_color_data_list", "array_append",
                       {"raw_color": 4278255513 + i, "texture_lookup": 1008648})
               for i in range(22)]
    changes, pabgh = build_dyecolorgroupinfo_changes(body, header, intents)
    assert len(changes) == 1 and pabgh is not None
    nb, nh = _apply(body, header, changes, pabgh)
    payload, end = _entry(nb, nh, "dyecolorgroupinfo", KWE_GROUP)
    _s, e_, elems = locate_color_list(nb, payload, end, KWE_GROUP)
    assert len(elems) == 131
    assert elems[109] == (4278255513, 1008648)
    # tail carried verbatim
    p0, end0 = _entry(body, header, "dyecolorgroupinfo", KWE_GROUP)
    assert nb[e_:end] == body[p0 + 4 + 109 * 8:end0]
    # every other group byte-identical
    for key in parse_pabgh_index(header, "dyecolorgroupinfo")[1]:
        if key == KWE_GROUP:
            continue
        pa, ea = _entry(body, header, "dyecolorgroupinfo", key)
        pb, eb = _entry(nb, nh, "dyecolorgroupinfo", key)
        assert body[pa:ea] == nb[pb:eb]


def test_dye_group_bad_element_refused():
    body = load_vanilla_b24994088("dyecolorgroupinfo.pabgb")
    header = load_vanilla_b24994088("dyecolorgroupinfo.pabgh")
    with pytest.raises(DyecolorgroupinfoWriteRefused):
        build_dyecolorgroupinfo_changes(body, header, [
            _intent(KWE_GROUP, "dye_color_data_list", "array_append",
                    {"raw_color": -1, "texture_lookup": 1})])


# ── npcinfo append ──────────────────────────────────────────────────

def test_npcinfo_append_extends_vanilla_lists():
    body = load_vanilla_b24994088("npcinfo.pabgb")
    header = load_vanilla_b24994088("npcinfo.pabgh")
    intents = [
        _intent(THEORIC, "dye_color_group_data_list", "array_append",
                {"dye_color_group_key": 3693560950, "dye_target_key": 0}),
        _intent(THEORIC, "dye_texture_set_data_list", "array_append",
                {"texture_set_lookup": 2, "dye_target_key": 0}),
    ]
    changes, pabgh = build_npcinfo_changes(body, header, intents)
    nb, nh = _apply(body, header, changes, pabgh)
    payload, end = _entry(nb, nh, "npcinfo", THEORIC)
    dl = locate_dye_lists(nb, payload, end, THEORIC)
    assert [k for k, _t in dl.groups] == [3363967477, 3693560950]
    assert [lk for lk, _t in dl.texsets] == [1, 2]


# ── storeinfo append + slot edits ───────────────────────────────────

def _van_store(body, header, key):
    _ks, offs = parse_pabgh_index(header, "storeinfo")
    lay = detect_storeinfo_layout(body, sorted(offs.values()))
    payload, end = _entry(body, header, "storeinfo", key)
    recs, _s, _e = locate_stock_list(body, payload, end, key, lay)
    return recs, lay


def _new_record(item_key: int) -> dict:
    return {"effect_list": [], "flag_a": 1, "flag_b": 0, "flag_c": 1,
            "is_restore_item": 0, "lookup_a": DYE_STORE, "lookup_b": 0,
            "lookup_c": 0, "low_price_threshold_count_116": 4294967295,
            "order_index_113": 4294967295, "raw_a": 1000000, "raw_b": 1000000,
            "raw_c": 999, "raw_d": 0, "raw_e": 0, "sub_data": None,
            "value": {"disc": 0, "payload": {"body": item_key, "type": "Disc0"},
                      "raw_e": 1, "raw_g": 65535, "raw_q": item_key}}


def test_storeinfo_slot_edit_then_append_in_order():
    body = load_vanilla_b24994088("storeinfo.pabgb")
    header = load_vanilla_b24994088("storeinfo.pabgh")
    van, _lay = _van_store(body, header, DYE_STORE)
    assert len(van) >= 3
    intents = [
        _intent(DYE_STORE, "stock_data_list[2].sub_data", "set", None),
        _intent(DYE_STORE, "stock_data_list[2].raw_c", "set", 999),
        _intent(DYE_STORE, "stock_data_list", "array_append", _new_record(1003828)),
        _intent(DYE_STORE, "stock_data_list", "array_append", _new_record(1004318)),
    ]
    changes, pabgh = build_storeinfo_changes(body, header, intents)
    assert changes and pabgh is not None
    nb, nh = _apply(body, header, changes, pabgh)
    recs, _lay = _van_store(nb, nh, DYE_STORE)
    assert len(recs) == len(van) + 2
    assert recs[2].raw_c == 999 and recs[2].sub_data is None
    assert recs[-2].body == 1003828 and recs[-1].body == 1004318
    # untouched slots byte-identical to vanilla
    for a, b in zip(van[:2], recs[:2]):
        assert a == b


def test_storeinfo_slot_edit_out_of_range_refused():
    body = load_vanilla_b24994088("storeinfo.pabgb")
    header = load_vanilla_b24994088("storeinfo.pabgh")
    with pytest.raises(StoreinfoWriteRefused):
        build_storeinfo_changes(body, header, [
            _intent(DYE_STORE, "stock_data_list[500].raw_c", "set", 1)])
