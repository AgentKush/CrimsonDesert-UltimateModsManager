"""Release-review finding (2026-06-10): adding storeinfo.pabgb and
equipslotinfo.pabgb to _WHOLE_TABLE_TARGETS diverted EVERY intent on
those targets into the dedicated list writers, which only understand
stock_data_list / entries[N].etl_hashes. storeinfo has a PABGB schema,
so scalar intents (e.g. _buyableStockCount) used to flow through the
standard schema walk and produce byte changes; after the diversion
they silently produced nothing.

The fix partitions the batch: writer-supported fields go to the
dedicated writer, everything else falls through to
_intents_to_v2_changes. This test pins that a mod mixing
stock_data_list with a scalar storeinfo edit produces BOTH change
kinds.

The same bug recurred on interactioninfo (#317 review, 2026-07-28) --
see the second test below, which runs off a committed fixture rather
than a local repro directory, so it is not skippable.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from cdumm.storage.database import Database

_BASE = Path(__file__).resolve().parents[1] / "issue_repro" / "183"
_BODY = _BASE / "vanilla" / "storeinfo.pabgb"
_HDR = _BASE / "vanilla" / "storeinfo.pabgh"
_MOD = _BASE / "IHateLacey.json"


def _have_fixtures() -> bool:
    return _BODY.exists() and _HDR.exists() and _MOD.exists()


@pytest.mark.skipif(not _have_fixtures(), reason="183 fixtures absent")
def test_mixed_storeinfo_intents_produce_list_and_scalar_changes(tmp_path):
    from cdumm.engine.format3_apply import expand_format3_into_aggregated

    body = _BODY.read_bytes()
    header = _HDR.read_bytes()

    stock = json.loads(_MOD.read_text(encoding="utf-8"))
    stock_intent = stock["targets"][0]["intents"][0]
    mixed = {
        "modinfo": {"title": "MixedStoreMod", "version": "1.0",
                    "author": "t", "description": "t"},
        "format": 3,
        "targets": [{
            "file": "storeinfo.pabgb",
            "intents": [
                stock_intent,
                {"entry": "Store_Her_General", "key": 3101,
                 "field": "_buyableStockCount", "op": "set", "new": 77},
            ],
        }],
    }
    src = tmp_path / "mixed.json"
    src.write_text(json.dumps(mixed), encoding="utf-8")

    db = Database(tmp_path / "t.db")
    db.initialize()
    db.connection.execute(
        "INSERT INTO mods (id, name, mod_type, enabled, priority, "
        "json_source) VALUES (1, 'MixedStoreMod', 'paz', 1, 1, ?)",
        (str(src),))
    db.connection.commit()

    def extractor(target):
        assert target == "storeinfo.pabgb"
        return body, header

    aggregated: dict = {}
    warnings: list[str] = []
    expand_format3_into_aggregated(
        aggregated, {}, db, extractor, warnings_out=warnings)
    db.close()

    changes = aggregated.get("storeinfo.pabgb", [])
    labels = [c.get("label", "") for c in changes]
    assert any("stock_data_list" in l for l in labels), (
        f"list-writer change missing: labels={labels}, "
        f"warnings={warnings}")
    assert len(changes) >= 2, (
        f"expected list + scalar changes, got {len(changes)}: "
        f"{labels} warnings={warnings}")
    # the companion pabgh rebuild must still be present (list grew)
    assert aggregated.get("storeinfo.pabgh"), "pabgh rebuild missing"


# --------------------------------------------------------------------
# interactioninfo (#317 review). Same class of bug, second occurrence.
# --------------------------------------------------------------------

_V115 = Path(__file__).resolve().parent / "fixtures" / "vanilla115"

_needs_v115 = pytest.mark.skipif(
    not (_V115 / "interactioninfo.pabgb.zlib").exists(),
    reason="vanilla115 interactioninfo fixture absent")

_RAW_3_0 = 1077936128            # f32 3.0, the shape Fast Pickup writes

#: field -> a value that is in range for it. The three scalars reach the
#: generic walker; the two pivot fields reach interactioninfo_writer.
#: (``auto_moving_stop_distance`` is narrow -- a 32-bit float bit pattern
#: overflows it and applies nothing, which is why 7 is used here.)
_INTERACTIONINFO_CASES = {
    "is_blocked": 1,
    "interaction_type": 1,
    "auto_moving_stop_distance": 7,
    "interaction_pivot_list[0].raw_a": _RAW_3_0,
    "interaction_pivot_list[0].raw_b": _RAW_3_0,
}


def _run_interactioninfo(intents, tmp_path, participating=None):
    from cdumm.engine.format3_apply import expand_format3_into_aggregated

    def _load(name):
        return zlib.decompress((_V115 / (name + ".zlib")).read_bytes())

    body, header = _load("interactioninfo.pabgb"), _load(
        "interactioninfo.pabgh")
    mod = {
        "modinfo": {"title": "M", "version": "1.0", "author": "t",
                    "description": "t"},
        "format": 3,
        "targets": [{"file": "interactioninfo.pabgb", "intents": intents}],
    }
    src = tmp_path / "m.json"
    src.write_text(json.dumps(mod), encoding="utf-8")
    db = Database(tmp_path / "t.db")
    db.initialize()
    db.connection.execute(
        "INSERT INTO mods (id, name, mod_type, enabled, priority, "
        "json_source) VALUES (1, 'M', 'paz', 1, 1, ?)", (str(src),))
    db.connection.commit()
    aggregated: dict = {}
    warnings: list[str] = []
    expand_format3_into_aggregated(
        aggregated, {}, db, lambda _t: (body, header),
        warnings_out=warnings, participating_mod_ids=participating)
    db.close()
    return aggregated.get("interactioninfo.pabgb", []), warnings


@_needs_v115
@pytest.mark.parametrize("field,new", list(_INTERACTIONINFO_CASES.items()))
def test_every_interactioninfo_field_still_applies(field, new, tmp_path):
    """The owner's #317 review table, pinned.

    Routing the whole table to interactioninfo_writer -- which only
    places ``interaction_pivot_list[0].raw_a/.raw_b`` -- regressed
    is_blocked, interaction_type and auto_moving_stop_distance from
    1 change each to 0-refused. Each row here was 0 on the pre-fix
    branch and is 1 now.
    """
    changes, warnings = _run_interactioninfo(
        [{"entry": "Gimmick_PickUp", "key": 1000004, "field": field,
          "op": "set", "new": new}], tmp_path)
    assert len(changes) == 1, (
        f"{field} applied {len(changes)} change(s); warnings={warnings}")


@_needs_v115
def test_mixed_interactioninfo_mod_gets_writer_and_walker_changes(tmp_path):
    """All five in ONE mod: 2 before the partition, 5 after."""
    intents = [{"entry": "Gimmick_PickUp", "key": 1000004, "field": f,
                "op": "set", "new": v}
               for f, v in _INTERACTIONINFO_CASES.items()]
    changes, warnings = _run_interactioninfo(intents, tmp_path)
    assert len(changes) == len(_INTERACTIONINFO_CASES), (
        f"got {len(changes)} of {len(_INTERACTIONINFO_CASES)}; "
        f"warnings={warnings}")
    labels = {c.get("label", "") for c in changes}
    for scalar in ("is_blocked", "interaction_type",
                   "auto_moving_stop_distance"):
        assert f"Gimmick_PickUp.{scalar}" in labels, (scalar, labels)


#: f32 2.5 -- what Gimmick_PickUp's raw_a already holds, so writing it
#: back is a genuine no-op and the writer emits no change.
_RAW_2_5 = 1075838976


@_needs_v115
def test_zero_bytes_warning_is_silent_when_the_walker_contributed(tmp_path):
    """The partition made "0 byte changes" reachable while the mod DID
    apply something: the writer can legitimately emit nothing (the pivot
    already holds the value) while a scalar on the standard path lands.
    Warning the user their mod did nothing would then be wrong."""
    changes, warnings = _run_interactioninfo([
        {"entry": "Gimmick_PickUp", "key": 1000004, "field": "is_blocked",
         "op": "set", "new": 1},
        {"entry": "Gimmick_PickUp", "key": 1000004,
         "field": "interaction_pivot_list[0].raw_a", "op": "set",
         "new": _RAW_2_5},
    ], tmp_path)
    assert len(changes) == 1, (changes, warnings)
    assert not [w for w in warnings if "0 byte changes" in w], warnings


@_needs_v115
def test_zero_bytes_warning_still_fires_when_nothing_applied(tmp_path):
    """The other half of the guard: suppressing the warning must not
    suppress it when the mod really did apply nothing."""
    changes, warnings = _run_interactioninfo([
        {"entry": "Gimmick_PickUp", "key": 1000004,
         "field": "interaction_pivot_list[0].raw_a", "op": "set",
         "new": _RAW_2_5},
    ], tmp_path)
    assert changes == []
    assert [w for w in warnings if "0 byte changes" in w], warnings


#: One of the 98 records the locator refuses to frame -- used to drive
#: the writer's refusal all the way out to warnings_out.
_UNFRAMEABLE_KEY = 1000021       # Gimmick_PickUp_Climb


@_needs_v115
@pytest.mark.parametrize("field,new,label", [
    ("interaction_pivot_list[0].raw_a", _RAW_3_0, "writer path"),
    ("is_blocked", 1, "standard path"),
])
def test_both_paths_attribute_the_mod_as_participating(field, new, label,
                                                       tmp_path):
    """Each branch of the partition records which mods contributed.
    Missing that on either side means a mod silently isn't credited for
    the bytes it applied."""
    participating: set = set()
    changes, warnings = _run_interactioninfo(
        [{"entry": "Gimmick_PickUp", "key": 1000004, "field": field,
          "op": "set", "new": new}], tmp_path, participating=participating)
    assert len(changes) == 1, (label, changes, warnings)
    assert participating == {1}, (label, participating)


@_needs_v115
def test_writer_refusals_reach_the_user_not_just_the_log(tmp_path):
    """An intent the writer refuses must surface in warnings_out naming
    the record and the reason. Refusing quietly is the failure mode this
    whole writer exists to avoid."""
    changes, warnings = _run_interactioninfo([
        {"entry": "", "key": _UNFRAMEABLE_KEY,
         "field": "interaction_pivot_list[0].raw_a", "op": "set",
         "new": _RAW_3_0},
    ], tmp_path)
    assert changes == []
    skipped = [w for w in warnings if "skipped" in w]
    assert skipped, warnings
    assert "could not be located unambiguously" in skipped[0], skipped


@_needs_v115
def test_a_crashing_writer_does_not_take_the_whole_apply_down(
        tmp_path, monkeypatch):
    """The except-Exception guard around the writer. A bug in one table's
    writer must not abort every other mod in the same Apply."""
    from cdumm.engine import interactioninfo_writer

    def _boom(*_a, **_k):
        raise RuntimeError("simulated writer bug")

    monkeypatch.setattr(
        interactioninfo_writer, "build_interactioninfo_changes", _boom)
    changes, warnings = _run_interactioninfo([
        {"entry": "Gimmick_PickUp", "key": 1000004, "field": "is_blocked",
         "op": "set", "new": 1},
        {"entry": "Gimmick_PickUp", "key": 1000004,
         "field": "interaction_pivot_list[0].raw_a", "op": "set",
         "new": _RAW_3_0},
    ], tmp_path)
    # the standard-path intent still applied despite the writer blowing up
    assert len(changes) == 1, (changes, warnings)
    assert changes[0].get("label") == "Gimmick_PickUp.is_blocked"
