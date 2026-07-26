"""GitHub #190 part 2: equipslotinfo.pabgb ``entries[N].etl_hashes``.

Character Creator's Female Rapier and Shield Module sets the etl hash
lists of two records in equipslot entry 1 (3 -> 4 hashes and 5 -> 7
hashes). CDUMM rejected the mod as schema-less. The writer rewrites
the targeted records' hash lists, preserves every other byte
verbatim, and rebuilds the companion .pabgh offsets because the entry
grows.

Trust anchor: the record model (u32 etl_count + hashes + an opaque
per-record block, u16 unk + u32 count entry head, 20B-item footer +
0xb954d87c terminator) must round-trip every entry of the vanilla file
byte-identically.

**Two things were wrong here and this module now pins both.**

1. The opaque block size was hardcoded at 66 -- the value RE'd against
   CD 1.10. On 1.15 it is 63, so the walk desynced at the second record
   of every multi-record entry and the writer refused every intent.
   The mod applied nothing while reporting no skipped intents.

2. These tests could not have caught that, because they were gated on
   ``issue_repro/190/`` -- a gitignored directory that is not in the
   repo. All four skipped in CI and on every fresh clone. That is
   audit finding C7 again (see tests/fixture_loaders.py), so the fix is
   the same: commit the bytes. The two tables are 1.7 KB compressed.

The mod's own file is NOT committed (it is someone else's work); only
the two integer lists it sets, which are data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tests.fixture_loaders import has_vanilla115, load_vanilla115

_FIXTURE = "equipslotinfo.pabgb"
_needs_fixture = pytest.mark.skipif(
    not has_vanilla115(_FIXTURE),
    reason="vanilla115 equipslotinfo fixture absent")


@dataclass
class _Intent:
    entry: str
    key: int
    field: str
    op: str
    new: Any


def _body() -> bytes:
    return load_vanilla115("equipslotinfo.pabgb")


def _header() -> bytes:
    return load_vanilla115("equipslotinfo.pabgh")


def _mod_intents() -> list[_Intent]:
    """The Female Rapier and Shield Module's two intents.

    Transcribed from the mod's Format 3 JSON rather than read out of
    the mod archive, so the test carries no third-party file.
    """
    return [
        _Intent(entry="", key=1, field="entries[0].etl_hashes", op="set",
                new=[1584411264, 2594511993, 2327795645, 1187101662]),
        _Intent(entry="", key=1, field="entries[1].etl_hashes", op="set",
                new=[257028056, 1334259611, 1584411264, 2594511993,
                     2327795645, 517658843, 1187101662]),
    ]


def _entries(body, header):
    from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index
    ks, offs = parse_pabgh_index(header, "equipslotinfo")
    spans = sorted(offs.values()) + [len(body)]
    out = {}
    for key, off in offs.items():
        _, _, payload = _parse_entry_header(body, off, ks)
        end = spans[spans.index(off) + 1]
        out[key] = (off, payload, end)
    return out


# ── the drift itself ────────────────────────────────────────────────

@_needs_fixture
def test_block_size_is_derived_not_the_hardcoded_1_10_value():
    """The regression that made the mod a no-op.

    66 is what #190 measured on CD 1.10. The shipped table needs 63.
    Deriving it is what keeps the writer working across game patches.
    """
    from cdumm.engine.equipslotinfo_writer import (
        _FIXED_BLOCK, derive_fixed_block)
    assert derive_fixed_block(_body(), _header()) == 63
    assert _FIXED_BLOCK == 66, (
        "the legacy default should stay 66 so nothing silently changes "
        "for callers that don't derive")


@_needs_fixture
def test_the_legacy_block_size_desyncs_this_table():
    """Proof the fix is load-bearing: 66 must NOT parse this table.

    If a future refactor made 66 work again, deriving would be
    pointless -- and, worse, ambiguous.
    """
    from cdumm.engine.equipslotinfo_writer import (
        EquipslotWriteRefused, parse_entry_records)
    body, header = _body(), _header()
    _off, payload, end = _entries(body, header)[1]
    with pytest.raises(EquipslotWriteRefused):
        parse_entry_records(body, payload, end, 66)


@_needs_fixture
def test_derivation_is_unambiguous():
    """Exactly one candidate may round-trip every entry.

    ``derive_fixed_block`` raises when several qualify; this pins that
    the real table is not one of those cases, so the writer isn't
    quietly picking between equals.
    """
    from cdumm.engine.equipslotinfo_writer import (
        _FIXED_BLOCK_MAX, parse_entry_records, serialize_entry_payload)
    body, header = _body(), _header()
    ents = _entries(body, header)
    winners = []
    for block in range(_FIXED_BLOCK_MAX):
        try:
            for _key, (_o, payload, end) in ents.items():
                unk, recs, footer = parse_entry_records(
                    body, payload, end, block)
                assert serialize_entry_payload(
                    unk, recs, footer, block) == body[payload:end]
        except Exception:  # noqa: BLE001
            continue
        winners.append(block)
    assert winners == [63]


# ── the model ───────────────────────────────────────────────────────

@_needs_fixture
def test_every_vanilla_entry_round_trips_byte_exact():
    from cdumm.engine.equipslotinfo_writer import (
        derive_fixed_block, parse_entry_records, serialize_entry_payload)
    body, header = _body(), _header()
    block = derive_fixed_block(body, header)
    ents = _entries(body, header)
    assert len(ents) == 17
    for key, (_off, payload, end) in ents.items():
        unk, records, footer = parse_entry_records(
            body, payload, end, block)
        assert serialize_entry_payload(unk, records, footer, block) == \
            body[payload:end], f"entry {key} mis-round-tripped"


# ── the mod ─────────────────────────────────────────────────────────

@_needs_fixture
def test_female_rapier_module_applies_end_to_end():
    from cdumm.engine.equipslotinfo_writer import (
        build_equipslotinfo_changes, derive_fixed_block, parse_entry_records)
    from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index

    body, header = _body(), _header()
    block = derive_fixed_block(body, header)
    intents = _mod_intents()
    assert len(intents) == 2

    pabgb_changes, pabgh_change = build_equipslotinfo_changes(
        body, header, intents)
    assert len(pabgb_changes) == 1  # both records live in entry 1
    assert pabgh_change is not None  # entry grew -> offsets shift

    # Apply the replace and re-parse the patched entry.
    c = pabgb_changes[0]
    start = c["offset"]
    orig = bytes.fromhex(c["original"])
    patched_blob = bytes.fromhex(c["patched"])
    assert body[start:start + len(orig)] == orig
    patched = body[:start] + patched_blob + body[start + len(orig):]
    growth = len(patched) - len(body)
    assert growth == (len(intents[0].new) - 3 + len(intents[1].new) - 5) * 4

    new_header = bytes.fromhex(pabgh_change["patched"])
    ks, offs = parse_pabgh_index(new_header, "equipslotinfo")
    _, _, payload = _parse_entry_header(patched, offs[1], ks)
    spans = sorted(offs.values()) + [len(patched)]
    end = spans[spans.index(offs[1]) + 1]
    _unk, records, _footer = parse_entry_records(
        patched, payload, end, block)

    assert records[0][1] == [v & 0xFFFFFFFF for v in intents[0].new]
    assert records[1][1] == [v & 0xFFFFFFFF for v in intents[1].new]
    # untouched records keep their hash lists
    vk, voffs = parse_pabgh_index(header, "equipslotinfo")
    _, _, vpayload = _parse_entry_header(body, voffs[1], vk)
    vspans = sorted(voffs.values()) + [len(body)]
    vend = vspans[vspans.index(voffs[1]) + 1]
    _vu, vrecords, _vf = parse_entry_records(body, vpayload, vend, block)
    assert [r[1] for r in records[2:]] == [r[1] for r in vrecords[2:]]
    # opaque blocks preserved verbatim for ALL records
    assert [r[2] for r in records] == [r[2] for r in vrecords]

    # pabgh: every entry after entry 1 shifts by exactly +growth
    for key, voff in voffs.items():
        expect = voff + growth if voff > voffs[1] else voff
        assert offs[key] == expect, key


@_needs_fixture
def test_the_mod_actually_changes_bytes():
    """The user-visible symptom, stated as an assertion.

    Before the fix this produced zero changes -- CDUMM reported the
    intents as supported and the mod did nothing in game.
    """
    from cdumm.engine.equipslotinfo_writer import build_equipslotinfo_changes
    pabgb_changes, _pabgh = build_equipslotinfo_changes(
        _body(), _header(), _mod_intents())
    assert pabgb_changes, "the writer produced no change at all"
    assert any(c["original"] != c["patched"] for c in pabgb_changes), (
        "the writer produced a change that patches nothing")


@_needs_fixture
def test_out_of_range_record_index_refuses():
    from cdumm.engine.equipslotinfo_writer import (
        EquipslotWriteRefused, build_equipslotinfo_changes)
    bad = _Intent(entry="", key=1, field="entries[99].etl_hashes",
                  op="set", new=[1, 2, 3])
    with pytest.raises(EquipslotWriteRefused, match="out of range"):
        build_equipslotinfo_changes(_body(), _header(), [bad])


@_needs_fixture
def test_validator_accepts_the_indexed_field_path():
    from cdumm.engine.format3_handler import Format3Intent, validate_intents
    intents = [
        Format3Intent(entry=i.entry, key=i.key, field=i.field,
                      op=i.op, new=i.new)
        for i in _mod_intents()
    ]
    res = validate_intents("equipslotinfo.pabgb", intents)
    assert len(res.supported) == 2, [r for _i, r in res.skipped]
