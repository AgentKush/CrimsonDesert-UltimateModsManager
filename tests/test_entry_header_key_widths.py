"""A 1-byte entry key was being read as a u32, and it cost a whole table.

``_parse_entry_header`` chose the entry-id width with
``"<H" if key_size == 2 else "<I"``. Five live tables have 1-byte keys,
so their ids were read four bytes wide — swallowing three bytes of the
entry name and putting every field after it at the wrong offset.

``mercenaryinfo`` is the one that shows the damage plainly. It was
recorded as "memory-order misaligned", and the evidence was that it read
ASCII ``cenary`` where a name should be. ``Mercenary_Main`` minus
``cenary`` is exactly the three bytes an over-wide id read swallows. The
field order was never the problem.

The mapping deliberately covers 1 and 2 only. Wider keys (8, and a
12-byte composite) belong to tables with no entry-name header at all, so
there is nothing to read correctly there and nothing to check an answer
against — changing them would be a guess. Verified inert: 21,484 entry
headers across a live install decode byte-for-byte as before.
"""
from __future__ import annotations

import struct

import pytest

from cdumm.semantic.parser import _parse_entry_header

NAME = b"Mercenary_Main"


def _entry(key: int, name: bytes, key_size: int, payload: bytes = b"") -> bytes:
    """One entry: [id][u32 name_len][name][NUL][payload]."""
    return (key.to_bytes(key_size, "little")
            + struct.pack("<I", len(name)) + name + b"\x00" + payload)


@pytest.mark.parametrize("key_size", [1, 2, 4])
def test_the_entry_id_is_read_at_the_index_key_width(key_size):
    body = _entry(64, NAME, key_size, payload=b"\x99" * 8)
    eid, name, payload = _parse_entry_header(body, 0, key_size)
    assert eid == 64
    assert name == NAME.decode()
    assert payload == key_size + 4 + len(NAME) + 1
    assert body[payload:payload + 2] == b"\x99\x99"


def test_the_mercenaryinfo_symptom():
    """The exact misread that got a table written off.

    A u8 id read as u32 eats three name bytes, so ``Mercenary_Main``
    starts reading as ``cenary``. That is what the old note recorded as
    evidence of a scrambled field order.
    """
    body = _entry(64, NAME, key_size=1)

    # what the old width did
    wrong_len = struct.unpack_from("<I", body, 4)[0]
    assert body[8:8 + 6] == b"cenary", "expected the three-byte swallow"
    assert wrong_len != len(NAME)

    # what it does now
    eid, name, _payload = _parse_entry_header(body, 0, 1)
    assert (eid, name) == (64, "Mercenary_Main")


@pytest.mark.parametrize("key_size", [1, 2, 4, 8, 12, 16])
def test_the_id_is_always_key_size_bytes_wide(key_size):
    """No width is special.

    This test used to assert the opposite for widths above 4 — that they
    "must read the id as a u32 exactly as before", because those tables
    "have no name header, so a different answer would be an unverifiable
    guess". Both halves of that were wrong, and it is worth keeping the
    correction visible rather than quietly editing it away:

    * There was an oracle all along. The PABGH index supplies the key
      for each offset, so the id read from the body must equal it. At
      full width that holds for 8344 of 8344 ``characterappearance-
      indexinfo`` entries and 988 of 988 ``aieventtableinfo`` entries;
      as u32 it held for none of them.
    * The name header is not missing, just empty (``name_len == 0``).
      It only looked missing because the truncated id spilled into the
      ``name_len`` field and tripped the sanity guard.

    See ``test_entry_header_wide_keys.py`` for the live-data proof.
    """
    body = bytes(range(1, 33))
    eid, _name, _pay = _parse_entry_header(body, 0, key_size)
    assert eid == int.from_bytes(body[:key_size], "little")


def test_format3_accepts_one_byte_keys():
    """The other half of the refusal: even read correctly, Format 3
    dropped these tables on the key width alone."""
    import inspect

    from cdumm.engine import format3_apply

    src = inspect.getsource(format3_apply)
    assert "key_size not in (1, 2, 4)" in src, (
        "the H2 guard should accept 1-byte keys now that their entry "
        "headers parse")


def test_a_truncated_entry_is_still_refused():
    """Widening the width must not widen what counts as readable."""
    assert _parse_entry_header(b"\x40\x02", 0, 1) == (0, "", 0)
