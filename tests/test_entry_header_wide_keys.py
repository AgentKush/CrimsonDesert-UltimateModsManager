"""Entry ids are ``key_size`` bytes wide -- at every width, not just some.

``_parse_entry_header`` used to read the id as u32 unless ``key_size``
was 1 or 2. Two live tables have wider keys, and both were misread:
``characterappearanceindexinfo`` (8-byte keys, 8344 entries) and
``aieventtableinfo`` (a 12-byte composite key, 988 entries).

The reason that went unnoticed is the reason this whole class of bug
goes unnoticed: the wrong read SUCCEEDS. Truncating a 12-byte id to 4
bytes leaves the remaining 8 bytes to be consumed as ``name_len`` and
then as name bytes. ``name_len`` came out huge, the function hit its
own ``nlen > 500`` guard and returned early with an empty name -- which
reads exactly like "this table has no entry-name header", and was
written down as such. It is not true. Every one of those 9,332 entries
has a perfectly ordinary header; the name is simply empty.

The oracle is that the PABGH index already knows the key for each
offset, so the id read back out of the body has to equal it. That check
was available the whole time.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index

_FX = Path(__file__).resolve().parent / "fixtures" / "vanilla116"

# (table, key_size, entry count) -- the two tables the old code misread.
_WIDE = [
    ("characterappearanceindexinfo", 8, 8344),
    ("aieventtableinfo", 12, 988),
]


def _table(name: str):
    body = _FX / f"{name}.pabgb.zlib"
    if not body.exists():
        return None
    return (zlib.decompress(body.read_bytes()),
            zlib.decompress((_FX / f"{name}.pabgh.zlib").read_bytes()))


def _old_eid(data: bytes, offset: int, key_size: int) -> int:
    """The pre-fix id read, kept so the tests can show what it cost."""
    fmt = {1: "<B", 2: "<H"}.get(key_size, "<I")
    return struct.unpack_from(fmt, data, offset)[0]


# ── the oracle: the body's id must equal the index's key ────────────────

@pytest.mark.parametrize("name,key_size,count", _WIDE,
                         ids=[w[0] for w in _WIDE])
def test_every_entry_id_matches_its_index_key(name, key_size, count):
    t = _table(name)
    if t is None:
        pytest.skip(f"no {name} fixture")
    body, header = t
    ks, offsets = parse_pabgh_index(header, name)
    assert ks == key_size
    assert len(offsets) == count

    for key, off in offsets.items():
        eid, _name, _payload = _parse_entry_header(body, off, ks)
        assert eid == key, f"{name} entry at {off}: id {eid} != key {key}"


@pytest.mark.parametrize("name,key_size,count", _WIDE,
                         ids=[w[0] for w in _WIDE])
def test_the_old_u32_read_matched_nothing(name, key_size, count):
    """Pins WHY the widening is needed.

    If someone narrows the id back to u32, the test above starts failing
    -- but this one says out loud that the old behaviour was not merely
    imprecise, it matched zero entries out of thousands.
    """
    t = _table(name)
    if t is None:
        pytest.skip(f"no {name} fixture")
    body, header = t
    ks, offsets = parse_pabgh_index(header, name)
    matched = sum(1 for key, off in offsets.items()
                  if _old_eid(body, off, ks) == key)
    assert matched == 0


# ── the header is empty, not absent ─────────────────────────────────────

@pytest.mark.parametrize("name,key_size,count", _WIDE,
                         ids=[w[0] for w in _WIDE])
def test_the_name_header_is_present_and_empty(name, key_size, count):
    """These tables were described as carrying "no entry-name header".

    They carry the standard ``[id][u32 name_len][name][NUL]`` header
    with ``name_len == 0``. Recording that distinction matters: "absent"
    invites skipping the header entirely, which would put every payload
    read ``key_size + 5`` bytes early.
    """
    t = _table(name)
    if t is None:
        pytest.skip(f"no {name} fixture")
    body, header = t
    ks, offsets = parse_pabgh_index(header, name)

    for key, off in offsets.items():
        eid, entry_name, payload = _parse_entry_header(body, off, ks)
        assert eid == key
        assert entry_name == ""
        # name_len is really there, and really zero.
        assert struct.unpack_from("<I", body, off + ks)[0] == 0
        # ...followed by the usual NUL, which the payload starts after.
        assert body[off + ks + 4] == 0
        assert payload == off + ks + 5


# ── the widening must be inert everywhere else ──────────────────────────

@pytest.mark.parametrize("key_size", [1, 2, 4])
def test_narrow_widths_are_unchanged(key_size):
    """Widths 1 and 2 already used ``key_size``; for width 4 ``key_size``
    IS 4. So the change can only affect 8 and 12, and this pins that."""
    name = b"Some_Entry"
    entry_id = {1: 0x5A, 2: 0x1234, 4: 0x000F51F0}[key_size]
    blob = (entry_id.to_bytes(key_size, "little")
            + struct.pack("<I", len(name)) + name + b"\x00"
            + b"\xde\xad\xbe\xef")

    eid, parsed, payload = _parse_entry_header(blob, 0, key_size)
    assert eid == entry_id
    assert parsed == "Some_Entry"
    assert blob[payload:] == b"\xde\xad\xbe\xef"
    # identical to what the old narrow-width read produced
    assert _old_eid(blob, 0, key_size) == entry_id


@pytest.mark.parametrize("key_size", [8, 12])
def test_wide_widths_round_trip_a_synthetic_entry(key_size):
    """Runs with no fixture present, so the shape is pinned even in a
    checkout without game data."""
    entry_id = int.from_bytes(bytes(range(1, key_size + 1)), "little")
    blob = (entry_id.to_bytes(key_size, "little")
            + struct.pack("<I", 0) + b"\x00"
            + b"payload-bytes")

    eid, parsed, payload = _parse_entry_header(blob, 0, key_size)
    assert eid == entry_id
    assert parsed == ""
    assert payload == key_size + 5
    assert blob[payload:] == b"payload-bytes"


def test_a_truncated_header_still_refuses_rather_than_guessing():
    """The short-buffer guard has to scale with the wider id too."""
    eid, name, payload = _parse_entry_header(b"\x01\x02\x03", 0, 12)
    assert (eid, name, payload) == (0, "", 0)
