"""iteminfo sharpness_data path alias (DMM name vs CDUMM decoded shape).

ExtensiveItemBuffs writes ``sharpness_data.stat_data.stat_list_static``.
CDUMM's native parser decodes the same bytes as a flat
``sharpness_data.stat_list``, so every one of those intents resolved to
nothing -- 1452 of them across the Nexus corpus, dropped as
"unresolved" with the mod reporting success.

That the two names are the same field was measured on live 1.15, not
inferred:

  * ``sharpness_data`` decodes as a dict on 6508/6508 records, keys
    {craft_tool_info, max_sharpness, p_prefix, shape, stat_list, tail,
     w_trailing, w_unk_a}. No ``stat_data``.
  * ``_read_ItemInfoSharpnessData`` reads one flat
    ``u32 stat_count + N*12 stats``, so ``stat_list`` is the only stat
    list under it.
  * elements are ``('change_mb', 'stat')`` -- the shape the mod writes.
  * same record: key 14510 Marni_Devotee_PlateArmor_Helm decodes
    ``[{stat: 1000003, change_mb: 1000}]``; the mod sets that stat to
    8000.
  * writing through ``stat_list`` and re-serializing changed exactly 2
    bytes (e803 -> 401f, i.e. 1000 -> 8000), both inside record 14510,
    with record offsets identical to vanilla -- length-preserving, so
    no .pabgh rebuild.

These tests pin the rewrite itself. The byte-exact evidence above needed
a live install and can't run in CI.
"""
from __future__ import annotations

from cdumm.engine.iteminfo_writer import (
    _canonical_nested_path,
    _resolve_path_target,
    apply_nested_intent,
)


def _item() -> dict:
    """A record shaped like the native parser's real output."""
    return {
        "key": 14510,
        "string_key": "Marni_Devotee_PlateArmor_Helm",
        "sharpness_data": {
            "w_unk_a": 0,
            "max_sharpness": 100,
            "craft_tool_info": 0,
            "shape": "W",
            "stat_list": [{"stat": 1000003, "change_mb": 1000}],
            "tail": b"\x00\x00\x00",
            "p_prefix": None,
            "w_trailing": b"",
        },
    }


# ── the rewrite itself ───────────────────────────────────────────────

def test_bare_dmm_path_is_rewritten():
    assert _canonical_nested_path(
        "sharpness_data.stat_data.stat_list_static"
    ) == "sharpness_data.stat_list"


def test_indexed_suffix_survives_the_rewrite():
    """The prefix is replaced; everything after it is preserved."""
    assert _canonical_nested_path(
        "sharpness_data.stat_data.stat_list_static[0].change_mb"
    ) == "sharpness_data.stat_list[0].change_mb"


def test_dotted_suffix_survives_the_rewrite():
    assert _canonical_nested_path(
        "sharpness_data.stat_data.stat_list_static.0.change_mb"
    ) == "sharpness_data.stat_list.0.change_mb"


def test_unrelated_paths_are_untouched():
    """Scoped to the one prefix -- nothing else may be rewritten.

    enchant_data_list genuinely IS nested as
    enchant_stat_data.stat_list_static and already resolves, so
    rewriting it would break 14,899 working intents.
    """
    for p in (
        "enchant_data_list[4].enchant_stat_data.stat_list_static",
        "sharpness_data.stat_list",
        "sharpness_data.max_sharpness",
        "drop_default_data.use_socket",
        "max_stack_count",
    ):
        assert _canonical_nested_path(p) == p


def test_a_similar_but_different_prefix_is_not_rewritten():
    """Prefix matching must be on a path boundary, not a substring."""
    p = "sharpness_data.stat_data.stat_list_static_extra"
    assert _canonical_nested_path(p) == p


# ── it actually resolves and writes ──────────────────────────────────

def test_dmm_path_now_resolves_to_the_stat_list():
    item = _item()
    got = _resolve_path_target(
        item, "sharpness_data.stat_data.stat_list_static")
    assert got is not None, "the DMM path must resolve after aliasing"
    parent, seg = got
    assert parent is item["sharpness_data"]
    assert seg == "stat_list"


def test_the_mods_real_intent_applies():
    """The exact intent from ExtensiveItemBuffs on key 14510."""
    item = _item()
    outcome = apply_nested_intent(
        item, "sharpness_data.stat_data.stat_list_static",
        [{"stat": 1000003, "change_mb": 8000}])
    assert outcome == "ok", outcome
    assert item["sharpness_data"]["stat_list"] == [
        {"stat": 1000003, "change_mb": 8000}]


def test_element_level_write_through_the_dmm_path():
    item = _item()
    outcome = apply_nested_intent(
        item, "sharpness_data.stat_data.stat_list_static[0].change_mb",
        8000)
    assert outcome == "ok", outcome
    assert item["sharpness_data"]["stat_list"][0]["change_mb"] == 8000
    # the sibling key must be untouched
    assert item["sharpness_data"]["stat_list"][0]["stat"] == 1000003


def test_before_the_alias_this_path_was_unresolved():
    """Pins WHY the alias is needed: the raw DMM path hits nothing.

    Resolving it against the un-aliased name is what produced 1452
    'skipped (unresolved)' warnings while the mod reported success.
    """
    item = _item()
    assert _resolve_path_target(item, "sharpness_data.stat_data") is None
    assert "stat_data" not in item["sharpness_data"]


def test_a_record_without_sharpness_stats_still_refuses():
    """Aliasing must not invent a target where there is none."""
    item = _item()
    del item["sharpness_data"]["stat_list"]
    assert _resolve_path_target(
        item, "sharpness_data.stat_data.stat_list_static") is None
    assert apply_nested_intent(
        item, "sharpness_data.stat_data.stat_list_static",
        [{"stat": 1, "change_mb": 2}]) == "unresolved"
