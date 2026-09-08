"""StageInfo is misaligned past ``_sequencerDesc``, so it is gated.

The schema was ported from an upstream parser rather than derived from
the game, and the exe disagrees with it structurally, not by a swap.
Reading the deserializer at 0x141488903 on buildid 25116796:

* ``_isBlocked`` (1 byte, ``+0x10``), then ``_name`` (``+0x18``),
  ``_stageDesc`` (``+0x38``) and ``_completeLog`` (``+0x58``) through
  reader 0x141231470, then ``_sequencerDesc`` (``+0x78``) through
  0x14228E9D0. Those five match this schema's order.
* It then reads THREE fields the schema does not contain at all:
  ``_spawnFactionSpawnDataInfo`` (``+0x168``), ``_spawnFactionNodeInfo``
  (``+0x16A``) and ``_disableFactionSpawnPartyNameHashList``
  (``+0x170``).
* Only then does it reach ``_stageCategory``, and it reads EIGHT stream
  bytes where the schema declares ``u32``.

So everything from ``_stageCategory`` onward sits at an offset that is
not proven, which is why the walk reaches 0% of 51,861 records complete.

``_verified_fields`` is the existing mechanism for exactly this: the grid
renders ungated fields as ``(unverified)`` rather than a possibly-wrong
number, and ``format3_apply`` refuses to write to them, so a mod cannot
land a value on the wrong byte. Gating is the honest state until the
table is re-derived. That re-derivation is blocked on the six
variable-length list element readers ``tools/derive_table_layout.py``
reports it cannot fit (GitHub #409).
"""
from __future__ import annotations

import json
from pathlib import Path

from cdumm.semantic.parser import get_schema

_SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
_EXPECTED = ["_isBlocked", "_name", "_stageDesc", "_completeLog",
             "_sequencerDesc"]


def _override() -> dict:
    return json.loads((_SCHEMAS / "pabgb_type_overrides.json")
                      .read_text(encoding="utf-8-sig"))["StageInfo"]


def test_stageinfo_vouches_only_for_the_five_confirmed_fields():
    assert _override()["_verified_fields"] == _EXPECTED


def test_the_gate_reaches_the_loaded_schema():
    """An override nothing reads would be decoration."""
    vf = get_schema("stageinfo").verified_fields
    assert vf is not None, "StageInfo lost its verified-only gate"
    assert set(vf) == set(_EXPECTED)


def test_the_misaligned_fields_are_not_vouched_for():
    """The named evidence, pinned so a future widening has to face it."""
    vf = get_schema("stageinfo").verified_fields
    for name in ("_stageCategory", "_stageDataType", "_fieldInfo",
                 "_closeFilterByGroup", "_closeCondition"):
        assert name not in vf, (
            f"{name} was vouched for, but the exe reads it at an offset "
            f"this schema does not produce")


def test_gating_did_not_drop_any_field_from_the_order():
    """The gate is a display and write guard, not a schema truncation.

    Dropping a field from ``_ordered_fields`` would shift every later
    field's offset, which is the failure CONSENSUS-1 guards against in
    the loader. The order must stay whole.
    """
    assert len(_override()["_ordered_fields"]) == 81
    assert len(get_schema("stageinfo").fields) == 81
