"""knowledgeinfo ``is_default``: the byte, and the case for that byte.

The three "Unlock All ..." mods (Nexus 2726 and 2664) are 166 intents
that between them applied zero bytes, because ``KnowledgeInfo._isDefault``
is declared ``direct_15B`` -- a Pearl Abyss tagged primitive -- and the
schema says how wide the field is but not where inside it the value sits.

These tests re-run the derivation in CI against the committed vanilla
1.15 table rather than trusting the offset:

* exactly two offsets in ``name_end+0..79`` are boolean-valued across all
  6219 records AND zero on all 166 keys the mods target;
* of those two, ``+17`` is set only on the 51 ``Knowledge_Skill_*`` life
  skill tiers, while ``+5`` is set on the base stat knowledges a
  character must start with -- which is what ``is_default`` means;
* the writer turns all 166 intents into 166 one-byte changes and refuses
  anything it can't place.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cdumm.engine.knowledgeinfo_writer import (
    IS_DEFAULT_OFFSET,
    build_knowledgeinfo_changes,
    locate_is_default,
)
from cdumm.semantic.parser import parse_pabgh_index

# Loaded directly rather than through fixture_loaders so this file stays
# independent of any other in-flight branch that touches that module.
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla115"

_needs = pytest.mark.skipif(
    not (_FIXTURES / "knowledgeinfo.pabgb.zlib").exists(),
    reason="vanilla115 knowledgeinfo fixture absent")

#: The keys the three mods set is_default=1 on. Game data, not mod
#: authorship: these are the knowledge record IDs for equipment recipes,
#: abyss gear recipes and the four elementals.
MOD_TARGET_KEYS = (
    1000092, 1000140, 1000141, 1000142, 1000143, 1000144, 1000145,
    1000146, 1000222, 1000232, 1000234, 1000247, 1000318, 1000376,
    1000377, 1000393, 1000512, 1000541, 1000549, 1000554, 1000591,
    1000661, 1000662, 1000668, 1000669, 1000670, 1000677, 1000679,
    1000724, 1000725, 1000726, 1000727, 1000728, 1000729, 1000730,
    1000766, 1000802, 1000803, 1000804, 1000873, 1000874, 1000875,
    1000876, 1000877, 1000891, 1001152, 1001153, 1001180, 1001181,
    1001182, 1001187, 1001188, 1001189, 1001190, 1001191, 1001192,
    1001265, 1001282, 1001283, 1001284, 1001285, 1001286, 1001323,
    1001324, 1001325, 1001326, 1001327, 1001328, 1001470, 1001471,
    1001843, 1002251, 1002326, 1002327, 1002430, 1002431, 1002432,
    1002737, 1002827, 1002828, 1002993, 1002994, 1002995, 1002996,
    1002997, 1002998, 1002999, 1003000, 1003001, 1003023, 1003036,
    1003038, 1003076, 1003078, 1003089, 1003183, 1003204, 1003221,
    1003225, 1003227, 1003231, 1003232, 1003233, 1003234, 1003235,
    1003237, 1003238, 1003239, 1003242, 1003297, 1003310, 1003342,
    1003347, 1003348, 1003349, 1003350, 1003351, 1003352, 1003353,
    1003354, 1003355, 1003356, 1003486, 1003539, 1003540, 1003541,
    1003543, 1003544, 1003545, 1003547, 1003550, 1003552, 1003840,
    1003842, 1003843, 1003844, 1003845, 1003846, 1003847, 1003854,
    1003856, 1003861, 1003862, 1003863, 1003864, 1003865, 1003866,
    1003867, 1003868, 1003869, 1003870, 1003871, 1003872, 1003874,
    1003875, 1003876, 1003877, 1003878, 1003879, 1003880, 1003881,
    1003882, 1003883, 1003884, 1003885, 1004780,
)


class _Intent:
    def __init__(self, key, field="is_default", new=1, op="set", entry=""):
        self.key, self.field, self.new = key, field, new
        self.op, self.entry = op, entry


def _load(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / (name + ".zlib")).read_bytes())


def _table():
    return _load("knowledgeinfo.pabgb"), _load("knowledgeinfo.pabgh")


def _records(body: bytes, header: bytes):
    """{key: (record_bytes, name_end_within_record, name)}."""
    _keys, offs = parse_pabgh_index(header, "knowledgeinfo")
    ordered = sorted(offs.items(), key=lambda kv: kv[1])
    out = {}
    for i, (k, o) in enumerate(ordered):
        hi = ordered[i + 1][1] if i + 1 < len(ordered) else len(body)
        eb = bytes(body[o:hi])
        n = struct.unpack_from("<I", eb, 4)[0]
        out[k] = (eb, 8 + n, eb[8:8 + n].decode("utf-8", "replace"))
    return out


def test_writer_offset_matches_the_derivation():
    assert IS_DEFAULT_OFFSET == 5


@_needs
def test_every_record_parses_with_the_documented_head():
    body, header = _table()
    recs = _records(body, header)
    assert len(recs) == 6219
    for k, (eb, end, name) in recs.items():
        assert struct.unpack_from("<I", eb, 0)[0] == k
        # Most are Knowledge_*, but not all (e.g. 'Valley_of_Vultures'),
        # so the name is only checked for being a plausible identifier.
        assert name and name.isascii(), name
        assert eb[end] == 0             # pad
        assert eb[end + 6] == 13        # framing constant (byte, not u32)


@_needs
def test_exactly_two_offsets_survive_the_boolean_and_target_filter():
    """The derivation, re-run: an offset qualifies only if it is boolean
    across all 6219 records, zero on all 166 mod targets, and used."""
    body, header = _table()
    recs = _records(body, header)
    qualifying = []
    for d in range(80):
        vals = []
        for eb, end, _n in recs.values():
            if end + d >= len(eb):
                vals = None
                break
            vals.append(eb[end + d])
        if vals is None or not set(vals) <= {0, 1} or 1 not in vals:
            continue
        if {recs[k][0][recs[k][1] + d] for k in MOD_TARGET_KEYS} != {0}:
            continue
        qualifying.append(d)
    assert qualifying == [5, 17], qualifying


@_needs
def test_the_rival_offset_is_a_life_skill_flag_not_is_default():
    """+17 is set on 51 records and every one is a life-skill tier."""
    body, header = _table()
    recs = _records(body, header)
    ones = [n for eb, end, n in recs.values() if eb[end + 17] == 1]
    assert len(ones) == 51
    assert all(n.startswith("Knowledge_Skill_") for n in ones), ones[:5]


@_needs
def test_is_default_is_set_on_the_knowledges_a_character_starts_with():
    """The positive case for +5: base stat knowledges are default-known.
    Reading it as _isBlocked instead would mean a character whose Hp and
    CriticalRate knowledges are blocked."""
    body, header = _table()
    recs = _records(body, header)
    ones = {n for eb, end, n in recs.values()
            if eb[end + IS_DEFAULT_OFFSET] == 1}
    assert len(ones) == 562
    for expected in ("Knowledge_Hp", "Knowledge_CriticalRate",
                     "Knowledge_AttackSpeedRate", "Knowledge_MoveSpeedRate",
                     "Knowledge_Fatal", "Knowledge_KnockOut"):
        assert expected in ones, expected


@_needs
def test_all_166_mod_targets_are_locked_in_vanilla():
    body, header = _table()
    recs = _records(body, header)
    for k in MOD_TARGET_KEYS:
        eb, end, _n = recs[k]
        assert eb[end + IS_DEFAULT_OFFSET] == 0, k


@_needs
def test_writer_applies_every_mod_target_as_one_byte():
    body, header = _table()
    intents = [_Intent(k) for k in MOD_TARGET_KEYS]
    changes, dropped = build_knowledgeinfo_changes(body, header, intents)
    assert dropped == []
    assert len(changes) == len(MOD_TARGET_KEYS)
    patched = bytearray(body)
    for c in changes:
        assert c["original"] == "00" and c["patched"] == "01"
        patched[c["offset"]:c["offset"] + 1] = bytes.fromhex(c["patched"])
    assert len(patched) == len(body)
    assert sum(1 for a, b in zip(body, patched) if a != b) == 166


@_needs
def test_writer_no_ops_when_already_default():
    body, header = _table()
    recs = _records(body, header)
    already = next(k for k, (eb, end, _n) in recs.items()
                   if eb[end + IS_DEFAULT_OFFSET] == 1)
    changes, dropped = build_knowledgeinfo_changes(
        body, header, [_Intent(already)])
    assert (changes, dropped) == ([], [])


@_needs
@pytest.mark.parametrize("intent,fragment", [
    (_Intent(1000802, field="is_blocked"), "is not knowledgeinfo's"),
    (_Intent(1000802, op="scale"), "only 'set'"),
    (_Intent(1000802, new=7), "not a boolean"),
    (_Intent(99999999), "no knowledgeinfo record"),
    (_Intent(1000802, entry="Knowledge_Wrong"), "but the intent names"),
])
def test_writer_refuses_rather_than_guesses(intent, fragment):
    body, header = _table()
    changes, dropped = build_knowledgeinfo_changes(body, header, [intent])
    assert changes == []
    assert len(dropped) == 1 and fragment in dropped[0][1]


@_needs
def test_locate_refuses_a_record_that_lost_its_framing():
    """If the two structural constants aren't there, the record isn't the
    shape the offset was derived from -- don't write into it."""
    body, header = _table()
    _keys, offs = parse_pabgh_index(header, "knowledgeinfo")
    lo = offs[1000802]
    ordered = sorted(offs.values())
    hi = ordered[ordered.index(lo) + 1]
    assert locate_is_default(body, lo, hi, 1000802) is not None

    broken = bytearray(body)
    name_len = struct.unpack_from("<I", broken, lo + 4)[0]
    broken[lo + 8 + name_len + 6] = 14          # framing byte, not 13
    assert locate_is_default(bytes(broken), lo, hi, 1000802) is None
