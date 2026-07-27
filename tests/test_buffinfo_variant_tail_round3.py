"""buffinfo variant tail sizes, derivation round 3 (exact tiling).

Rounds 1-2 (see ``_VARIANT_TAIL_SIZES``' comment) only used entries that
were single-item or fully homogeneous, so every mixed-tag entry stayed
unreachable and 11 tags were unknown. One of them, **tag 98**, is item 0
of ``BuffLevel_Difficulty_Boss`` -- which is why Ultra Hard Mode (Nexus
2295) could not place a single one of its intents: the walk died on the
first item and everything after it was unreachable.

Round 3 uses the stronger constraint the table structure already gives:
an entry's list region ``[buff_data_list_offset, min_level_offset)``
must tile **exactly** into ``buff_data_count`` items. Sweeping 0..399
for a tag and requiring EVERY record containing it to tile exactly
yields a unique size or none. Only unique answers were recorded.

Two independent checks that the method is sound, both pinned below:

* it returns **no** candidate for tags 37, 95 and 115 -- precisely the
  tags the pre-existing comment had already identified as variable-tail
  by a different method. Agreement, arrived at independently.
* the values it unlocks read as clean round numbers (150000, 50000) and
  the mod's requested values land on them exactly. A wrong tail size
  desyncs the walk and would read noise.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cdumm._vendor import buffinfo_parser as bp
from cdumm.semantic.parser import parse_pabgh_index

# Loaded directly rather than through fixture_loaders so this file stays
# independent of any other in-flight branch that touches that module.
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla115"

_needs = pytest.mark.skipif(
    not (_FIXTURES / "buffinfo.pabgb.zlib").exists(),
    reason="vanilla115 buffinfo fixture absent")

#: Sizes round 3 added. Kept here so a change to the table has to be a
#: deliberate edit in two places, not a silent drift.
ROUND3 = {4: 13, 20: 88, 26: 5, 28: 1, 63: 17, 71: 8, 72: 1, 78: 12,
          98: 10}

#: Tags the earlier rounds flagged as VARIABLE-tail. Round 3 must not
#: invent a size for any of them.
KNOWN_VARIABLE = {37, 95, 115}


def _load(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / (name + ".zlib")).read_bytes())


def _bounds(body: bytes, header: bytes) -> dict[int, tuple[int, int]]:
    _ks, offs = parse_pabgh_index(header, "buffinfo")
    so = sorted(offs.items(), key=lambda kv: kv[1])
    out = {}
    for i, (k, off) in enumerate(so):
        out[k] = (off, so[i + 1][1] if i + 1 < len(so) else len(body))
    return out


def _walk(eb: bytes, sizes: dict[int, int]) -> list[int] | None:
    """Item start offsets, or None if the list doesn't tile exactly."""
    ent = bp.parse_entry(eb)
    if ent is None:
        return None
    end = ent.min_level_offset
    pos = ent.buff_data_list_offset
    starts = []
    for _ in range(ent.buff_data_count):
        if pos + bp._ITEM_HEADER_BYTES > end:
            return None
        try:
            hdr = bp.parse_item_header(eb, pos)
        except Exception:  # noqa: BLE001
            return None
        starts.append(pos)
        if hdr.absent_flag != 0:
            pos += bp._ITEM_HEADER_BYTES
            continue
        try:
            common = bp.parse_payload_common(eb, hdr.payload_offset)
        except Exception:  # noqa: BLE001
            return None
        sz = sizes.get(common.tag)
        if sz is None:
            return None
        pos = common.end_offset + sz
    return starts if pos == end else None


def _tiles(eb: bytes, sizes: dict[int, int]) -> bool:
    """True iff the list region tiles exactly under ``sizes``."""
    return _walk(eb, sizes) is not None


def test_round3_sizes_are_in_the_table():
    for tag, size in ROUND3.items():
        assert bp._VARIANT_TAIL_SIZES.get(tag) == size, tag


def test_variable_tail_tags_stay_out():
    """Rounds 1-2 proved these have no single size. Never invent one."""
    for tag in KNOWN_VARIABLE:
        assert tag not in bp._VARIANT_TAIL_SIZES, tag


#: Records that isolate each tag under the round-1/2 table alone. Pinned
#: so a shrinking evidence base shows up as a failure, not a quiet pass.
EVIDENCE_RECORDS = {4: 6, 20: 3, 26: 1, 28: 1, 63: 6, 71: 1, 72: 1,
                    78: 1, 98: 4}


@_needs
def test_each_round3_size_is_the_unique_tiling_solution():
    """The derivation, re-run: no other size in 0..399 works.

    Every round-3 tag must be forced by at least one record whose only
    unknown is that tag -- no tag rides in on another's back.
    """
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    bounds = _bounds(body, header)
    base = {t: s for t, s in bp._VARIANT_TAIL_SIZES.items()
            if t not in ROUND3}

    evidence, solutions = {}, {}
    for tag, expected in ROUND3.items():
        # records whose ONLY unknown is this tag, under the base table
        keys = [k for k, (o, e) in bounds.items()
                if not _tiles(bytes(body[o:e]), base)
                and _tiles(bytes(body[o:e]), {**base, tag: expected})]
        evidence[tag] = len(keys)
        solutions[tag] = [
            c for c in range(400)
            if keys and all(
                _tiles(bytes(body[bounds[k][0]:bounds[k][1]]),
                       {**base, tag: c}) for k in keys)]

    assert evidence == EVIDENCE_RECORDS, evidence
    assert solutions == {t: [s] for t, s in ROUND3.items()}, solutions


@_needs
def test_round3_strictly_increases_records_that_tile():
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    bounds = _bounds(body, header)
    base = {t: s for t, s in bp._VARIANT_TAIL_SIZES.items()
            if t not in ROUND3}
    before = sum(1 for k, (o, e) in bounds.items()
                 if _tiles(bytes(body[o:e]), base))
    after = sum(1 for k, (o, e) in bounds.items()
                if _tiles(bytes(body[o:e]), bp._VARIANT_TAIL_SIZES))
    assert (before, after) == (199, 227), (before, after)


@_needs
def test_round3_moves_no_item_that_already_resolved():
    """New sizes are additive: a record that already walked must still
    walk, and every item must land on the SAME offset. Write targeting
    for the 199 pre-existing records is therefore untouched."""
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    bounds = _bounds(body, header)
    base = {t: s for t, s in bp._VARIANT_TAIL_SIZES.items()
            if t not in ROUND3}
    checked = 0
    for o, e in bounds.values():
        eb = bytes(body[o:e])
        was = _walk(eb, base)
        if was is None:
            continue
        assert _walk(eb, bp._VARIANT_TAIL_SIZES) == was
        checked += 1
    assert checked == 199, checked


@_needs
def test_ultra_hard_boss_record_now_walks_its_whole_list():
    """The record that motivated this: item 0 is tag 98."""
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    bounds = _bounds(body, header)
    eb = bytes(body[bounds[1000277][0]:bounds[1000277][1]])
    ent = bp.parse_entry(eb)
    assert ent.name == "BuffLevel_Difficulty_Boss"
    assert _tiles(eb, bp._VARIANT_TAIL_SIZES)
    # every declared item is now addressable
    for i in range(ent.buff_data_count):
        assert bp.locate_buff_field(
            eb, f"buff_data_list[{i}].absent_flag") is not None, i


@_needs
def test_the_unlocked_values_are_the_mod_s_targets():
    """Strongest check that the walk lands right: the three fields Ultra
    Hard sets read clean vanilla values, not noise."""
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    bounds = _bounds(body, header)
    eb = bytes(body[bounds[1000277][0]:bounds[1000277][1]])
    seen = {}
    for idx in (6, 7, 8):
        hit = bp.locate_buff_field(
            eb, f"buff_data_list[{idx}].data.variant.body.f02")
        assert hit is not None, idx
        off, width, _dtype = hit
        assert width == 8
        seen[idx] = struct.unpack_from("<Q", eb, off)[0]
    assert seen == {6: 150000, 7: 50000, 8: 50000}, seen
