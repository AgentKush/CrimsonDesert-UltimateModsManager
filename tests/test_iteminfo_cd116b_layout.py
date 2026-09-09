"""CD 1.16b iteminfo layout: PrefabData grew one u32 (see
``_read_PrefabData_CD116b``), pinned on the committed
``vanilla_b24773079`` table (Steam buildid 24773079).

cd116 round-trips only 1,025 of 6,573 records on this table (every
record whose ``prefab_data_list`` is empty; anything with at least one
prefab entry desyncs). The important thing these tests do that a bare
round-trip cannot: check the decoded VALUES, because a wrong split
inside the new element can still reproduce its own bytes on write.
Reading the new u32 AFTER ``is_craft_material``/``unk_flag_b``/
``unk_flag_c`` also round-trips the whole table byte-exact, but under
that placement the three u8s decode as the same non-boolean triple
(115, 225, 197) on literally every one of 12,274 prefab entries --
three fields that were 0 on nearly every CD 1.16 record all becoming
one constant nonzero triple table-wide is what a wrong split looks
like, not real data.
"""
from __future__ import annotations

import collections

import pytest

import cdumm.engine.iteminfo_native_parser as P
from cdumm.semantic.parser import parse_pabgh_index
from tests.fixture_loaders import (
    has_vanilla_b24773079,
    load_vanilla116,
    load_vanilla_b24773079,
)

pytestmark = pytest.mark.skipif(
    not has_vanilla_b24773079("iteminfo.pabgb"),
    reason="vanilla_b24773079 iteminfo fixture not present")


def _decode(body: bytes, header: bytes, fields):
    """key -> record, skipping records this layout cannot place."""
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    order = sorted(offs.items(), key=lambda kv: kv[1])
    starts = [o for _k, o in order]
    out = {}
    for i, (key, off) in enumerate(order):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        try:
            r = P._Reader(body, off, rec_end=end)
            rec = P._read_item(r, fields=fields)
        except Exception:  # noqa: BLE001, S112 -- a record this layout
            continue       # cannot place is the thing being counted
        if rec:
            out[key] = rec
    return out


@pytest.fixture(scope="module")
def t_b24773079():
    return (load_vanilla_b24773079("iteminfo.pabgb"),
            load_vanilla_b24773079("iteminfo.pabgh"))


def test_cd116b_is_selected_for_the_b24773079_table(t_b24773079):
    body, header = t_b24773079
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    chosen = P.detect_iteminfo_layout(body, sorted(offs.values()))
    assert chosen is not None
    names = [s[0] for s in chosen]
    assert "pre_respawn_116" in names, "still the cd116 tail otherwise"


def test_the_b24773079_table_round_trips(t_b24773079):
    """The whole table re-serializes byte-exact under cd116b -- unlike
    cd116, which manages only records with an empty prefab_data_list."""
    body, header = t_b24773079
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    order = sorted(offs.items(), key=lambda kv: kv[1])
    starts = [o for _k, o in order]
    exact = 0
    for i, (_key, off) in enumerate(order):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        try:
            r = P._Reader(body, off, rec_end=end)
            rec = P._read_item(r, fields=P._ITEM_FIELDS_CD116B)
            w = P._Writer()
            P._write_item(w, rec, fields=P._ITEM_FIELDS_CD116B)
        except Exception:  # noqa: BLE001, S112 -- see above
            continue
        if bytes(w.buf) == body[off:end]:
            exact += 1
    assert exact == len(order), (
        f"only {exact}/{len(order)} records round-trip byte-exact")


def test_prefab_flags_stay_in_the_boolean_domain(t_b24773079):
    """The value-domain check a byte-exact round-trip cannot make:
    is_craft_material and unk_flag_b are boolean-shaped on the CD 1.16
    fixture, and stay that way here. A split that reads the new u32's
    bytes into them instead would push both out of {0, 1}."""
    d = _decode(*t_b24773079, P._ITEM_FIELDS_CD116B)
    icm = collections.Counter()
    ufb = collections.Counter()
    for rec in d.values():
        for pd in (rec.get("prefab_data_list") or []):
            icm[pd["is_craft_material"]] += 1
            ufb[pd["unk_flag_b"]] += 1
    assert set(icm) <= {0, 1}, f"is_craft_material left {{0,1}}: {icm}"
    assert set(ufb) <= {0, 1}, f"unk_flag_b left {{0,1}}: {ufb}"
    assert icm.total() > 10000 and ufb.total() > 10000


def test_unk_prefab_hash_is_a_constant_sentinel(t_b24773079):
    """The new field is 0xEAC5E173 on every one of 12,274 prefab
    entries sampled during derivation -- a fixed sentinel, not per-item
    data. A wrong split would show a spread of values instead (whatever
    is_craft_material/unk_flag_b/unk_flag_c actually carry)."""
    d = _decode(*t_b24773079, P._ITEM_FIELDS_CD116B)
    vals = collections.Counter()
    for rec in d.values():
        for pd in (rec.get("prefab_data_list") or []):
            vals[pd["unk_prefab_hash"]] += 1
    assert vals == {3938836851: vals[3938836851]}, (
        f"expected one constant sentinel, got {dict(vals)}")
    assert vals[3938836851] > 10000


def test_cd116b_does_not_win_on_the_116_table():
    """The regression that would corrupt existing installs: a new
    layout scoring high enough on an older table to be chosen for it.

    cd116b's PrefabData reader expects 4 extra bytes per element that
    the CD 1.16 table doesn't have, so it desyncs on every record whose
    prefab_data_list isn't empty -- it can only tie cd116 on records
    with none at all, and loses outright on the rest.
    """
    body = load_vanilla116("iteminfo.pabgb")
    header = load_vanilla116("iteminfo.pabgh")
    _keys, offs = parse_pabgh_index(header, "iteminfo")
    chosen = P.detect_iteminfo_layout(body, sorted(offs.values()))
    assert chosen is P._ITEM_FIELDS_CD116, (
        "cd116b must not outscore cd116 on the CD 1.16 table")
