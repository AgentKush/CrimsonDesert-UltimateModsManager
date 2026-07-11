"""Shared loaders for committed binary fixtures.

Audit finding C7 (2026-06-10): the byte-writer proof tests were gated
on machine-local paths (Temp extracts, gitignored issue_repro), so
the suite was green in CI while the round-trip proofs for the archive
writers never executed anywhere. The CD 1.10 vanilla extracts now
live zlib-compressed in tests/fixtures/vanilla110/ (10:1 on these
tables); the gitignored issue_repro copy is the fallback for files
not yet committed.
"""
from __future__ import annotations

import zlib
from functools import lru_cache
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent


@lru_cache(maxsize=8)
def load_vanilla110(name: str) -> bytes:
    """Load a CD 1.10 vanilla extract (e.g. "iteminfo.pabgb").

    Raises FileNotFoundError when neither the committed compressed
    fixture nor the issue_repro fallback has the file.
    """
    packed = _TESTS_DIR / "fixtures" / "vanilla110" / (name + ".zlib")
    if packed.exists():
        return zlib.decompress(packed.read_bytes())
    loose = _REPO_ROOT / "issue_repro" / "182" / "vanilla110" / name
    if loose.exists():
        return loose.read_bytes()
    raise FileNotFoundError(
        f"vanilla110 fixture {name!r} absent from tests/fixtures and "
        f"issue_repro")


def has_vanilla110(name: str) -> bool:
    try:
        load_vanilla110(name)
        return True
    except FileNotFoundError:
        return False


@lru_cache(maxsize=8)
def load_vanilla113(name: str) -> bytes:
    """Load a CD 1.13 vanilla extract (e.g. "iteminfo.pabgb").

    Same idea as :func:`load_vanilla110`, for the version the game
    actually ships. Needed because 1.13 restructured iteminfo's
    equipment records (SubItem tag 17 + _enchantDataList), so the 1.10
    fixture cannot exercise that code at all.

    This is finding C7 all over again, which is why it now has a
    committed fixture rather than an env var. The 1.13 tests were
    reading ``CDUMM_VANILLA_ITEMINFO_DIR`` or a ``tests/fixtures/iteminfo/``
    directory that has never existed in this repo -- so they skipped
    silently, in CI and on a fresh clone, and the strongest assertions in
    the suite (0 opaque records, the enchant-tier oracle, byte-exact
    re-serialization) guarded nothing. A test that can only run on one
    developer's machine is a test that does not exist.

    Compresses ~10:1 like the 1.10 tables (5.8 MB -> 549 KB).
    """
    packed = _TESTS_DIR / "fixtures" / "vanilla113" / (name + ".zlib")
    if packed.exists():
        return zlib.decompress(packed.read_bytes())
    raise FileNotFoundError(
        f"vanilla113 fixture {name!r} absent from tests/fixtures")


def has_vanilla113(name: str) -> bool:
    try:
        load_vanilla113(name)
        return True
    except FileNotFoundError:
        return False
