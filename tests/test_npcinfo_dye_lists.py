"""GitHub #393 (delichandelarosse): donr484's Dye Hard (Nexus 3270) sets
two list fields on each world Dyer's npcinfo record and CDUMM had no
writer for the table, so all 20 intents were skipped.

Layout evidence and the writer live in ``cdumm.engine.npcinfo_writer``.
These tests hold that layout to the committed CD 2.0 vanilla bytes:

* the ten world Dyers and the Camp Dyer (the only vanilla NPC with
  10/10 lists) locate and decode as the module documents
* a Dye Hard-shaped rewrite (10 groups + 10 texsets, target 0) lands,
  re-indexes, and leaves every other entry byte-identical
* a set that equals vanilla is a no-op
* an entry that does not carry the anchor blobs is refused, not patched
"""
from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from cdumm.engine.npcinfo_writer import (
    NpcinfoWriteRefused,
    build_npcinfo_changes,
    locate_dye_lists,
)
from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index
from tests.fixture_loaders import (
    has_vanilla_b24994088,
    load_vanilla_b24994088,
)

pytestmark = pytest.mark.skipif(
    not has_vanilla_b24994088("npcinfo.pabgb"),
    reason="CD 2.0 npcinfo fixture absent")

DYERS = list(range(1000221, 1000231))
CAMP_DYER = 1000342  # NHM_Unique_Oliver_649_npc

# The ten colour-group keys Dye Hard writes, which are also -- in this
# exact order -- the Camp Dyer's vanilla list.
GROUP_KEYS = [3363967477, 3693560950, 110038222, 1081114260, 2817281435,
              1586656, 755308489, 713422964, 1329654226, 1196838804]


def _table():
    return load_vanilla_b24994088("npcinfo.pabgb"), load_vanilla_b24994088("npcinfo.pabgh")


def _entries(body, header):
    ks, offs = parse_pabgh_index(header, "npcinfo")
    so = sorted(offs.values()) + [len(body)]
    out = {}
    for key, off in offs.items():
        end = so[so.index(off) + 1]
        _eid, name, payload = _parse_entry_header(body, off, ks)
        out[key] = (off, end, payload, name)
    return ks, out


def _intent(key, field, new):
    return SimpleNamespace(key=key, entry="", field=field, op="set", new=new)


def test_camp_dyer_vanilla_lists_are_the_dye_hard_set():
    body, header = _table()
    _ks, ents = _entries(body, header)
    _off, end, payload, name = ents[CAMP_DYER]
    assert name == "NHM_Unique_Oliver_649_npc"
    dl = locate_dye_lists(body, payload, end, CAMP_DYER)
    assert [k for k, _t in dl.groups] == GROUP_KEYS
    assert [lk for lk, _t in dl.texsets] == list(range(1, 11))
    # target = the per-Dyer "found" unlock keys, in step for both lists
    assert [t for _k, t in dl.groups] == list(range(1000091, 1000101))
    assert [t for _lk, t in dl.texsets] == list(range(1000091, 1000101))
    assert end - dl.list_end == 4


def test_world_dyers_each_carry_their_own_group_and_lookup():
    body, header = _table()
    _ks, ents = _entries(body, header)
    seen_groups, seen_lookups = set(), set()
    for key in DYERS:
        _off, end, payload, _name = ents[key]
        dl = locate_dye_lists(body, payload, end, key)
        assert len(dl.groups) == 1 and len(dl.texsets) == 1
        assert dl.groups[0][0] in GROUP_KEYS
        seen_groups.add(dl.groups[0][0])
        seen_lookups.add(dl.texsets[0][0])
    assert seen_groups == set(GROUP_KEYS)
    assert seen_lookups == set(range(1, 11))


def test_dye_hard_rewrite_lands_and_leaves_others_untouched():
    body, header = _table()
    intents = []
    for key in DYERS:
        intents.append(_intent(key, "dye_color_group_data_list",
                               [{"dye_target_key": 0, "dye_color_group_key": g}
                                for g in GROUP_KEYS]))
        intents.append(_intent(key, "dye_texture_set_data_list",
                               [{"dye_target_key": 0, "texture_set_lookup": i}
                                for i in range(1, 11)]))
    changes, pabgh = build_npcinfo_changes(body, header, intents)
    assert len(changes) == 10 and pabgh is not None

    buf = bytearray(body)
    for c in sorted(changes, key=lambda c: -c["offset"]):
        o = c["offset"]
        orig = bytes.fromhex(c["original"])
        assert buf[o:o + len(orig)] == orig
        buf[o:o + len(orig)] = bytes.fromhex(c["patched"])
    new_body = bytes(buf)
    new_header = bytes.fromhex(pabgh["patched"])

    _ks, old = _entries(body, header)
    _ks2, new = _entries(new_body, new_header)
    assert set(old) == set(new)
    for key in DYERS:
        off, end, payload, _n = new[key]
        dl = locate_dye_lists(new_body, payload, end, key)
        assert [k for k, t in dl.groups] == GROUP_KEYS
        assert all(t == 0 for _k, t in dl.groups)
        assert [lk for lk, _t in dl.texsets] == list(range(1, 11))
        assert all(t == 0 for _lk, t in dl.texsets)
    for key, (off, end, _p, _n) in old.items():
        if key in DYERS:
            continue
        noff, nend, _np, _nn = new[key]
        assert body[off:end] == new_body[noff:nend], key


def test_set_equal_to_vanilla_is_a_noop():
    body, header = _table()
    _ks, ents = _entries(body, header)
    _off, end, payload, _n = ents[CAMP_DYER]
    dl = locate_dye_lists(body, payload, end, CAMP_DYER)
    intents = [
        _intent(CAMP_DYER, "dye_color_group_data_list",
                [{"dye_color_group_key": k, "dye_target_key": t} for k, t in dl.groups]),
        _intent(CAMP_DYER, "dye_texture_set_data_list",
                [{"texture_set_lookup": lk, "dye_target_key": t} for lk, t in dl.texsets]),
    ]
    assert build_npcinfo_changes(body, header, intents) == ([], None)


def test_entry_without_anchor_blobs_is_refused():
    body, header = _table()
    _ks, ents = _entries(body, header)
    # NHM_Unique_Faust_576_npc (1000001) is a 109-byte minimal record
    # with none of the tagged blobs.
    _off, end, payload, name = ents[1000001]
    assert name == "NHM_Unique_Faust_576_npc"
    with pytest.raises(NpcinfoWriteRefused):
        locate_dye_lists(body, payload, end, 1000001)
    intents = [_intent(1000001, "dye_color_group_data_list",
                       [{"dye_target_key": 0, "dye_color_group_key": GROUP_KEYS[0]}])]
    with pytest.raises(NpcinfoWriteRefused):
        build_npcinfo_changes(body, header, intents)


def test_texture_lookup_above_u16_is_refused():
    body, header = _table()
    intents = [_intent(DYERS[0], "dye_texture_set_data_list",
                       [{"dye_target_key": 0, "texture_set_lookup": 70000}])]
    with pytest.raises(NpcinfoWriteRefused):
        build_npcinfo_changes(body, header, intents)


def test_element_encoding_is_8_and_6_bytes():
    """The Camp Dyer is the proof: 10 x 8 + 10 x 6 + two u32 counts must
    end exactly 4 bytes before the entry end."""
    body, header = _table()
    _ks, ents = _entries(body, header)
    _off, end, payload, _n = ents[CAMP_DYER]
    dl = locate_dye_lists(body, payload, end, CAMP_DYER)
    assert dl.list_end - dl.list_start == 4 + 10 * 8 + 4 + 10 * 6
    # and the first element really is (u32 key, u32 target)
    k, t = struct.unpack_from("<II", body, dl.list_start + 4)
    assert (k, t) == (GROUP_KEYS[0], 1000091)
