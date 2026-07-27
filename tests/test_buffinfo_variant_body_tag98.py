"""buffinfo variant tag 98 body layout, plus the table-wide invariant.

Tag 98 is the variant Ultra Hard Mode (Nexus 2295) edits. Its tail size
was derived in round 3 of ``_VARIANT_TAIL_SIZES``; this file pins the
*body* layout inside that tail, which is what makes
``...data.variant.body.f01`` writable.

The layout is derived rather than guessed:

* it is tag 104's declared shape (u8 selector + u64 value) plus one
  trailing byte, and 1 + 8 + 1 accounts for the whole 10-byte tail;
* the i64 at offset 1 is divisible by 1000 across all 28 vanilla
  tag-98 items and lies on a 50000 grid in [-500000, +500000], while
  offset 0 manages 26/28 with 100x the magnitude and offset 2 manages
  0/28.

Also pinned here: every entry in ``_VARIANT_BODY_FIELDS`` tiles its
tail exactly -- fields are contiguous from 0, non-overlapping, and sum
to ``_VARIANT_TAIL_SIZES[tag]``. That held for all 21 pre-existing tags
before tag 98 was added, so it is a real invariant of the table and not
a rule invented to fit one new row.
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

TAG = 98
LAYOUT = [("f00", "u8", 0, 1), ("f01", "u64", 1, 8), ("f02", "u8", 9, 1)]


def _load(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / (name + ".zlib")).read_bytes())


def _bounds(bodyb: bytes, header: bytes) -> dict[int, tuple[int, int]]:
    _ks, offs = parse_pabgh_index(header, "buffinfo")
    so = sorted(offs.items(), key=lambda kv: kv[1])
    return {k: (o, so[i + 1][1] if i + 1 < len(so) else len(bodyb))
            for i, (k, o) in enumerate(so)}


def _tails(eb: bytes, want_tag: int) -> list[tuple[int, bytes]]:
    """(item_index, tail_bytes) for every item of ``want_tag``."""
    ent = bp.parse_entry(eb)
    if ent is None:
        return []
    end, pos, out = ent.min_level_offset, ent.buff_data_list_offset, []
    for i in range(ent.buff_data_count):
        if pos + bp._ITEM_HEADER_BYTES > end:
            return out
        try:
            hdr = bp.parse_item_header(eb, pos)
        except Exception:  # noqa: BLE001
            return out
        if hdr.absent_flag != 0:
            pos += bp._ITEM_HEADER_BYTES
            continue
        try:
            common = bp.parse_payload_common(eb, hdr.payload_offset)
        except Exception:  # noqa: BLE001
            return out
        size = bp._VARIANT_TAIL_SIZES.get(common.tag)
        if size is None:
            return out
        if common.tag == want_tag:
            out.append(
                (i, bytes(eb[common.end_offset:common.end_offset + size])))
        pos = common.end_offset + size
    return out


def _all_tails(want_tag: int) -> list[tuple[int, int, bytes]]:
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    out = []
    for k, (o, e) in _bounds(body, header).items():
        for i, raw in _tails(bytes(body[o:e]), want_tag):
            out.append((k, i, raw))
    return out


def test_tag98_layout_is_registered():
    name, fields = bp._VARIANT_BODY_FIELDS[TAG]
    assert fields == LAYOUT
    # Placeholder, deliberately: the engine class name isn't known.
    assert name == "UnknownTag98BuffData"


def test_every_declared_body_tiles_its_tail_exactly():
    """Contiguous from 0, non-overlapping, summing to the tail size."""
    for tag, (_name, fields) in sorted(bp._VARIANT_BODY_FIELDS.items()):
        tail = bp._VARIANT_TAIL_SIZES.get(tag)
        assert tail is not None, tag
        cursor = 0
        for _fname, _dtype, off, size in fields:
            assert off == cursor, (tag, _fname, off, cursor)
            cursor += size
        assert cursor == tail, (tag, cursor, tail)


def test_declared_widths_match_their_dtypes():
    widths = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}
    for tag, (_name, fields) in bp._VARIANT_BODY_FIELDS.items():
        for fname, dtype, _off, size in fields:
            assert widths[dtype] == size, (tag, fname, dtype, size)


@_needs
def test_offset_1_is_the_only_credible_value_field():
    """The derivation, re-run. 8-byte reads at every other offset in
    the tail are visibly not a game-designer's number."""
    rows = _all_tails(TAG)
    assert len(rows) == 28, len(rows)
    scores = {}
    for off in range(10 - 8 + 1):
        vs = [struct.unpack_from("<q", raw, off)[0] for _k, _i, raw in rows]
        scores[off] = (sum(1 for v in vs if v % 1000 == 0),
                       max(abs(v) for v in vs))
    assert scores[1][0] == 28                 # every item, no exception
    assert scores[1][1] == 500_000            # and a sane magnitude
    assert scores[0][0] < 28
    assert scores[0][1] > 100 * scores[1][1]  # 128 million, not 500k
    assert scores[2][0] == 0


@_needs
def test_selector_bytes_are_small_enums():
    rows = _all_tails(TAG)
    assert {raw[0] for _k, _i, raw in rows} == {0, 2}
    assert {raw[9] for _k, _i, raw in rows} == {0, 1}


@_needs
def test_ultra_hard_boss_items_now_resolve_and_read_vanilla_values():
    """Items 4 and 5 are the two Ultra Hard could not place."""
    body, header = _load("buffinfo.pabgb"), _load("buffinfo.pabgh")
    lo, hi = _bounds(body, header)[1000277]
    eb = bytes(body[lo:hi])
    seen = {}
    for idx in (4, 5):
        hit = bp.locate_buff_field(
            eb, f"buff_data_list[{idx}].data.variant.body.f01")
        assert hit is not None, idx
        off, width, dtype = hit
        assert (width, dtype) == (8, "u64")
        seen[idx] = struct.unpack_from("<Q", eb, off)[0]
    assert seen == {4: 500_000, 5: 300_000}, seen
