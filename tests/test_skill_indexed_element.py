"""skill.pabgb: current DMM indexed-element paths.

Timuela's "Focus Aerial Roll doesn't cost Spirit" sets
``use_resource_stat_list[0].d`` on three dash skills. Every variant of
that mod applied **zero bytes**. Two separate reasons, both fixed here:

1. ``LIST_WRITERS`` carried only the legacy whole-list names
   (``_useResourceStatList``). Current DMM addresses ONE element and one
   field inside it, by letter. Registered under the wildcard key
   ``use_resource_stat_list[].d``, the same normalization #190 added for
   equipslotinfo.

2. ``format3_apply``'s ``list_routable`` filter matched only the RAW
   field, not the normalized wildcard -- so even once registered, the
   intents passed validation and then produced no change. That is the
   same raw-vs-normalized mismatch, on the apply side.

The letters map onto the element's WIRE order, which the vendored
parser spells out in ``_parse_resource_stat`` (22 bytes, matching the
``cnt * 22`` stride the record walker uses):

    a stat_type u8 | b stat_hash u32 | c flag u8
    d value i64    | e hash2 u32     | f hash3 u32

so ``.d`` is ``value``, the resource cost -- vanilla -20000 on all three
dash skills. This also settles a disagreement between the mod's own
variants: the DMM V3 export writes ``18446744073709551615``, which is
**-1** in the i64 the field actually is, not a colossal cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from cdumm.engine.skill_writer import (
    _RESOURCE_STAT_LETTERS,
    _as_signed,
    parse_indexed_element_field,
)


@dataclass
class _Intent:
    entry: str
    key: int
    field: str
    op: str
    new: Any
    old: Any = None


# ── path parsing ────────────────────────────────────────────────────

def test_letter_d_is_value():
    assert parse_indexed_element_field(
        "use_resource_stat_list[0].d") == (
        "_useResourceStatList", 0, "value")


def test_every_letter_maps_to_the_parser_wire_order():
    for i, name in enumerate(_RESOURCE_STAT_LETTERS):
        letter = chr(ord("a") + i)
        assert parse_indexed_element_field(
            f"use_resource_stat_list[3].{letter}") == (
            "_useResourceStatList", 3, name)


def test_explicit_names_work_too():
    assert parse_indexed_element_field(
        "use_resource_stat_list[1].value") == (
        "_useResourceStatList", 1, "value")


def test_unknown_leaf_is_refused():
    assert parse_indexed_element_field(
        "use_resource_stat_list[0].z") is None
    assert parse_indexed_element_field(
        "use_resource_stat_list[0].nonsense") is None


def test_buff_level_list_is_refused_no_letter_mapping_yet():
    """It's a list OF lists; guessing a letter mapping would be wrong."""
    assert parse_indexed_element_field("buff_level_list[0].a") is None


def test_non_indexed_paths_are_not_claimed():
    assert parse_indexed_element_field("_useResourceStatList") is None
    assert parse_indexed_element_field("") is None


# ── the signed-value conversion ─────────────────────────────────────

def test_dmm_unsigned_u64_max_is_minus_one():
    """The DMM V3 export writes 18446744073709551615 for a field the
    game stores as i64. Written raw it doesn't even fit '<q'."""
    assert _as_signed(18446744073709551615, 64) == -1


def test_small_positive_values_pass_through():
    assert _as_signed(1, 64) == 1
    assert _as_signed(0, 64) == 0


def test_vanilla_cost_round_trips():
    assert _as_signed(-20000 & 0xFFFFFFFFFFFFFFFF, 64) == -20000


# ── registry wiring ─────────────────────────────────────────────────

def test_wildcard_keys_registered_for_every_resolvable_leaf():
    """LIST_WRITERS and parse_indexed_element_field must not drift."""
    from cdumm.engine.format3_handler import LIST_WRITERS
    leaves = [chr(ord("a") + i)
              for i in range(len(_RESOURCE_STAT_LETTERS))]
    leaves += list(_RESOURCE_STAT_LETTERS)
    for leaf in leaves:
        assert parse_indexed_element_field(
            f"use_resource_stat_list[0].{leaf}") is not None, leaf
        assert ("skill", f"use_resource_stat_list[].{leaf}") \
            in LIST_WRITERS, leaf


def test_legacy_whole_list_names_still_registered():
    from cdumm.engine.format3_handler import LIST_WRITERS
    assert ("skill", "_useResourceStatList") in LIST_WRITERS
    assert ("skill", "_buffLevelList") in LIST_WRITERS


def test_validator_accepts_the_indexed_path():
    from cdumm.engine.format3_handler import Format3Intent, validate_intents
    res = validate_intents("skill.pabgb", [
        Format3Intent(entry="Skill_CrowSuperDash", key=15045,
                      field="use_resource_stat_list[0].d",
                      op="set", new=1),
    ])
    assert len(res.supported) == 1, [r for _i, r in res.skipped]


def test_apply_side_routes_the_normalized_key():
    """The bug that made this validate-then-do-nothing: the apply-side
    filter must match the wildcard, not just the raw field."""
    import re

    from cdumm.engine.format3_handler import LIST_WRITERS
    field = "use_resource_stat_list[0].d"
    assert ("skill", field) not in LIST_WRITERS
    normalized = re.sub(r"\[\d+\]", "[]", field)
    assert ("skill", normalized) in LIST_WRITERS


@pytest.mark.parametrize("bad", [
    "use_resource_stat_list[].d",      # no index
    "use_resource_stat_list.d",        # no brackets
    "use_resource_stat_list[0]",       # no leaf
])
def test_malformed_paths_refused(bad):
    assert parse_indexed_element_field(bad) is None
