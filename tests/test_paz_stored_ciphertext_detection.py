"""A stored-uncompressed PAZ entry that is actually encrypted.

``PazEntry.encrypted`` is a filename heuristic covering the ui/ text
formats (.xml/.css/.html/.js). Every COMPRESSED path in
``decompress_entry`` self-corrects when it guesses wrong -- LZ4 fails,
so it retries via decrypt -- but the stored-uncompressed path had no
detection at all. A mod-authored overlay that encrypts anything outside
the heuristic (a ``.pabgb`` game table, say) came back as raw
ciphertext, which reads as a corrupt file rather than an encrypted one.

That is not hypothetical: Nexus mod 2511 ships its "Mod Folder Version"
exactly this way, and its ``statusinfo.pabgb`` extracted as 8185 of 8216
bytes of noise.

The detector decides by COMPARISON, never by a threshold on the raw
bytes. A stored entry may legitimately be high-entropy plaintext (an
already-compressed payload), which no property of the bytes in isolation
can tell apart from ciphertext. What separates them is what decryption
*does*: it reveals structure in real ciphertext and only adds noise to
anything else. Measured entropy change when decrypting:

    genuinely encrypted        -3.26 to -4.29 bits   (structure revealed)
    plaintext game table       +4.29 to +4.75 bits   (noise added)
    stored compressed payload  +0.002 to +0.014      (already noise)

So the rule is a drop of at least 1.0 bits/byte -- 3x margin on the true
side, and unreachable from the false side.
"""
from __future__ import annotations

import os
import struct

import pytest

from cdumm.archive.paz_crypto import encrypt
from cdumm.archive.paz_parse import PazEntry
from cdumm.engine.json_patch_handler import (
    _MIN_ENTROPY_DROP,
    _looks_encrypted,
    _shannon_entropy,
    decompress_entry,
)

#: A stand-in for a PABGB game table: mostly zeros with structured
#: runs, which is what gives real tables their 3.2-4.2 bits/byte and
#: 47-64% zero fraction. Synthesised rather than loaded so this test
#: is hermetic -- it exercises the detector's behaviour on structured
#: versus random input, which is the whole mechanism.
NAME = "statusinfo.pabgb"


def _table() -> bytes:
    out = bytearray()
    for i in range(2000):
        out += struct.pack("<I", 1000000 + i)
        out += b"\x00" * 12
        out += f"Record_{i:04d}".encode()
        out += b"\x00" * 8
    return bytes(out)


def _stored_entry(payload: bytes, path: str = f"gamedata/{NAME}") -> PazEntry:
    """A PAMT entry describing a stored (uncompressed) slot, exactly the
    shape mod 2511's overlay uses: comp_size == orig_size, type 0."""
    return PazEntry(path=path, paz_file="", offset=0,
                    comp_size=len(payload), orig_size=len(payload),
                    flags=0x00300000, paz_index=0)


def test_entropy_helper():
    assert _shannon_entropy(b"") == 0.0
    assert _shannon_entropy(b"\x00" * 1000) == 0.0
    assert _shannon_entropy(bytes(range(256)) * 8) == pytest.approx(8.0)


def test_stored_ciphertext_is_detected_and_opened():
    """The case that was returning garbage."""
    plain = _table()
    cipher = encrypt(plain, NAME)
    assert cipher != plain
    entry = _stored_entry(cipher)
    assert entry.encrypted is False          # heuristic says plaintext

    out = decompress_entry(cipher, entry)
    assert out == plain
    # and the correction is recorded, so a repack re-encrypts the slot
    assert entry._encrypted_override is True


def test_plaintext_table_is_returned_untouched():
    """The false positive that would corrupt a normal file."""
    plain = _table()
    entry = _stored_entry(plain)
    out = decompress_entry(plain, entry)
    assert out == plain
    assert entry._encrypted_override is None


def test_high_entropy_plaintext_is_not_decrypted():
    """The worst case for the detector: plaintext that is genuinely
    random-looking, which is what a stored already-compressed payload
    is. It is indistinguishable from ciphertext by entropy, zero
    fraction, or any other property of the bytes alone -- only the
    directional test separates them, and this is the case that proves a
    threshold would not."""
    blob = os.urandom(64 * 1024)
    assert _shannon_entropy(blob) > 7.9      # indistinguishable by entropy
    entry = _stored_entry(blob, path="gamedata/whatever.bin")
    out = decompress_entry(blob, entry)
    assert out == blob
    assert entry._encrypted_override is None


def test_detector_directly():
    plain = _table()
    assert _looks_encrypted(encrypt(plain, NAME), NAME) is True
    assert _looks_encrypted(plain, NAME) is False
    assert _looks_encrypted(os.urandom(64 * 1024), NAME) is False


def test_tiny_payloads_are_never_probed():
    """Too few bytes to measure entropy meaningfully."""
    assert _looks_encrypted(b"\x01\x02\x03", "x.bin") is False
    assert _looks_encrypted(b"", "x.bin") is False


def test_the_entropy_drop_margin_is_not_marginal():
    """Pin the measured separation: the true case clears the threshold
    several times over, and the false cases move the other way."""
    plain = _table()
    cipher = encrypt(plain, NAME)
    blob = os.urandom(64 * 1024)

    true_drop = _shannon_entropy(cipher) - _shannon_entropy(plain)
    plain_drop = (_shannon_entropy(plain)
                  - _shannon_entropy(encrypt(plain, NAME)))
    # Random plaintext stays random through a decrypt: no drop to find.
    blob_drop = (_shannon_entropy(blob)
                 - _shannon_entropy(encrypt(blob, NAME)))

    assert true_drop > 3 * _MIN_ENTROPY_DROP
    assert plain_drop < 0
    assert abs(blob_drop) < 0.1
