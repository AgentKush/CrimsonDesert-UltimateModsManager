"""The user-facing consequence of the fixed-offset bug, as a test.

Reading the wrong u32 was not an abstract decoding defect. For 82 of the
CD 1.13 table's entries the count was somewhere other than payload+44,
so the parser read a different u32, found 0, and reported "this store is
empty" -- a *successful* parse, no exception, no warning.

Two things follow, and both are asserted here against the real tables:

  * a mod editing one of those stores saw an empty list, so its edit
    silently did nothing; and
  * had the writer acted on that, it would have spliced a new list in at
    payload+44 -- in the middle of live fields, on a table whose only
    integrity check is the game crashing on store open.

Store 3111 (33 records at payload+92) stands in for the other 81.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cdumm.engine.storeinfo_native_parser import (
    StoreinfoParseError,
    detect_storeinfo_layout,
    locate_stock_list,
    parse_stock_list,
    serialize_stock_list,
)
from cdumm.engine.storeinfo_writer import build_storeinfo_changes
from cdumm.semantic.parser import parse_pabgh_index

_F113 = Path(__file__).resolve().parent / "fixtures" / "vanilla113"
_F116 = Path(__file__).resolve().parent / "fixtures" / "vanilla116"

# A store whose list is NOT at payload+44. 33 records, count at +92.
MISSED_STORE = 3111
MISSED_RECORDS = 33
OLD_CONSTANT = 44


@dataclass
class _Intent:
    entry: str
    key: int
    field: str
    op: str = "set"
    new: Any = None
    old: Any = None


def _load(d: Path):
    body = zlib.decompress((d / "storeinfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((d / "storeinfo.pabgh.zlib").read_bytes())
    _ks, offsets = parse_pabgh_index(header, "storeinfo")
    return body, header, offsets


def _payload(body: bytes, off: int) -> int:
    return off + 6 + struct.unpack_from("<I", body, off + 2)[0] + 1


@pytest.fixture(scope="module")
def t113():
    return _load(_F113)


def test_the_old_constant_reported_this_stocked_store_as_empty(t113):
    """The bug itself. Not an exception — a clean, confident, wrong 0."""
    body, _header, offsets = t113
    layout = detect_storeinfo_layout(body, sorted(offsets.values()))
    payload = _payload(body, offsets[MISSED_STORE])

    recs, _s, _e = parse_stock_list(body, payload + OLD_CONSTANT, layout)
    assert recs == [], "expected the old offset to read an empty list"


def test_the_store_actually_has_stock_and_it_round_trips(t113):
    body, _header, offsets = t113
    layout = detect_storeinfo_layout(body, sorted(offsets.values()))
    spans = sorted(offsets.values()) + [len(body)]
    off = offsets[MISSED_STORE]
    recs, start, end = locate_stock_list(
        body, _payload(body, off), spans[spans.index(off) + 1],
        MISSED_STORE, layout)

    assert len(recs) == MISSED_RECORDS
    assert serialize_stock_list(recs, layout) == body[start:end]
    # located past the old constant, which is what made it invisible
    assert start - _payload(body, off) == 92


def test_an_edit_to_that_store_now_reaches_the_real_records(t113):
    """End to end: a Format 3 intent that changes one record's price.

    Before, per the first test, the writer would have been handed an
    empty vanilla list, so every one of the mod's records would have
    looked new. Now they match, and exactly the edited bytes change.
    """
    body, header, offsets = t113
    layout = detect_storeinfo_layout(body, sorted(offsets.values()))
    spans = sorted(offsets.values()) + [len(body)]
    off = offsets[MISSED_STORE]
    recs, start, end = locate_stock_list(
        body, _payload(body, off), spans[spans.index(off) + 1],
        MISSED_STORE, layout)

    # An intent that re-states every record as-is: a faithful writer must
    # match them all against vanilla and emit nothing.
    same = [{"value": {"payload": {"body": r.body}}} for r in recs]
    changes, _pabgh = build_storeinfo_changes(
        body, header,
        [_Intent(entry="", key=MISSED_STORE, field="stock_data_list",
                 new=same)])
    assert changes == [], (
        "restating vanilla produced changes, so records are not being "
        "matched against the real list")

    # Now drop the last record. The rewritten span must be the located
    # one, shorter by exactly one record, and identical elsewhere.
    changes, _pabgh = build_storeinfo_changes(
        body, header,
        [_Intent(entry="", key=MISSED_STORE, field="stock_data_list",
                 new=same[:-1])])
    assert len(changes) == 1
    ch = changes[0]
    assert ch["offset"] == start
    assert bytes.fromhex(ch["original"]) == body[start:end]
    rebuilt = bytes.fromhex(ch["patched"])
    assert rebuilt == serialize_stock_list(recs[:-1], layout)
    # exactly one record shorter -- that record's own size, not an
    # average: sub_data is optional, so records are 119 or 132 bytes
    dropped = serialize_stock_list(recs[-1:], layout)
    assert len(body[start:end]) - len(rebuilt) == len(dropped) - 4


def test_a_store_with_no_stock_is_refused_not_guessed(t113):
    """The other half. An empty store's list cannot be located — there is
    no first record to anchor on — so adding stock to one is refused with
    a reason, rather than written to a guessed offset."""
    body, header, offsets = t113
    layout = detect_storeinfo_layout(body, sorted(offsets.values()))
    spans = sorted(offsets.values()) + [len(body)]
    empty = [k for k, o in offsets.items()
             if _is_empty(body, o, spans, k, layout)]
    assert empty, "expected the table to contain provably-empty stores"

    from cdumm.engine.storeinfo_writer import StoreinfoWriteRefused
    with pytest.raises(StoreinfoWriteRefused, match="no stock list"):
        build_storeinfo_changes(
            body, header,
            [_Intent(entry="", key=empty[0], field="stock_data_list",
                     new=[{"value": {"payload": {"body": 1001}}}])])


def _is_empty(body, off, spans, key, layout) -> bool:
    try:
        locate_stock_list(body, _payload(body, off),
                          spans[spans.index(off) + 1], key, layout)
    except StoreinfoParseError:
        return True
    return False


@pytest.mark.skipif(not (_F116 / "storeinfo.pabgb.zlib").exists(),
                    reason="1.16 fixture absent")
def test_the_same_store_is_reachable_on_cd_1_16():
    """The fix is not 1.13-specific: the anchor locates the list on the
    1.16 table too, where the record shape is different again."""
    body, _header, offsets = _load(_F116)
    layout = detect_storeinfo_layout(body, sorted(offsets.values()))
    assert layout.label == "CD 1.16"
    spans = sorted(offsets.values()) + [len(body)]
    off = offsets[MISSED_STORE]
    recs, start, end = locate_stock_list(
        body, _payload(body, off), spans[spans.index(off) + 1],
        MISSED_STORE, layout)
    assert recs and recs[0].lookup_a == MISSED_STORE
    assert serialize_stock_list(recs, layout) == body[start:end]
