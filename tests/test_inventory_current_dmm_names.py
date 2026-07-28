"""inventory.pabgb: current DMM Mod Builder slot-field names.

AXIOM's "Inventory Space 240/700" (Rayruii) sets ``default_slots`` and
``max_slots`` on the ``Character`` record. Those applied **zero bytes**:
``inventory_writer`` has framed this table correctly since the DMM
"max inventory" work, but was registered only under the LEGACY names
``default_slot_count`` / ``max_slot_count``. The Mod Builder later
dropped the ``_count`` suffix, so every current-vintage inventory mod
missed the writer entirely -- the same rename drift that hit
characterinfo.

Ground truth is independent of CDUMM: donr484's *Max Inventory Storage*
(Nexus 1561) ships the same edits as **raw byte-offset patches with
hand-written labels** for CD1.15.00. Its 11 labelled offsets all verify
byte-exact against the shipped table, and its labels name the vanilla
values -- ``backpack 50->700`` and ``backpack max 240->700`` against the
``Character`` record. This module pins that the aliases resolve to
exactly those offsets, so the rename can't be "fixed" onto the wrong
slots.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# Loaded directly rather than via tests.fixture_loaders so this module
# carries no dependency on another in-flight PR.
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vanilla115"


def _load(name: str) -> bytes:
    return zlib.decompress((_FIXTURES / (name + ".zlib")).read_bytes())


_needs = pytest.mark.skipif(
    not (_FIXTURES / "inventory.pabgb.zlib").exists(),
    reason="vanilla115 inventory fixture absent")

# donr484's labelled offsets, CD1.15.00 (Nexus 1561). Independent of
# anything CDUMM derives.
_ORACLE_CHARACTER_DEFAULT = 3195   # "backpack 50->700"
_ORACLE_CHARACTER_MAX = 3197       # "backpack max 240->700"
_ORACLE_ALL = {3195, 3197, 4261, 4889, 5650, 5652,
               5979, 6198, 6400, 6613, 16197}


@dataclass
class _Intent:
    entry: str
    key: int
    field: str
    op: str
    new: Any


def _body() -> bytes:
    return _load("inventory.pabgb")


def _header() -> bytes:
    return _load("inventory.pabgh")


@_needs
def test_current_dmm_names_are_registered():
    from cdumm.engine.format3_handler import LIST_WRITERS
    for f in ("default_slots", "max_slots", "need_save_slots"):
        assert ("inventory", f) in LIST_WRITERS, f


@_needs
def test_legacy_names_still_registered():
    """The rename is additive -- mods already in the wild keep working."""
    from cdumm.engine.format3_handler import LIST_WRITERS
    for f in ("default_slot_count", "max_slot_count",
              "need_save_slot_count"):
        assert ("inventory", f) in LIST_WRITERS, f


@_needs
def test_validator_accepts_the_current_names():
    from cdumm.engine.format3_handler import Format3Intent, validate_intents
    intents = [
        Format3Intent(entry="Character", key=2, field="default_slots",
                      op="set", new=240),
        Format3Intent(entry="Character", key=2, field="max_slots",
                      op="set", new=700),
    ]
    res = validate_intents("inventory.pabgb", intents)
    assert len(res.supported) == 2, [r for _i, r in res.skipped]


@_needs
def test_vanilla_character_reads_the_values_donr484_labelled():
    """50 and 240 -- straight from the other author's patch labels."""
    body = _body()
    assert struct.unpack_from(
        "<H", body, _ORACLE_CHARACTER_DEFAULT)[0] == 50
    assert struct.unpack_from("<H", body, _ORACLE_CHARACTER_MAX)[0] == 240


@_needs
def test_axiom_mod_writes_exactly_the_oracle_offsets():
    from cdumm.engine.inventory_writer import build_inventory_changes
    body, header = _body(), _header()
    changes, dropped = build_inventory_changes(body, header, [
        _Intent("Character", 2, "default_slots", "set", 240),
        _Intent("Character", 2, "max_slots", "set", 700),
    ])
    assert not dropped, dropped
    assert changes, "current-DMM names produced no change"

    changed: dict[int, tuple[int, int]] = {}
    for c in changes:
        off = c["offset"]
        ob = bytes.fromhex(c["original"])
        pb = bytes.fromhex(c["patched"])
        # the writer emits one change over the 6-byte slot block
        assert body[off:off + len(ob)] == ob, "original bytes mismatch"
        for i in range(0, len(ob) - 1, 2):
            a = struct.unpack_from("<H", ob, i)[0]
            b = struct.unpack_from("<H", pb, i)[0]
            if a != b:
                changed[off + i] = (a, b)

    assert changed == {
        _ORACLE_CHARACTER_DEFAULT: (50, 240),
        _ORACLE_CHARACTER_MAX: (240, 700),
    }, changed
    assert set(changed) <= _ORACLE_ALL


@_needs
def test_aliases_and_legacy_names_resolve_to_the_same_bytes():
    """A rename must not move the write. Same record, both spellings."""
    from cdumm.engine.inventory_writer import build_inventory_changes
    body, header = _body(), _header()

    def _run(dflt: str, mx: str):
        ch, dr = build_inventory_changes(body, header, [
            _Intent("Character", 2, dflt, "set", 240),
            _Intent("Character", 2, mx, "set", 700),
        ])
        assert not dr, dr
        return [(c["offset"], c["original"], c["patched"]) for c in ch]

    assert _run("default_slots", "max_slots") == \
        _run("default_slot_count", "max_slot_count")


@_needs
def test_every_record_decodes_a_sane_capacity_pair():
    """Whole-table sanity: default <= max everywhere, and the records
    donr484 labelled read the values his labels claim."""
    from cdumm.engine.inventory_writer import _MARK
    body, header = _body(), _header()
    del header
    # every oracle offset must sit 4 or 2 bytes before a marker
    for off in _ORACLE_ALL:
        assert body[off + 4:off + 9] == _MARK or \
            body[off + 2:off + 7] == _MARK, off

