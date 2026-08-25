"""Regression: post-apply PAPGT/PAMT verification hung the Apply dialog at
100% for several seconds on a real install.

Report 2026-08-26. `_post_apply_verify` runs synchronously on the GUI
thread by design -- it's the gate between "Apply reported success" and
"it's safe to Launch", so it can't be backgrounded without letting the
user launch the game before the install has been cleared. The actual fix
is to make the check itself fast: `_cached_pamt_hash` skips rehashing a
directory's PAMT when its (mtime, size) fingerprint hasn't changed since
the last run, persisted in a small on-disk cache. Only the directory (or
two) an Apply actually touches ever needs rehashing; everything else is
just a stat() call.
"""
from __future__ import annotations

import struct
from pathlib import Path

from cdumm.archive.hashlittle import compute_pamt_hash, compute_papgt_hash


def _build_papgt(entries: list[tuple[str, int]]) -> bytes:
    """entries: list of (dir_name, pamt_hash)."""
    names = [e[0].encode("ascii") + b"\x00" for e in entries]
    string_table = b"".join(names)
    name_offsets = []
    off = 0
    for n in names:
        name_offsets.append(off)
        off += len(n)

    body = bytearray()
    for (dir_name, pamt_hash), name_off in zip(entries, name_offsets):
        body += struct.pack("<III", 0x003FFF00, name_off, pamt_hash)
    body += struct.pack("<I", len(string_table))
    body += string_table

    header = bytearray(12)
    header[8] = len(entries)
    papgt_hash = compute_papgt_hash(bytes(header) + bytes(body))
    struct.pack_into("<I", header, 4, papgt_hash)
    return bytes(header) + bytes(body)


def _setup_game_dir(tmp_path: Path, *, corrupt: bool) -> Path:
    game_dir = tmp_path / "game"
    (game_dir / "meta").mkdir(parents=True)
    (game_dir / "0000").mkdir(parents=True)

    pamt_bytes = b"\x00" * 12 + b"some pamt content"
    (game_dir / "0000" / "0.pamt").write_bytes(pamt_bytes)
    real_hash = compute_pamt_hash(pamt_bytes)
    recorded_hash = real_hash + 1 if corrupt else real_hash

    papgt = _build_papgt([("0000", recorded_hash)])
    (game_dir / "meta" / "0.papgt").write_bytes(papgt)
    return game_dir


def test_clean_install_reports_no_issues(tmp_path, db):
    from cdumm.gui.fluent_window import _compute_post_apply_issues

    game_dir = _setup_game_dir(tmp_path, corrupt=False)
    assert _compute_post_apply_issues(game_dir, db.connection) == []


def test_pamt_hash_mismatch_is_reported(tmp_path, db):
    from cdumm.gui.fluent_window import _compute_post_apply_issues

    game_dir = _setup_game_dir(tmp_path, corrupt=True)
    issues = _compute_post_apply_issues(game_dir, db.connection)
    assert any("PAMT hash mismatch" in detail for _src, detail in issues)


# ── PAMT hash cache ─────────────────────────────────────────────────────

def test_cached_pamt_hash_reuses_entry_for_unchanged_file(tmp_path):
    from cdumm.gui.fluent_window import _cached_pamt_hash

    pamt_path = tmp_path / "0.pamt"
    pamt_path.write_bytes(b"\x00" * 12 + b"content")
    calls = []

    def counting_hasher(data: bytes) -> int:
        calls.append(data)
        return 42

    cache: dict = {}
    first = _cached_pamt_hash(pamt_path, cache, counting_hasher)
    second = _cached_pamt_hash(pamt_path, cache, counting_hasher)

    assert first == 42 and second == 42
    assert len(calls) == 1, (
        "second call must be served from cache -- the underlying hash "
        "function must not run again for an unchanged file")


def test_cached_pamt_hash_recomputes_when_file_changes(tmp_path):
    from cdumm.gui.fluent_window import _cached_pamt_hash

    pamt_path = tmp_path / "0.pamt"
    pamt_path.write_bytes(b"\x00" * 12 + b"content")
    cache: dict = {}
    _cached_pamt_hash(pamt_path, cache, compute_pamt_hash)

    pamt_path.write_bytes(b"\x00" * 12 + b"different content, different size")
    real = compute_pamt_hash(pamt_path.read_bytes())
    got = _cached_pamt_hash(pamt_path, cache, compute_pamt_hash)

    assert got == real, "a changed file must be rehashed, not served stale"


def test_compute_post_apply_issues_writes_a_persistent_cache(tmp_path, db):
    """End-to-end: a verify pass must leave an on-disk cache entry for
    the directory it hashed, so the NEXT pass (this run or a future
    CDUMM launch) can skip rehashing it if it hasn't changed."""
    from cdumm.engine.cdmods_paths import get_cdmods_root
    from cdumm.gui.fluent_window import (
        _compute_post_apply_issues,
        _load_pamt_hash_cache,
    )

    game_dir = _setup_game_dir(tmp_path, corrupt=False)
    assert _compute_post_apply_issues(game_dir, db.connection) == []

    cache_path = get_cdmods_root(None, game_dir) / ".post_apply_hash_cache.json"
    cache = _load_pamt_hash_cache(cache_path)
    pamt_path = game_dir / "0000" / "0.pamt"
    st = pamt_path.stat()

    entry = cache.get(str(pamt_path))
    assert entry is not None, "verify must record a cache entry for the hashed PAMT"
    assert entry[0] == st.st_mtime_ns and entry[1] == st.st_size
