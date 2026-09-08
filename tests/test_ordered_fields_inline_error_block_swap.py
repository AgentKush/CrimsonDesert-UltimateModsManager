"""Two verified orders had a pair of fields the wrong way round.

``QuestGaugeInfo`` and ``AIEventTableInfo`` were derived on 2026-08-13 by
``tools/derive_table_layout.py``, which ordered fields by the hot-path
branch that reaches each field's error block. That rule was missing a
basic-block leader at the fall-through of a conditional branch, so an
inline (not outlined) error block folded into the block before the
field's own ``jne ok`` and got keyed on an inbound edge belonging to
something else. GitHub #407 fixed the rule; these two orders are the
fallout of having been derived before it.

WHY THE ORIGINAL PROOF DID NOT CATCH IT
---------------------------------------
Both entries quote a byte-consumption proof: the walk consumes every
record to its exact last byte. That is true of the wrong order too. The
swapped fields are fixed-width primitives, so the pair consumes the same
total either way (12 bytes on QuestGaugeInfo, 9 on AIEventTableInfo) and
the walk still lands on the final byte. Byte consumption cannot see a
same-total swap, which is why this needs a value-domain test rather than
another decode check.

WHAT PINS THE CORRECTED ORDER
-----------------------------
Three independent things, all on buildid 25116796.

1. The deserializer itself. QuestGaugeInfo reads 8 bytes into ``+0x78``
   guarded at 0x14148320D with ``_percent``'s message, then 4 bytes into
   ``+0x80`` with ``_gaugeTime``'s. AIEventTableInfo reads 8 bytes into
   ``+0x38`` guarded at 0x1414651F3 with ``_eventDelayType``'s message,
   then 1 byte into ``+0x41`` with ``_isSequencerInterruptEvent``'s.
2. The declared widths agree with those reads: u64 then u32, u64 then u8.
3. The values, which is what this file tests. They are checked on the
   installed game rather than a fixture because neither table has one.
"""
from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from cdumm.engine.schema_verify import verified_order

_SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def _order(cls: str) -> list[str]:
    over = json.loads((_SCHEMAS / "pabgb_type_overrides.json")
                      .read_text(encoding="utf-8-sig"))
    return over[cls]["_ordered_fields"]


def test_questgaugeinfo_reads_percent_before_gauge_time():
    o = _order("QuestGaugeInfo")
    assert o.index("_percent") < o.index("_gaugeTime")


def test_aieventtableinfo_reads_delay_type_before_the_interrupt_flag():
    o = _order("AIEventTableInfo")
    assert o.index("_eventDelayType") < o.index("_isSequencerInterruptEvent")


def test_the_loaded_schema_agrees_with_the_override_file():
    """The override is only worth anything if the walker actually uses it."""
    assert (verified_order("questgaugeinfo").index("_percent")
            < verified_order("questgaugeinfo").index("_gaugeTime"))
    assert (verified_order("aieventtableinfo").index("_eventDelayType")
            < verified_order("aieventtableinfo")
            .index("_isSequencerInterruptEvent"))


# ── against the installed game (skips without one) ───────────────────────

def _game_dir() -> Path | None:
    env = os.environ.get("CDUMM_GAME_DIR")
    if env and (Path(env) / "bin64").is_dir():
        return Path(env)
    for root in ("C:", "D:", "E:", "F:"):
        for lib in ("SteamLibrary", "Steam"):
            p = Path(f"{root}/{lib}/steamapps/common/Crimson Desert")
            if (p / "bin64").is_dir():
                return p
    return None


def _values(game: Path, table: str, wanted: set[str]) -> dict[str, list[int]]:
    from cdumm.engine.schema_verify import _consume_field_bytes, _payload_offset, _schema_in_order
    from cdumm.engine.v2_to_format3 import _load_vanilla_table
    from cdumm.semantic.parser import parse_pabgh_index

    body = _load_vanilla_table(game, table + ".pabgb")
    header = _load_vanilla_table(game, table + ".pabgh")
    schema = _schema_in_order(table, verified_order(table))
    key_size, offs = parse_pabgh_index(header, table)
    entries = sorted(offs.items(), key=lambda kv: kv[1])
    out: dict[str, list[int]] = {name: [] for name in wanted}
    for i, (_key, off0) in enumerate(entries):
        end = entries[i + 1][1] if i + 1 < len(entries) else len(body)
        po = _payload_offset(body, off0, key_size,
                             no_null_skip=schema.no_null_skip,
                             no_entry_header=schema.no_entry_header)
        if po is None:
            continue
        off = po
        for f in schema.fields:
            c = _consume_field_bytes(body, off, f, end)
            if c is None:
                break
            if f.name in wanted:
                out[f.name].append(
                    struct.unpack_from("<" + f.struct_fmt, body, off)[0])
            off += c
    return out


@pytest.mark.slow
def test_questgauge_values_are_coherent_on_the_installed_game():
    """``_percent`` tops out at 1000000 and ``_gaugeTime`` reads 259200.

    A fixed-point percent whose ceiling is 1,000,000, and a duration of
    exactly 72 hours. Under the old order ``_percent`` held
    1113255523123200, which is what a u64 read across the wrong boundary
    looks like.
    """
    game = _game_dir()
    if game is None:
        pytest.skip("no Crimson Desert install found (set CDUMM_GAME_DIR)")
    v = _values(game, "questgaugeinfo", {"_percent", "_gaugeTime"})
    assert v["_percent"], "no records decoded"
    assert max(v["_percent"]) == 1_000_000
    assert 259_200 in v["_gaugeTime"]
    assert max(v["_gaugeTime"]) <= 259_200


@pytest.mark.slow
def test_aievent_values_are_coherent_on_the_installed_game():
    """The boolean reads as a boolean and the enum stays small.

    Under the old order the boolean took a value of 2 and the enum took
    72057594037927936, which is 1 shifted into the top byte of a u64.
    """
    game = _game_dir()
    if game is None:
        pytest.skip("no Crimson Desert install found (set CDUMM_GAME_DIR)")
    v = _values(game, "aieventtableinfo",
                {"_isSequencerInterruptEvent", "_eventDelayType"})
    assert v["_isSequencerInterruptEvent"], "no records decoded"
    assert set(v["_isSequencerInterruptEvent"]) <= {0, 1}
    assert max(v["_eventDelayType"]) <= 2
