"""knowledgeinfo: locating a field is not the same as it meaning anything.

The "Unlock All Equipment and AbyssGear Recipes" (Nexus 2726) and
"Unlock All Elementals" (Nexus 2664) mods set ``is_default`` on 166
knowledge records. ``_isDefault`` is declared ``direct_15B`` -- a tagged
primitive whose value position the schema does not give -- so the byte
offset was never read off a schema. It was derived statistically.

That makes this table different from every other row in the canary.
Elsewhere, a moved layout makes the production path refuse. Here the
production path would keep succeeding and write the wrong byte, because
its checks are structural and the structure would still hold.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import scripts.post_update_check as puc
from cdumm.engine.knowledgeinfo_writer import (
    IS_DEFAULT_OFFSET,
    _record_bounds,
    locate_is_default,
)

_FIX = Path(__file__).parent / "fixtures" / "vanilla115"
_TABLE = "knowledgeinfo"

pytestmark = pytest.mark.skipif(
    not (_FIX / f"{_TABLE}.pabgb.zlib").exists(),
    reason=f"{_TABLE} fixture not committed")


def _table() -> tuple[bytes, bytes]:
    return (zlib.decompress((_FIX / f"{_TABLE}.pabgb.zlib").read_bytes()),
            zlib.decompress((_FIX / f"{_TABLE}.pabgh.zlib").read_bytes()))


def _named(body: bytes, header: bytes) -> dict[str, tuple[int, int, int]]:
    """name -> (key, lo, hi) for every record."""
    out = {}
    for key, (lo, hi) in _record_bounds(header, body).items():
        name_len = struct.unpack_from("<I", body, lo + 4)[0]
        name = body[lo + 8:lo + 8 + name_len].decode("ascii", "replace")
        out[name] = (key, lo, hi)
    return out


def test_the_committed_table_locates_completely():
    body, header = _table()
    ok, detail = puc.check_knowledgeinfo_is_default(body, header)
    assert ok, detail
    assert "6219/6219 records locate" in detail
    assert "562 default-known" in detail
    assert "[5, 17]" in detail
    assert "6/6 start-known anchors true" in detail


def test_the_row_is_pinned_so_it_gates():
    assert (_FIX.name, _TABLE) in puc._FIXTURE_GREEN
    rows = puc.run_fixture_checks(versions=(_FIX.name,))
    row = [r for r in rows if r[0] == f"{_FIX.name}/{_TABLE}"]
    assert len(row) == 1, [r[0] for r in rows]
    _label, ok, detail, gating = row[0]
    assert ok, detail
    assert gating


def test_the_semantic_anchor_catches_what_the_structure_cannot():
    """The reason this row exists, demonstrated rather than argued.

    Zero the is_default byte on exactly the six start-known knowledges.
    Every structural constant `locate_is_default` checks is untouched --
    the key still echoes the index, the name length is plausible, the
    zero at name_end and the 13 at name_end+6 are both intact, and the
    value is still in {0,1}. So ALL 6,219 records still locate.

    A row gated on "does the field locate" would be green on a table
    where the field no longer means is_default. The anchor is what makes
    it red.
    """
    body, header = _table()
    named = _named(body, header)

    broken = bytearray(body)
    for name in puc._KNOWLEDGE_START_KNOWN:
        key, lo, hi = named[name]
        pos = locate_is_default(body, lo, hi, key)
        assert pos is not None, f"{name} did not locate in vanilla"
        broken[pos] = 0
    broken = bytes(broken)

    # The production locator is entirely happy with the mutated table.
    bounds = _record_bounds(header, broken)
    still = sum(1 for key, (lo, hi) in bounds.items()
                if locate_is_default(broken, lo, hi, key) is not None)
    assert still == len(bounds), (
        "the premise failed: the mutation broke the structural checks, "
        "so this test no longer demonstrates the gap")

    ok, detail = puc.check_knowledgeinfo_is_default(broken, header)
    assert not ok, detail
    assert "0/6 start-known anchors true" in detail
    assert "flip an unrelated byte" in detail


def test_a_shifted_record_head_is_refused_not_guessed():
    """The structural checks still earn their place for the other class
    of break: when the head shape moves, the locator must refuse rather
    than write at a position it cannot prove."""
    body, header = _table()
    named = _named(body, header)
    _key, lo, _hi = next(iter(named.values()))

    broken = bytearray(body)
    name_len = struct.unpack_from("<I", body, lo + 4)[0]
    broken[lo + 8 + name_len] = 0xFF        # the "always 0" framing byte

    ok, detail = puc.check_knowledgeinfo_is_default(bytes(broken), header)
    assert not ok, detail
    assert "1 refused" in detail


def test_the_offset_derivation_still_has_exactly_two_candidates():
    """+5 and +17 are the only boolean, non-constant offsets in the
    window the offset was derived from. If that stops being true the
    derivation needs redoing before the writer can be trusted."""
    body, header = _table()
    heads = []
    for lo, hi in _record_bounds(header, body).values():
        name_len = struct.unpack_from("<I", body, lo + 4)[0]
        heads.append((lo + 8 + name_len, hi))

    found = []
    for off in range(80):
        true_count = 0
        for name_end, hi in heads:
            pos = name_end + off
            if pos >= hi or body[pos] not in (0, 1):
                break
            true_count += body[pos]
        else:
            if true_count:
                found.append(off)

    assert found == [IS_DEFAULT_OFFSET, 17], found


def test_the_life_skill_offset_spans_more_skills_than_the_docstring_says():
    """The writer's docstring says the 51 records at +17 "are
    Knowledge_Skill_Farming/Ranching/Logging/Mining_I..III" -- four
    skills over three tiers, which is twelve, not fifty-one.

    The count and the conclusion (+17 is a life-skill flag, not a
    start-known flag) are both right; only the enumeration reads as
    exhaustive when it is illustrative. Pinned so the real shape is on
    record, the way npcinfo's 452-vs-462 is.
    """
    body, header = _table()
    hits = []
    for name, (_key, lo, hi) in _named(body, header).items():
        name_len = struct.unpack_from("<I", body, lo + 4)[0]
        pos = lo + 8 + name_len + 17
        if pos < hi and body[pos] == 1:
            hits.append(name)

    assert len(hits) == 51
    assert all(n.startswith("Knowledge_Skill_") for n in hits)
    skills = {n.removeprefix("Knowledge_Skill_").rsplit("_", 1)[0]
              for n in hits}
    assert len(skills) == 19, sorted(skills)
    assert {"Banking", "Building", "Crafting"} <= skills, sorted(skills)
