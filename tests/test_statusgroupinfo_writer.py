"""statusgroupinfo ``status_info_list`` writer.

Nexus mod 2634 "Critical Rate Enhancement" (norva2) retargets one slot of
the item-activation stat groups::

    {key: 1000006, field: "status_info_list[3]", op: "set", new: 1000006}

The values in these lists are ``statusinfo`` record keys, not numbers.

The record grammar is::

    <u32 key><u32 name_len><name><u8 is_blocked>
    <u32 c><c * u32>   x3      three key lists
    <u32 75><75 * u32> x2      two reverse index tables

and ``test_the_grammar_tiles_every_record_exactly`` is the pin for it: the
walk must consume every record to the byte on all eight, which a wrong
field order or a wrong element width cannot do.

The riskier question is which of the three lists ``status_info_list`` names.
The shipped schema has three list-typed fields but its declaration order is
not wire order for this table, so the writer does not rely on the naming
alone -- it refuses whenever another list in the same record is also long
enough to hold the index, which is exactly the case where a mis-naming
could do damage.
"""
from __future__ import annotations

import struct

import pytest

from cdumm.engine.format3_handler import Format3Intent, validate_intents
from cdumm.engine.statusgroupinfo_writer import (
    build_statusgroupinfo_changes,
    parse_record,
)
from cdumm.semantic.parser import parse_pabgh_index
from tests.fixture_loaders import has_vanilla115, load_vanilla115

FIXTURE = "statusgroupinfo.pabgb"
STAT_ON_ITEM = 1000006          # StatOnActivateByItem
STAT_ON_ITEM_NO_ASR = 1000007   # ...WithoutAttackSpeedRate
NUMERIC_GROUP = 1000004         # "103" -- all three lists populated
CRITICAL_DAMAGE = 1000006       # a statusinfo record key
TABLE_LEN = 75                  # one entry per statusinfo record

pytestmark = pytest.mark.skipif(
    not has_vanilla115(FIXTURE),
    reason="1.15 statusgroupinfo fixture not present")


def _tables():
    return (load_vanilla115("statusgroupinfo.pabgb"),
            load_vanilla115("statusgroupinfo.pabgh"))


def _bounds(body: bytes, header: bytes, key: int) -> tuple[int, int]:
    _, offsets = parse_pabgh_index(header, "statusgroupinfo")
    starts = sorted(offsets.values())
    o = offsets[key]
    i = starts.index(o)
    return o, (starts[i + 1] if i + 1 < len(starts) else len(body))


def _lists(body: bytes, header: bytes, key: int):
    o, end = _bounds(body, header, key)
    return parse_record(body, o, end)


def _elem(body: bytes, at: int) -> int:
    return struct.unpack_from("<I", body, at)[0]


def _intent(key: int, idx: int, new: object,
            entry: str = "StatOnActivateByItem") -> Format3Intent:
    return Format3Intent(entry=entry, key=key,
                         field=f"status_info_list[{idx}]", op="set", new=new)


def _apply(body: bytes, changes: list[dict]) -> bytes:
    out = bytearray(body)
    for c in changes:
        off = c["offset"]
        orig = bytes.fromhex(c["original"])
        assert out[off:off + len(orig)] == orig, "change 'original' must anchor"
        out[off:off + len(orig)] = bytes.fromhex(c["patched"])
    return bytes(out)


# ------------------------------------------------------------- the layout

def test_the_grammar_tiles_every_record_exactly():
    """The structural pin. parse_record only returns lists when the walk
    lands exactly on the record end, so this passing on all eight records
    means the field order and every element width are right."""
    body, header = _tables()
    _, offsets = parse_pabgh_index(header, "statusgroupinfo")
    assert len(offsets) == 8
    for key in offsets:
        assert _lists(body, header, key) is not None, key


def test_the_two_index_tables_pair_with_the_lists():
    """Each 75-entry table holds exactly one set entry per element of the
    list it serves -- table 0 for list 1, table 1 for list 0 -- on every
    record. That correspondence is what identifies the tables, and it also
    cross-checks the list lengths the writer indexes into."""
    body, header = _tables()
    _, offsets = parse_pabgh_index(header, "statusgroupinfo")
    for key in offsets:
        o, end = _bounds(body, header, key)
        lists = parse_record(body, o, end)
        assert lists is not None
        # walk past the three lists to the two tables
        p = lists[-1][0] + 4 * lists[-1][1]
        tables = []
        for _ in range(2):
            count = struct.unpack_from("<I", body, p)[0]
            assert count == TABLE_LEN, (key, count)
            tables.append([_elem(body, p + 4 + 4 * j) for j in range(count)])
            p += 4 + 4 * count
        assert p == end
        set0 = [v for v in tables[0] if v != 0xFFFFFFFF]
        set1 = [v for v in tables[1] if v != 0xFFFFFFFF]
        assert len(set0) == lists[1][1], f"table0 vs list1 on {key}"
        assert len(set1) == lists[0][1], f"table1 vs list0 on {key}"
        # the values are positions, and every position is used once
        assert sorted(set0) == list(range(lists[1][1])), key
        assert sorted(set1) == list(range(lists[0][1])), key


def test_the_item_activation_groups_have_only_one_populated_list():
    """Why the mod's index is unambiguous on these two records, and the
    content evidence for which list status_info_list names: the groups
    named for the stats an item grants carry them in list 1, with lists 0
    and 2 empty."""
    body, header = _tables()
    for key in (STAT_ON_ITEM, STAT_ON_ITEM_NO_ASR):
        lists = _lists(body, header, key)
        counts = [c for _s, c in lists]
        assert counts[0] == 0 and counts[2] == 0, (key, counts)
        assert counts[1] >= 4, (key, counts)


# ------------------------------------------------------------- the writes

def test_applies_the_real_mod_intent():
    """Mod 2634's Global_Critical_Rate, both records."""
    body, header = _tables()
    intents = [
        _intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE),
        _intent(STAT_ON_ITEM_NO_ASR, 3, CRITICAL_DAMAGE,
                entry="StatOnActivateByItemWithoutAttackSpeedRate"),
    ]
    changes, dropped = build_statusgroupinfo_changes(body, header, intents)
    assert not dropped, dropped
    assert len(changes) == 2

    modified = _apply(body, changes)
    assert len(modified) == len(body), "writes must be length-preserving"
    for key in (STAT_ON_ITEM, STAT_ON_ITEM_NO_ASR):
        lists = _lists(modified, header, key)
        elem_start, count = lists[1]
        assert count >= 4
        assert _elem(modified, elem_start + 4 * 3) == CRITICAL_DAMAGE


def test_only_the_targeted_element_moves():
    body, header = _tables()
    changes, _ = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE)])
    modified = _apply(body, changes)
    lists = _lists(body, header, STAT_ON_ITEM)
    at = lists[1][0] + 4 * 3
    diff = [j for j in range(len(body)) if body[j] != modified[j]]
    assert diff, "the mod must change something"
    assert all(at <= j < at + 4 for j in diff)


def test_every_other_record_is_byte_identical():
    body, header = _tables()
    changes, _ = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE)])
    modified = _apply(body, changes)
    _, offsets = parse_pabgh_index(header, "statusgroupinfo")
    for key in offsets:
        if key == STAT_ON_ITEM:
            continue
        o, end = _bounds(body, header, key)
        assert body[o:end] == modified[o:end], key


def test_setting_the_current_key_is_a_noop():
    body, header = _tables()
    lists = _lists(body, header, STAT_ON_ITEM)
    current = _elem(body, lists[1][0] + 4 * 3)
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 3, current)])
    assert changes == []
    assert not dropped


# ----------------------------------------------------------- the refusals

def test_refuses_when_more_than_one_list_could_hold_the_index():
    """The guard that makes the naming inference safe. On a numeric group
    every list is populated, so status_info_list[3] could mean any of
    them -- the writer must refuse rather than pick."""
    body, header = _tables()
    lists = _lists(body, header, NUMERIC_GROUP)
    assert sum(1 for _s, c in lists if c > 3) > 1, "precondition"
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(NUMERIC_GROUP, 3, CRITICAL_DAMAGE,
                               entry="103")])
    assert changes == []
    assert "ambiguous" in dropped[0][1]


def test_refuses_index_past_every_list():
    body, header = _tables()
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 99, CRITICAL_DAMAGE)])
    assert changes == []
    assert "no list with" in dropped[0][1]


def test_refuses_missing_key():
    body, header = _tables()
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(42424242, 3, CRITICAL_DAMAGE)])
    assert changes == []
    assert "no record" in dropped[0][1]


def test_refuses_unsupported_op():
    body, header = _tables()
    i = Format3Intent(entry="StatOnActivateByItem", key=STAT_ON_ITEM,
                      field="status_info_list[3]", op="scale", new=2)
    changes, dropped = build_statusgroupinfo_changes(body, header, [i])
    assert changes == []
    assert "not supported" in dropped[0][1]


@pytest.mark.parametrize("bad", [2 ** 32, -1, 1.5, "1000006", None, True])
def test_refuses_values_that_are_not_record_keys(bad):
    body, header = _tables()
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 3, bad)])
    assert changes == [], f"{bad!r} must not be written"
    assert "statusinfo record key" in dropped[0][1]


def test_refuses_a_record_that_does_not_tile():
    """A table whose layout has drifted must be left alone, not written
    into on the old offsets."""
    body, header = _tables()
    o, _end = _bounds(body, header, STAT_ON_ITEM)
    name_len = struct.unpack_from("<I", body, o + 4)[0]
    corrupt = bytearray(body)
    # inflate the first list's count so the walk can no longer tile
    struct.pack_into("<I", corrupt, o + 8 + name_len + 1, 4000)
    changes, dropped = build_statusgroupinfo_changes(
        bytes(corrupt), header, [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE)])
    assert changes == []
    assert "does not match the known" in dropped[0][1]


@pytest.mark.parametrize("bad", [0, 1, 999999, 1000075, 2000000, 2 ** 32 - 1])
def test_refuses_keys_outside_the_statusinfo_key_space(bad):
    """#320 review: `new` was only checked for being a 32-bit int, so
    status_info_list[3] = 99999 was accepted and written. These lists
    hold statusinfo record keys; anything outside 1000000-1000074 is a
    dangling reference in a list the game dereferences."""
    body, header = _tables()
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 3, bad)])
    assert changes == [], f"{bad} must not be written"
    assert "outside the statusinfo key space" in dropped[0][1]


def test_the_key_space_bound_agrees_with_the_index_table_width():
    """The bound isn't a magic number: the statusinfo snapshot has one
    key per reverse-index slot. If those ever disagree the bound is
    wrong, so pin the agreement rather than the literal."""
    from cdumm.engine.stat_names import STAT_NAMES_CD113
    from cdumm.engine.statusgroupinfo_writer import (
        _TABLE_LEN,
        _status_key_space,
    )
    assert len(STAT_NAMES_CD113) == _TABLE_LEN
    lo, hi = _status_key_space()
    assert (lo, hi) == (min(STAT_NAMES_CD113), max(STAT_NAMES_CD113))
    assert hi - lo + 1 == _TABLE_LEN


def test_key_space_falls_back_when_the_snapshot_is_unusable(monkeypatch,
                                                            caplog):
    """The bound is derived, so it has two failure modes: the snapshot
    module missing (trimmed build) and the snapshot disagreeing with the
    table width. Neither may silently drop the range check -- both must
    fall back to the literal bound, and the disagreement must be logged.
    """
    import builtins
    import logging

    from cdumm.engine import stat_names
    from cdumm.engine.statusgroupinfo_writer import (
        _MAX_STATUS_KEY,
        _MIN_STATUS_KEY,
        _status_key_space,
    )
    expected = (_MIN_STATUS_KEY, _MAX_STATUS_KEY)

    # 1. snapshot present but the wrong width -> fall back AND warn
    monkeypatch.setattr(stat_names, "STAT_NAMES_CD113", {1: "x", 2: "y"})
    with caplog.at_level(logging.WARNING):
        assert _status_key_space() == expected
    assert any("reverse-index tables" in r.message for r in caplog.records), (
        [r.message for r in caplog.records])
    monkeypatch.undo()

    # 2. module unimportable -> fall back, no crash
    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "cdumm.engine.stat_names":
            raise ImportError("simulated trimmed build")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert _status_key_space() == expected


def test_out_of_range_is_still_refused_under_the_fallback(monkeypatch):
    """The fallback must actually be wired into the refusal, not just
    return a tuple nobody consults."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "cdumm.engine.stat_names":
            raise ImportError("simulated trimmed build")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    body, header = _tables()
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 3, 99999)])
    assert changes == []
    assert "outside the statusinfo key space" in dropped[0][1]


def test_every_key_the_mod_writes_is_inside_the_bound():
    """The guard must not refuse the thing it exists to allow."""
    body, header = _tables()
    changes, dropped = build_statusgroupinfo_changes(
        body, header, [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE)])
    assert dropped == [], dropped
    assert len(changes) == 1


# ---------------------------------------------- the reverse index (#320)

def _reverse_index_conflicts(body: bytes, header: bytes):
    """Slots that claim two different statusinfo keys across the table.

    table 0 is a reverse index over list 1: each occupied slot holds a
    position in that record's list 1, and reading the key there gives
    slot -> key. Vanilla is a clean global bijection.
    """
    _, offsets = parse_pabgh_index(header, "statusgroupinfo")
    starts = sorted(offsets.values())
    mapping, conflicts = {}, []
    for key, o in offsets.items():
        i = starts.index(o)
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        lists = parse_record(body, o, end)
        assert lists is not None, key
        # walk past the three lists to reach table 0
        p = lists[-1][0] + 4 * lists[-1][1]
        t0_start, t0_count = p + 4, struct.unpack_from("<I", body, p)[0]
        l1_start, l1_count = lists[1]
        for slot in range(t0_count):
            pos = _elem(body, t0_start + 4 * slot)
            if pos == 0xFFFFFFFF:
                continue
            assert pos < l1_count, (key, slot, pos)   # never out of range
            sk = _elem(body, l1_start + 4 * pos)
            if mapping.setdefault(slot, sk) != sk:
                conflicts.append((slot, mapping[slot], sk))
    return mapping, conflicts


def test_vanilla_reverse_index_is_a_global_bijection():
    body, header = _tables()
    mapping, conflicts = _reverse_index_conflicts(body, header)
    assert conflicts == [], conflicts
    assert len(mapping) == 53


def test_the_mods_write_leaves_the_reverse_index_semantically_stale():
    """#320 review, reproduced and pinned.

    The write changes list 1 without touching table 0, so slot 9 -- which
    every other record uses for CriticalRate -- now resolves to
    CriticalDamage on the two records the mod edits. This test asserts
    the review's finding rather than hiding it: if a future change starts
    maintaining the index, this test is what should be updated to say so.
    """
    body, header = _tables()
    changes, _ = build_statusgroupinfo_changes(
        body, header,
        [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE),
         _intent(STAT_ON_ITEM_NO_ASR, 3, CRITICAL_DAMAGE,
                 entry="StatOnActivateByItemWithoutAttackSpeedRate")])
    patched = _apply(body, changes)
    _mapping, conflicts = _reverse_index_conflicts(patched, header)
    # one per edited record, and the same slot both times
    assert conflicts == [(9, 1000007, CRITICAL_DAMAGE)] * 2, conflicts


def test_the_stale_slot_is_semantic_not_structural():
    """What the review could not settle was harmless vs silently wrong vs
    crash. A crash needs a pointer that dangles or leaves the list; this
    pins that neither happens. Every record still tiles, every index
    entry still points in range at a real element, and the write is
    length-preserving."""
    body, header = _tables()
    changes, _ = build_statusgroupinfo_changes(
        body, header,
        [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE),
         _intent(STAT_ON_ITEM_NO_ASR, 3, CRITICAL_DAMAGE,
                 entry="StatOnActivateByItemWithoutAttackSpeedRate")])
    patched = _apply(body, changes)
    assert len(patched) == len(body)
    assert sum(a != b for a, b in zip(body, patched)) == 2
    # _reverse_index_conflicts asserts pos < l1_count on every entry of
    # every record, and parse_record returns None unless the record tiles
    # exactly -- so reaching the end here is the in-range/tiling proof.
    _reverse_index_conflicts(patched, header)


# --------------------------------------------------- the writer's guards

@pytest.mark.parametrize("mutate,why", [
    (lambda b, o: None, "record shorter than the envelope"),
    (lambda b, o: struct.pack_into("<I", b, o + 4, 10 ** 6),
     "name runs past the record end"),
    (lambda b, o: struct.pack_into("<I", b, o + 4, 2 ** 31),
     "name_len overflows the body"),
])
def test_parse_record_refuses_malformed_records(mutate, why):
    """A record whose envelope is corrupt must return None, not index
    into whatever follows it in the body."""
    body, header = _tables()
    o, end = _bounds(body, header, STAT_ON_ITEM)
    if why == "record shorter than the envelope":
        assert parse_record(body, o, o + 4) is None, why
        return
    corrupt = bytearray(body)
    mutate(corrupt, o)
    assert parse_record(bytes(corrupt), o, end) is None, why


@pytest.mark.parametrize("count,why", [
    (10 ** 9, "list count past the sanity bound"),
    (74, "index table is not 75 entries"),
])
def test_parse_record_refuses_bad_list_and_table_widths(count, why):
    """_read_list's bound and the _TABLE_LEN check. Both exist so a
    drifted layout is refused rather than written into on old offsets."""
    body, header = _tables()
    o, end = _bounds(body, header, STAT_ON_ITEM)
    lists = parse_record(body, o, end)
    assert lists is not None
    corrupt = bytearray(body)
    if why == "list count past the sanity bound":
        target = lists[0][0] - 4                 # list 0's count field
    else:
        # the first index table's count sits after the last list
        target = lists[-1][0] + 4 * lists[-1][1]
    struct.pack_into("<I", corrupt, target, count)
    assert parse_record(bytes(corrupt), o, end) is None, why


def test_parse_record_refuses_a_record_that_over_runs():
    """Shrinking the end by four bytes leaves the last index table
    unable to fit -- refused before the tiling check."""
    body, header = _tables()
    o, end = _bounds(body, header, STAT_ON_ITEM)
    assert parse_record(body, o, end) is not None
    assert parse_record(body, o, end - 4) is None


def test_parse_record_refuses_a_record_that_under_runs():
    """The 'did not tile exactly' arm specifically: the grammar reads
    cleanly but stops SHORT of the record end, so bytes are left over
    that the writer has no account of. Extending the end is the only way
    to reach this -- shrinking it trips the width check first.
    """
    body, header = _tables()
    o, end = _bounds(body, header, STAT_ON_ITEM)
    assert parse_record(body, o, end) is not None
    assert parse_record(body, o, end + 4) is None


def test_read_list_refuses_when_the_count_field_itself_does_not_fit():
    """_read_list's first guard: fewer than 4 bytes remain, so even the
    length prefix would read past the record."""
    from cdumm.engine.statusgroupinfo_writer import _read_list

    body, header = _tables()
    _o, end = _bounds(body, header, STAT_ON_ITEM)
    assert _read_list(body, end - 3, end) is None       # one byte short
    assert _read_list(body, end, end) is None           # nothing left


def test_unreadable_header_drops_every_intent_rather_than_crashing(
        monkeypatch):
    """parse_pabgh_index raising must not take the whole Apply down --
    every intent comes back as dropped with a reason.

    Monkeypatched rather than fed a short header: a short header does NOT
    raise, it warns and returns no offsets, so the intents fall through
    to "no record with key" instead. This guard is for the raising case.
    """
    from cdumm.engine import statusgroupinfo_writer as sw

    def _boom(*_a, **_k):
        raise ValueError("simulated corrupt pabgh")

    monkeypatch.setattr(sw, "parse_pabgh_index", _boom)
    body, header = _tables()
    intents = [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE),
               _intent(STAT_ON_ITEM_NO_ASR, 3, CRITICAL_DAMAGE)]
    changes, dropped = build_statusgroupinfo_changes(body, header, intents)
    assert changes == []
    assert len(dropped) == len(intents)
    assert all("header unreadable" in r for _i, r in dropped), dropped


def test_a_short_header_is_refused_too_just_by_a_different_route():
    """The non-raising path the test above documents: no offsets, so
    every key misses. Either way nothing is written."""
    body, _header = _tables()
    changes, dropped = build_statusgroupinfo_changes(
        body, b"", [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE)])
    assert changes == []
    assert "no record with key" in dropped[0][1]


def test_refuses_a_field_that_is_not_status_info_list():
    body, header = _tables()
    bad = Format3Intent(entry="StatOnActivateByItem", key=STAT_ON_ITEM,
                        field="is_blocked", op="set", new=CRITICAL_DAMAGE)
    changes, dropped = build_statusgroupinfo_changes(body, header, [bad])
    assert changes == []
    assert "is not status_info_list[N]" in dropped[0][1]


@pytest.mark.parametrize("bad_key", [None, "not-an-int", 1.5])
def test_refuses_a_record_key_that_is_not_an_integer(bad_key):
    body, header = _tables()
    i = Format3Intent(entry="StatOnActivateByItem", key=bad_key,
                      field="status_info_list[3]", op="set",
                      new=CRITICAL_DAMAGE)
    changes, dropped = build_statusgroupinfo_changes(body, header, [i])
    assert changes == []
    assert ("not an integer" in dropped[0][1]
            or "no record with key" in dropped[0][1]), dropped


# ------------------------------------------------------------- the wiring
#
# The writer above is well covered in isolation. The dispatch that
# connects it to Apply was not covered at all (33 of 34 added lines in
# format3_apply.py never executed), so these drive the real pipeline.

def _run_apply(intents, tmp_path, participating=None):
    """Drive expand_format3_into_aggregated over a real mod file."""
    import json

    from cdumm.engine.format3_apply import expand_format3_into_aggregated
    from cdumm.storage.database import Database

    body, header = _tables()
    mod = {"modinfo": {"title": "SG", "version": "1.0", "author": "t",
                       "description": "t"},
           "format": 3,
           "targets": [{"file": "statusgroupinfo.pabgb",
                        "intents": intents}]}
    src = tmp_path / "sg.json"
    src.write_text(json.dumps(mod), encoding="utf-8")
    db = Database(tmp_path / "t.db")
    db.initialize()
    db.connection.execute(
        "INSERT INTO mods (id, name, mod_type, enabled, priority, "
        "json_source) VALUES (1, 'SG', 'paz', 1, 1, ?)", (str(src),))
    db.connection.commit()
    aggregated: dict = {}
    warnings: list[str] = []
    expand_format3_into_aggregated(
        aggregated, {}, db, lambda _t: (body, header),
        warnings_out=warnings, participating_mod_ids=participating)
    db.close()
    return aggregated.get("statusgroupinfo.pabgb", []), warnings


def _mod_intent(key, idx, new):
    return {"entry": "StatOnActivateByItem", "key": key,
            "field": f"status_info_list[{idx}]", "op": "set", "new": new}


def test_apply_dispatch_routes_the_real_mod_intent_end_to_end(tmp_path):
    """Mod 2634's Global Critical Rate through the real pipeline, not
    the writer directly: 2 records, 2 one-byte changes, attributed."""
    participating: set = set()
    changes, warnings = _run_apply(
        [_mod_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE),
         _mod_intent(STAT_ON_ITEM_NO_ASR, 3, CRITICAL_DAMAGE)],
        tmp_path, participating=participating)
    assert len(changes) == 2, (changes, warnings)
    assert all(c["_target_file"] == "statusgroupinfo.pabgb" for c in changes)
    assert participating == {1}, participating
    # length-preserving: 4 bytes in, 4 bytes out, per change
    for c in changes:
        assert len(c["original"]) == len(c["patched"]) == 8


def test_apply_surfaces_writer_refusals_to_the_user(tmp_path):
    """A refused intent must reach warnings_out with its reason, not
    only the log."""
    changes, warnings = _run_apply(
        [_mod_intent(STAT_ON_ITEM, 3, 99999)], tmp_path)
    assert changes == []
    skipped = [w for w in warnings if "skipped" in w]
    assert skipped, warnings
    assert "outside the statusinfo key space" in skipped[0], skipped


def test_apply_warns_when_the_mod_produced_nothing(tmp_path):
    """Setting the key already there is a no-op; the user is told the
    mod changed no bytes rather than left to guess."""
    body, header = _tables()
    lists = _lists(body, header, STAT_ON_ITEM)
    already = _elem(body, lists[1][0] + 4 * 3)
    changes, warnings = _run_apply(
        [_mod_intent(STAT_ON_ITEM, 3, already)], tmp_path)
    assert changes == []
    assert [w for w in warnings if "0 byte changes" in w], warnings


def test_a_crashing_writer_does_not_abort_the_apply(tmp_path, monkeypatch):
    """The except-Exception guard in the dispatch. A bug in this writer
    must not take down the rest of the Apply."""
    from cdumm.engine import statusgroupinfo_writer as sw

    def _boom(*_a, **_k):
        raise RuntimeError("simulated writer bug")

    monkeypatch.setattr(sw, "build_statusgroupinfo_changes", _boom)
    changes, warnings = _run_apply(
        [_mod_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE)], tmp_path)
    assert changes == []          # degraded, but the call returned
    assert warnings               # and the user was told


def test_validate_intents_accepts_status_info_list():
    intents = [_intent(STAT_ON_ITEM, 3, CRITICAL_DAMAGE)]
    v = validate_intents("statusgroupinfo.pabgb", intents)
    assert len(v.supported) == 1, v
    assert not v.skipped, v
