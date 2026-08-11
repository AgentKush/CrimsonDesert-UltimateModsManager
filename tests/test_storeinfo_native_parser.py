"""storeinfo stock-list parser (GitHub #183 groundwork).

The trust anchor mirrors the iteminfo native parser: parse +
serialize on the live extracted storeinfo.pabgb must produce
byte-identical output, or applying a stock_data_list intent would
corrupt the file (the game crashes on store open with a corrupt
storeinfo body). The live-fixture tests skip when the extracted
vanilla file is not present; the synthetic tests always run.

Layout pinning (GitHub #351)
----------------------------
The fixed-offset tests below MUST pass the layout they were written
against. ``parse_stock_list``'s layout parameter defaults to
``DEFAULT_LAYOUT``, which is deliberately the *newest* build -- so a
test that omits it silently re-points at whatever layout ships next.
That is what #351 was: this file's ``issue_repro`` snapshot is from the
CD 1.11 era, the default had moved on to CD 1.16, and a 1.11 table was
being parsed as 1.16. CD 1.11 puts the const byte at record offset 34
and CD 1.16 puts it at 42, which was exactly the byte in the error:

    const byte at record offset 42 is 0 (expected 1)

It read as a live regression -- "the format drifted, store mods are
dead" -- and it was neither. Nothing about that snapshot changes when
the game updates.

Two things follow, and both are done here. The fixed-offset tests name
their layout explicitly. And the coverage that actually matters no
longer depends on an untracked snapshot at all: it runs against the
committed ``vanilla113`` / ``vanilla116`` fixtures through
``locate_stock_list``, which is the path production uses and which
takes no offset constant. That is audit finding C7 again (see
``tests/fixture_loaders``) -- a proof gated on a machine-local path is
a proof that runs nowhere, least of all in CI.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from cdumm.engine.storeinfo_native_parser import (
    LAYOUTS,
    ORDER_ELEM_SIZE,
    StockRecord,
    StoreinfoParseError,
    StoreListNotFound,
    locate_stock_list,
    parse_stock_list,
    serialize_stock_list,
)
from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index
from tests.fixture_loaders import (
    has_vanilla113,
    has_vanilla116,
    load_vanilla113,
    load_vanilla116,
)

_VANILLA_DIR = Path(__file__).resolve().parents[1] / "issue_repro" / "183" / "vanilla"
_LIVE_BODY = _VANILLA_DIR / "storeinfo.pabgb"
_LIVE_HEADER = _VANILLA_DIR / "storeinfo.pabgh"

_BY_LABEL = {ly.label: ly for ly in LAYOUTS}

#: The build the ``issue_repro/183`` snapshot was extracted from. Pinned
#: rather than defaulted -- see the module docstring.
_FIXTURE_LAYOUT = _BY_LABEL["CD 1.11"]


def _have_live_fixture() -> bool:
    return _LIVE_BODY.exists() and _LIVE_HEADER.exists()


# ── Synthetic round-trip (always runs) ───────────────────────────────


def _sample_records() -> list[StockRecord]:
    return [
        StockRecord(lookup_a=3101, raw_a=1_000_000, raw_b=1_000_000,
                    raw_c=1, body=6001,
                    sub_data={"flag": 0, "lookup_a": 4294967061,
                              "lookup_b": 0, "lookup_c": 0}),
        StockRecord(lookup_a=3101, raw_a=1_000_000, raw_b=1_000_000,
                    raw_d=1, raw_e=1, flag_a=1, body=1_003_172,
                    sub_data=None),
    ]


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda ly: ly.label)
def test_synthetic_round_trip(layout):
    """Round-trips in EVERY layout, not just whichever one is the default.

    This used to hardcode head == 110 (CD 1.11). When CD 1.13 moved the
    record shape, that hardcoding is precisely what made the test agree
    with a parser that could no longer read the game — so it is now driven
    off the layout under test.
    """
    recs = _sample_records()
    blob = serialize_stock_list(recs, layout)
    # count + rec0 (head + sub_data flag + 13 + effect u32)
    #       + rec1 (head + sub_data flag +  0 + effect u32)
    head = layout.head_size
    assert len(blob) == 4 + (head + 1 + 13 + 4) + (head + 1 + 4)
    parsed, start, end = parse_stock_list(blob, 0, layout)
    assert (start, end) == (0, len(blob))
    assert serialize_stock_list(parsed, layout) == blob
    assert parsed[0].sub_data == recs[0].sub_data
    assert parsed[1].sub_data is None
    assert [r.body for r in parsed] == [6001, 1_003_172]


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda ly: ly.label)
def test_refuses_unknown_sub_data_flag(layout):
    blob = bytearray(serialize_stock_list([_sample_records()[1]], layout))
    # Corrupt the sub_data optional flag (count u32 + the record head).
    blob[4 + layout.head_size] = 7
    with pytest.raises(StoreinfoParseError, match="optional flag is 7"):
        parse_stock_list(bytes(blob), 0, layout)


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda ly: ly.label)
def test_non_empty_effect_list_round_trips(layout):
    """``_orderCountDataList`` used to be refused outright, which cost 15
    entries of the 1.13 table. Its element is ORDER_ELEM_SIZE bytes
    (derived by exact tiling) and is carried verbatim."""
    rec = _sample_records()[1]
    rec.effect_list = [bytes(range(ORDER_ELEM_SIZE)),
                       b"\xff" * ORDER_ELEM_SIZE]
    blob = serialize_stock_list([rec], layout)
    assert len(blob) == 4 + layout.head_size + 1 + 4 + 2 * ORDER_ELEM_SIZE
    parsed, _s, _e = parse_stock_list(blob, 0, layout)
    assert parsed[0].effect_list == rec.effect_list
    assert serialize_stock_list(parsed, layout) == blob


def test_refuses_a_wrongly_sized_effect_element_on_serialize():
    """The elements are opaque, so the ONE thing we can still check is
    that they are the right width — a short one would silently shift
    every following record."""
    rec = _sample_records()[1]
    rec.effect_list = [b"\x00" * (ORDER_ELEM_SIZE - 1)]
    with pytest.raises(StoreinfoParseError, match="opaque bytes"):
        serialize_stock_list([rec])


def test_refuses_an_implausible_effect_count_on_parse():
    """A huge count means we are misaligned, not that the record has
    four billion order entries."""
    blob = bytearray(serialize_stock_list([_sample_records()[1]]))
    struct.pack_into("<I", blob, len(blob) - 4, 0xFFFFFF)
    with pytest.raises(StoreinfoParseError, match="implausible"):
        parse_stock_list(bytes(blob), 0)


# ── Live-fixture round-trip (the trust anchor) ───────────────────────


def _entry_payload_offsets():
    from cdumm.semantic.parser import parse_pabgh_index, _parse_entry_header
    body = _LIVE_BODY.read_bytes()
    key_size, offsets = parse_pabgh_index(
        _LIVE_HEADER.read_bytes(), "storeinfo")
    spans = sorted(offsets.values()) + [len(body)]
    out = {}
    for key, off in offsets.items():
        _, _, payload = _parse_entry_header(body, off, key_size)
        end = spans[spans.index(off) + 1]
        out[key] = (payload, end)
    return body, out


@pytest.mark.skipif(not _have_live_fixture(),
                    reason="extracted vanilla storeinfo fixture not present")
def test_live_entry_3101_round_trips_byte_exact():
    """Entry 3101 is the #183 mod's target. On the CD 1.11 build this
    snapshot came from it has 38 records (one more than the pre-patch
    37); they must survive parse + serialize byte-identically. Also pins
    the 1.11 layout: const33==1 and is_restore_item in {0,1} for every
    record.

    ``_FIXTURE_LAYOUT`` is passed explicitly. Omitting it is #351: the
    default is the newest layout, not this snapshot's.
    """
    body, entries = _entry_payload_offsets()
    payload, _end = entries[3101]
    count_off = payload + _FIXTURE_LAYOUT.count_payload_offset
    records, start, end = parse_stock_list(body, count_off, _FIXTURE_LAYOUT)
    assert len(records) == 38
    assert serialize_stock_list(records, _FIXTURE_LAYOUT) == body[start:end]
    assert all(r.const33 == 1 for r in records)
    assert all(r.is_restore_item in (0, 1) for r in records)


@pytest.mark.skipif(not _have_live_fixture(),
                    reason="extracted vanilla storeinfo fixture not present")
def test_live_full_file_clean_entries_round_trip():
    """Every entry the parser accepts must round-trip byte-exact.
    Entries it cannot handle yet (disc-variant value payloads or
    non-empty effect lists) must raise — never mis-parse silently.
    On the CD 1.11 build this snapshot came from, 268 of 293 entries are
    clean."""
    body, entries = _entry_payload_offsets()
    ok = failed = refused = 0
    for key, (payload, end) in entries.items():
        count_off = payload + _FIXTURE_LAYOUT.count_payload_offset
        if count_off + 4 > end:
            refused += 1
            continue
        try:
            records, start, lend = parse_stock_list(
                body, count_off, _FIXTURE_LAYOUT)
        except (StoreinfoParseError, struct.error, IndexError):
            refused += 1
            continue
        if serialize_stock_list(records, _FIXTURE_LAYOUT) == body[start:lend]:
            ok += 1
        else:
            failed += 1
    assert failed == 0, f"{failed} entries mis-round-tripped"
    assert ok >= 260, f"only {ok} entries round-tripped (expected >=260)"


# ── Committed-fixture coverage: runs everywhere, including CI ─────────
#
# The two tests above are gated on an untracked snapshot, so on a fresh
# clone and on the CI runner they skip. Everything below reads fixtures
# that are in the repo, and goes through the production locate path, so
# a real storeinfo regression has something that actually fails.

_COMMITTED = (
    ("vanilla113", "CD 1.13", 293, 263, 5443, 30),
    ("vanilla116", "CD 1.16", 432, 397, 6376, 35),
)


def _committed_entries(load, ver: str):
    body = load("storeinfo.pabgb")
    header = load("storeinfo.pabgh")
    key_size, offsets = parse_pabgh_index(header, "storeinfo")
    spans = sorted(offsets.values()) + [len(body)]
    out = {}
    for key, off in offsets.items():
        _, _, payload = _parse_entry_header(body, off, key_size)
        out[key] = (payload, spans[spans.index(off) + 1])
    return body, out


def _walk_committed(ver: str, layout):
    load = load_vanilla113 if ver == "vanilla113" else load_vanilla116
    body, entries = _committed_entries(load, ver)
    located = records = empty = not_found = 0
    for key, (payload, end) in entries.items():
        try:
            recs, start, lend = locate_stock_list(
                body, payload, end, key, layout)
        except StoreListNotFound as exc:
            # locate_stock_list distinguishes "provably empty" from
            # "could not read", and that distinction is the point.
            if "too" in str(exc) or "provably" in str(exc):
                empty += 1
            else:
                not_found += 1
            continue
        located += 1
        records += len(recs)
        assert serialize_stock_list(recs, layout) == body[start:lend], (
            f"{ver} store {key} did not round-trip byte-exact")
    return located, records, empty, not_found, len(entries)


@pytest.mark.parametrize(
    "ver,label,n_entries,exp_located,exp_records,exp_empty",
    _COMMITTED, ids=[c[0] for c in _COMMITTED])
def test_committed_fixture_locates_and_round_trips(
        ver, label, n_entries, exp_located, exp_records, exp_empty):
    """The whole table is accounted for, and every record round-trips.

    ``located + provably-empty == entries`` with ``not_found == 0`` is
    the assertion that would have contradicted #351's "store mods are
    dead" reading immediately: the parser reads the current table
    completely. Counts are pinned so a real drift moves them.
    """
    if ver == "vanilla113" and not has_vanilla113("storeinfo.pabgb"):
        pytest.skip("vanilla113 storeinfo fixture absent")
    if ver == "vanilla116" and not has_vanilla116("storeinfo.pabgb"):
        pytest.skip("vanilla116 storeinfo fixture absent")

    located, records, empty, not_found, total = _walk_committed(
        ver, _BY_LABEL[label])

    assert not_found == 0, f"{not_found} entries could not be located"
    assert located == exp_located
    assert records == exp_records
    assert empty == exp_empty
    assert located + empty == total == n_entries


@pytest.mark.parametrize(
    "ver,label,n_entries,exp_located,exp_records,exp_empty",
    _COMMITTED, ids=[c[0] for c in _COMMITTED])
def test_older_layouts_lose_decisively_on_committed_fixture(
        ver, label, n_entries, exp_located, exp_records, exp_empty):
    """Detection is not a close call, so a wrong layout cannot win.

    Every layout other than the fixture's own must locate dramatically
    fewer entries. If a future layout ever ties, ``_score_layout`` can no
    longer tell them apart and the tie must be resolved rather than
    silently broken by ordering — which is precisely how the #352 no-op
    change looked like an improvement.
    """
    if ver == "vanilla113" and not has_vanilla113("storeinfo.pabgb"):
        pytest.skip("vanilla113 storeinfo fixture absent")
    if ver == "vanilla116" and not has_vanilla116("storeinfo.pabgb"):
        pytest.skip("vanilla116 storeinfo fixture absent")

    right = _walk_committed(ver, _BY_LABEL[label])[0]
    for other in LAYOUTS:
        if other.label == label:
            continue
        got = _walk_committed(ver, other)[0]
        assert got < right / 2, (
            f"{ver}: layout {other.label!r} located {got} entries against "
            f"{label!r}'s {right} — detection is no longer decisive")


def test_parsing_an_older_shape_with_the_default_layout_refuses():
    """The #351 bug class, pinned so it cannot come back silently.

    A CD 1.11-shaped list parsed under the newest layout must RAISE, not
    return plausible-looking records. This is what makes omitting the
    layout argument a loud failure rather than a wrong answer — and it
    needs no fixture, so it runs everywhere.
    """
    old = _BY_LABEL["CD 1.11"]
    blob = serialize_stock_list(_sample_records(), old)

    # Sanity: it round-trips under its own layout.
    recs, _s, _e = parse_stock_list(blob, 0, old)
    assert len(recs) == 2

    # Under the newest layout the const tripwire is at a different record
    # offset, so the walk must be refused.
    newest = LAYOUTS[0]
    assert newest.const_off != old.const_off, (
        "this test needs two layouts whose const byte differs")
    with pytest.raises((StoreinfoParseError, struct.error, IndexError)):
        parse_stock_list(blob, 0, newest)
