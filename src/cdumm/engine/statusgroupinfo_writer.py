"""statusgroupinfo.pabgb ``status_info_list`` writer.

Nexus mod 2634 "Critical Rate Enhancement" (norva2) ships a Global Critical
Rate patch that retargets one slot of the item-activation stat groups::

    {key: 1000006, field: "status_info_list[3]", op: "set", new: 1000006}

The value is a ``statusinfo`` record key -- these lists hold references to
other records, not numbers.

Record layout
-------------
Derived from the live table and checked by exact tiling: the grammar below
consumes every one of the 8 records to the byte, with nothing left over and
nothing short::

    <u32 key><u32 name_len><name>          the record envelope
    <u8   is_blocked>
    <u32 count><count * u32 statusinfo key>   list 0
    <u32 count><count * u32 statusinfo key>   list 1
    <u32 count><count * u32 statusinfo key>   list 2
    <u32 75><75 * u32>                        slot table for list 1
    <u32 75><75 * u32>                        slot table for list 0

The two 75-entry tables are reverse indexes -- 75 is exactly the number of
``statusinfo`` records. Each holds one set entry (everything else is
0xFFFFFFFF) per element of the list it serves, and the entry value is that
element's position in the list. That correspondence holds on all 8 records
for both tables, which is what pairs table 0 with list 1 and table 1 with
list 0. The slot ids are stable across records: slot 9 resolves to
CriticalRate in every record that sets it, slot 10 to DHIT, and so on.

Which list is ``status_info_list``
----------------------------------
The shipped schema (``schemas/pabgb_complete_schema.json``, StatusGroupInfo)
names three list-typed fields -- ``_statusInfoList``,
``_regenerateStatusInfoList`` and ``_elementalStatusInfoList`` -- and the
wire has exactly three lists. Declaration order does not settle which is
which: it is demonstrably not wire order for this table (it puts
``_regenStatusIndexList`` before ``_key``, and swaps ``_isBlocked`` with
``_stringKey``).

The contents do settle it, the same way on both record families:

* List 2 is ``[Temperature, Confusion, Electricity]`` on every record that
  has one -- the three elemental statuses, so it is
  ``_elementalStatusInfoList``.
* List 0 holds Hp, Fatal, Hunger, Stamina, Mp, Morale, Fury -- regenerating
  resources. List 1 holds DDD, DPV, DHIT, CriticalRate, AttackSpeedRate,
  resistances -- derived combat stats.
* On ``StatOnActivateByItem``, whose entire purpose is the stats an item
  grants, list 0 and list 2 are EMPTY and list 1 carries the stats. A group
  named for granting stats cannot have an empty ``_statusInfoList``.

So list 1 is ``status_info_list``. That is an inference from content rather
than a byte-level proof.

How far the ambiguity guard actually reaches
--------------------------------------------
The writer refuses whenever another list in the same record is also long
enough to satisfy the index. **That is a narrower guarantee than "the
answer is forced regardless of the naming"**, which is what an earlier
revision of this docstring claimed (#320 review). The guard only fires
where two lists overlap; above ``len(list0)`` only list 1 fits, so the
index resolves without the guard ever being consulted and the naming
inference is load-bearing and unchecked -- roughly a hundred index
positions across the table.

It happens to be forced for the two records mod 2634 targets, because
their list 0 and list 2 are empty, so no other list could take the
index at all. For those two the answer really is naming-independent.
Elsewhere it is the content argument above doing the work. Two further
confirmations, both from the review rather than this PR's own evidence:
every decoded value resolves to a real stat name, and record 1000007
``StatOnActivateByItemWithoutAttackSpeedRate`` holds exactly record
1000006's list 1 minus ``AttackSpeedRate`` -- the record's own name
describes list 1's contents.
"""
from __future__ import annotations

import logging
import re
import struct

from cdumm.semantic.parser import parse_pabgh_index

logger = logging.getLogger(__name__)

_ENVELOPE = 8
_N_LISTS = 3
_STATUS_INFO_LIST = 1        # which of the three lists (see module docstring)
_N_TABLES = 2
_TABLE_LEN = 75              # one entry per statusinfo record
_MAX_COUNT = 4096            # sanity bound while walking

#: These lists hold ``statusinfo`` record keys, so a value outside that
#: key space is a dangling reference in a list the game dereferences --
#: a plausible crash vector, and never what a mod means. The key space is
#: 1000000..1000074, which is not a magic number here: it is the repo's
#: own statusinfo snapshot (``stat_names.STAT_NAMES_CD113``, 75 keys),
#: and 75 is exactly ``_TABLE_LEN`` -- the reverse-index tables carry one
#: slot per statusinfo record. The two agreeing is the check that this
#: bound is the real one. Verified at import.
_MIN_STATUS_KEY = 1000000
_MAX_STATUS_KEY = 1000074

_FIELD_RE = re.compile(r"^status_info_list\[(\d+)\]$")


def _status_key_space() -> tuple[int, int]:
    """(min, max) statusinfo key, cross-checked against ``_TABLE_LEN``.

    Falls back to the constants above if the snapshot is unavailable, so
    a trimmed build still range-checks rather than accepting anything.
    """
    try:
        from cdumm.engine.stat_names import STAT_NAMES_CD113 as names
    except Exception:  # noqa: BLE001 -- optional module, bound still applies
        return _MIN_STATUS_KEY, _MAX_STATUS_KEY
    if len(names) != _TABLE_LEN:
        logger.warning(
            "statusgroupinfo: statusinfo snapshot has %d keys but the "
            "reverse-index tables have %d slots; using the wider bound",
            len(names), _TABLE_LEN)
        return _MIN_STATUS_KEY, _MAX_STATUS_KEY
    return min(names), max(names)


def _read_list(body: bytes, p: int, end: int) -> tuple[int, int] | None:
    """Return (element_start, count) for the list at ``p``, or None."""
    if p + 4 > end:
        return None
    count = struct.unpack_from("<I", body, p)[0]
    if count > _MAX_COUNT or p + 4 + 4 * count > end:
        return None
    return p + 4, count


def parse_record(body: bytes, start: int, end: int) -> list[tuple[int, int]] | None:
    """The record's three lists as (element_start, count), or None.

    Returns None unless the full grammar consumes the record EXACTLY --
    three lists then two 75-entry tables, ending on the last byte. A record
    that does not tile is not one this writer understands, so it is left
    alone rather than written into.
    """
    if end - start < _ENVELOPE:
        return None
    name_len = struct.unpack_from("<I", body, start + 4)[0]
    p = start + _ENVELOPE + name_len
    if p >= end:
        return None
    p += 1                                    # is_blocked
    lists: list[tuple[int, int]] = []
    for _ in range(_N_LISTS):
        got = _read_list(body, p, end)
        if got is None:
            return None
        elem_start, count = got
        lists.append((elem_start, count))
        p = elem_start + 4 * count
    for _ in range(_N_TABLES):
        got = _read_list(body, p, end)
        if got is None:
            return None
        elem_start, count = got
        if count != _TABLE_LEN:
            return None
        p = elem_start + 4 * count
    if p != end:
        return None                           # did not tile exactly
    return lists


def build_statusgroupinfo_changes(
    vanilla_body: bytes, vanilla_header: bytes, intents: list
) -> tuple[list[dict], list[tuple[object, str]]]:
    """Apply ``status_info_list[i]`` set intents.

    Returns ``(changes, dropped)``: ``changes`` are ``{offset, original,
    patched}`` byte-change dicts absolute in the .pabgb body, one per
    element write; ``dropped`` is ``(intent, reason)`` for anything refused.
    Writes are length-preserving, so no .pabgh companion is emitted.
    """
    dropped: list[tuple[object, str]] = []
    try:
        _, offsets = parse_pabgh_index(vanilla_header, "statusgroupinfo")
    except Exception as e:  # noqa: BLE001 -- never crash the whole apply
        logger.error("statusgroupinfo writer: header unreadable: %s", e)
        return [], [(i, f"statusgroupinfo header unreadable: {e}")
                    for i in intents]
    starts = sorted(offsets.values())
    body_len = len(vanilla_body)
    lo_key, hi_key = _status_key_space()

    changes: list[dict] = []
    for intent in intents:
        field = getattr(intent, "field", "") or ""
        m = _FIELD_RE.match(field)
        if m is None:
            dropped.append((intent,
                            f"field {field!r} is not status_info_list[N]"))
            continue
        op = getattr(intent, "op", "set") or "set"
        if op != "set":
            dropped.append((intent, (
                f"op {op!r} not supported for status_info_list "
                f"(only 'set')")))
            continue
        new = getattr(intent, "new", None)
        if type(new) is not int or not 0 <= new < 2 ** 32:
            dropped.append((intent, (
                f"value {new!r} is not a statusinfo record key "
                f"(a 32-bit integer)")))
            continue
        if not lo_key <= new <= hi_key:
            dropped.append((intent, (
                f"value {new} is outside the statusinfo key space "
                f"({lo_key}-{hi_key}); writing it would leave a dangling "
                f"record reference in a list the game dereferences")))
            continue
        raw_key = getattr(intent, "key", None)
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            dropped.append((intent, (
                f"record key {raw_key!r} is not an integer")))
            continue
        o = offsets.get(key)
        if o is None:
            dropped.append((intent, (
                f"statusgroupinfo has no record with key {key}")))
            continue
        i = starts.index(o)
        end = starts[i + 1] if i + 1 < len(starts) else body_len
        lists = parse_record(vanilla_body, o, end)
        if lists is None:
            dropped.append((intent, (
                f"record key {key} does not match the known "
                f"statusgroupinfo layout")))
            continue

        idx = int(m.group(1))
        fits = [n for n, (_s, c) in enumerate(lists) if idx < c]
        if not fits:
            dropped.append((intent, (
                f"record key {key} has no list with a "
                f"status_info_list[{idx}] "
                f"(lengths {[c for _s, c in lists]})")))
            continue
        if fits != [_STATUS_INFO_LIST]:
            # More than one list could take this index, so which one the
            # mod means is not forced by the data. Refuse rather than pick.
            # Note this guard only reaches overlapping indexes -- above
            # len(list0) only list 1 fits and the naming inference in the
            # module docstring carries the write unchecked. See the
            # "How far the ambiguity guard actually reaches" section.
            dropped.append((intent, (
                f"status_info_list[{idx}] is ambiguous on record key "
                f"{key}: lists {fits} could all hold that index")))
            continue

        elem_start, _count = lists[_STATUS_INFO_LIST]
        at = elem_start + 4 * idx
        original = vanilla_body[at:at + 4]
        patched = struct.pack("<I", new)
        if original == patched:
            continue                          # already holds that key
        changes.append({
            "offset": at,
            "original": original.hex(),
            "patched": patched.hex(),
        })
    return changes, dropped
