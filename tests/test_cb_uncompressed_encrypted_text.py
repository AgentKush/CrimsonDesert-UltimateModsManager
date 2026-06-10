"""GitHub #199 (lupo1190): VAXIS Water Physics Overhaul "no longer
compatible" on CD 1.10.

Two game-side changes broke it: the material files moved from
material/dist/ to technique/ (the existing unique-basename fallback
already handles that), and the entries became ChaCha20-encrypted while
staying UNCOMPRESSED. The CB repack's encryption probe only ran for
compressed entries (the LZ4-decompress-fails trick), so the mod's
plaintext was stored where the game expects cipher bytes and the
materials silently failed to load in game.

_uncompressed_entry_is_encrypted_text closes the gap: raw slot bytes
that do not start like text but decrypt to text mark the entry
encrypted, so repack_entry_bytes re-encrypts the replacement.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cdumm.archive.paz_crypto import decrypt, encrypt
from cdumm.archive.paz_parse import PazEntry
from cdumm.engine.crimson_browser_handler import (
    _uncompressed_entry_is_encrypted_text,
)


def _entry(path: str, paz_file: Path, size: int) -> PazEntry:
    return PazEntry(path=path, paz_file=str(paz_file), offset=0,
                    comp_size=size, orig_size=size, flags=0, paz_index=0)


def test_encrypted_uncompressed_text_entry_is_detected(tmp_path: Path):
    plain = b"\xef\xbb\xbf<Technique Name=\"Water\"/>\n<Permutation/>"
    cipher = encrypt(plain, "water.material")
    assert not cipher.startswith((b"\xef\xbb\xbf", b"<"))
    paz = tmp_path / "0.paz"
    paz.write_bytes(cipher)
    entry = _entry("technique/water.material", paz, len(cipher))
    assert _uncompressed_entry_is_encrypted_text(paz, entry) is True
    # And the round trip that repack relies on holds.
    assert decrypt(cipher, "water.material") == plain


def test_plaintext_uncompressed_entry_is_not_flagged(tmp_path: Path):
    plain = b"<Technique Name=\"Water\"/>\n"
    paz = tmp_path / "0.paz"
    paz.write_bytes(plain)
    entry = _entry("technique/water.material", paz, len(plain))
    assert _uncompressed_entry_is_encrypted_text(paz, entry) is False


def test_binary_entry_is_not_flagged(tmp_path: Path):
    blob = bytes(range(256)) * 4  # binary, and its 'decryption' is junk
    paz = tmp_path / "0.paz"
    paz.write_bytes(blob)
    entry = _entry("model/thing.bin", paz, len(blob))
    assert _uncompressed_entry_is_encrypted_text(paz, entry) is False


def test_probe_errors_return_false(tmp_path: Path):
    entry = _entry("technique/water.material",
                   tmp_path / "missing.paz", 64)
    assert _uncompressed_entry_is_encrypted_text(
        tmp_path / "missing.paz", entry) is False


_GAME = Path(r"E:\SteamLibrary\steamapps\common\Crimson Desert")


@pytest.mark.skipif(not (_GAME / "0003" / "0.pamt").exists(),
                    reason="game install not present")
def test_live_water_material_detected_encrypted():
    """The real CD 1.10 technique/water.material entry must trip the
    probe (this is the exact slot the VAXIS mod replaces)."""
    from cdumm.archive.paz_parse import parse_pamt
    entries = parse_pamt(str(_GAME / "0003" / "0.pamt"),
                         str(_GAME / "0003"))
    e = next(x for x in entries if x.path == "technique/water.material")
    assert e.comp_size == e.orig_size, "expected uncompressed"
    assert not e.encrypted, "heuristic should miss .material (the bug)"
    assert _uncompressed_entry_is_encrypted_text(
        Path(e.paz_file), e) is True
