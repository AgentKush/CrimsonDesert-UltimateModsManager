"""CD 1.16 added ``_isNoAlert`` to SkillInfo — GitHub #355.

29% of the skill table (589 of 2013 entries) failed to parse on CD 1.16,
every one of them with ``Cannot find post-buff boundary``. The cause is a
single u8 the game inserted between ``_isUseChildPatternDescriptionBuffData``
and ``_damageType``, turning a run of six consecutive u8 flags into seven.
The field is named by the binary's own reflection data at SkillInfo index
25, between indices 24 and 26.

WHY THE FAILURE COUNT UNDERSTATED THE DAMAGE
--------------------------------------------
The 1424 entries that did "parse" on 1.16 were not fine. ``_parse_body``
falls back to ``_find_post_buff_start``, which brute-forces a start offset
and accepts any position where the reader lands exactly on the record end.
A record carrying one unread byte satisfies that test at start+1, so the
old layout began reading the post-buff block one byte late.

Everything from ``_damageType`` onward then re-aligned by accident and read
correctly, while every field before it read shifted. Measured against the
same records on 1.13: ``_maxLevel``, ``_damageType`` and ``_uiType`` agreed
on 100% of records, while ``_skillGroupKey`` and ``_applyType`` agreed on
0%. So the visible symptom was 589 refusals and the invisible one was 1424
records silently decoding their early post-buff fields from the wrong
offset.

WHY DETECTION IS PER-TABLE
--------------------------
A single record cannot tell the two layouts apart. The brute-force search
makes the wrong layout land exactly on the record end, and the entry then
round-trips byte-exact as well, because it writes back whatever it read.
Both per-record signals are blind. Only the aggregate separates them, which
is what ``_detect_is_no_alert`` uses and what the counts below pin.
"""
from __future__ import annotations

import struct

import pytest

from cdumm._vendor import skillinfo_parser as P
from tests.fixture_loaders import (
    has_vanilla113,
    has_vanilla116,
    load_vanilla113,
    load_vanilla116,
)

# (loader, label, entries, expected _isNoAlert)
_BUILDS = (
    (load_vanilla113, "CD 1.13", 1999, False),
    (load_vanilla116, "CD 1.16", 2013, True),
)


@pytest.fixture(autouse=True)
def _restore_global():
    """``_has_is_no_alert`` is module state; do not leak it between tests."""
    before = P._has_is_no_alert
    yield
    P._has_is_no_alert = before


def _skip_if_absent(label):
    have = has_vanilla113 if label == "CD 1.13" else has_vanilla116
    if not have("skill.pabgb") or not have("skill.pabgh"):
        pytest.skip(f"{label} skill fixture absent")


def _entries(body, header):
    index = P.parse_skill_pabgh(header)
    for i, (_key, off) in enumerate(index):
        end = index[i + 1][1] if i + 1 < len(index) else len(body)
        yield off, end


@pytest.mark.parametrize("load,label,n_entries,want_flag", _BUILDS,
                         ids=[b[1] for b in _BUILDS])
def test_every_entry_parses_and_round_trips(load, label, n_entries, want_flag):
    """THE test: the whole table decodes, and re-serializes byte-identically.

    Byte-exact round-trip on every record is the standard that matters here.
    Parse success alone moves for the wrong reasons, because the boundary
    search will happily accept a wrong start offset.
    """
    _skip_if_absent(label)
    body = load("skill.pabgb")
    header = load("skill.pabgh")

    parsed = P.parse_all(header, body)
    assert len(parsed) == n_entries
    assert P._has_is_no_alert is want_flag, (
        f"{label}: detected _isNoAlert={P._has_is_no_alert}, expected "
        f"{want_flag}")

    ok = 0
    for off, end in _entries(body, header):
        entry = P.parse_skill_entry(body, off, end)
        assert P.serialize_entry(entry) == body[off:end]
        ok += 1
    assert ok == n_entries


@pytest.mark.parametrize("load,label,n_entries,want_flag", _BUILDS,
                         ids=[b[1] for b in _BUILDS])
def test_the_other_layout_is_decisively_worse(load, label, n_entries,
                                              want_flag):
    """The wrong layout must lose clearly, or detection is a coin flip.

    This is the assertion that would fail if a future build moved the flag
    run again: the two layouts would stop being separable and the aggregate
    vote would start breaking ties by accident.
    """
    _skip_if_absent(label)
    body = load("skill.pabgb")
    header = load("skill.pabgh")

    def count(flag):
        P._has_is_no_alert = flag
        n = 0
        for off, end in _entries(body, header):
            try:
                P.parse_skill_entry(body, off, end)
            except (ValueError, struct.error, IndexError, AssertionError):
                continue
            n += 1
        return n

    right = count(want_flag)
    wrong = count(not want_flag)
    assert right == n_entries
    assert wrong < right, (
        f"{label}: wrong layout parsed {wrong} against {right} — the two "
        f"layouts are no longer separable")


def test_is_no_alert_sits_between_the_two_flags_the_binary_names():
    """Position check, independent of the counts above.

    SkillInfo's reflection data orders index 24 ``_isUseChildPattern...``,
    25 ``_isNoAlert``, 26 ``_damageType``. Reading it anywhere else in the
    u8 run would still consume the same number of bytes and still
    round-trip, so the counts cannot pin the position — only the value can.
    """
    _skip_if_absent("CD 1.16")
    body = load_vanilla116("skill.pabgb")
    header = load_vanilla116("skill.pabgh")
    P.parse_all(header, body)
    assert P._has_is_no_alert is True

    seen = 0
    for off, end in _entries(body, header):
        entry = P.parse_skill_entry(body, off, end)
        assert "_isNoAlert" in entry
        # A bool in the game's own naming convention.
        assert entry["_isNoAlert"] in (0, 1)
        seen += 1
    assert seen == 2013


def test_pre_116_tables_do_not_grow_the_field():
    """The 1.13 layout must not gain a field it never had."""
    _skip_if_absent("CD 1.13")
    body = load_vanilla113("skill.pabgb")
    header = load_vanilla113("skill.pabgh")
    P.parse_all(header, body)
    assert P._has_is_no_alert is False
    off, end = next(iter(_entries(body, header)))
    assert "_isNoAlert" not in P.parse_skill_entry(body, off, end)
