"""GitHub #383 (EnefFlow): Crimson Desert 2.0 ships PLACEHOLDER entries
0036-0040 in vanilla meta/0.papgt — optional language-pack slots whose
directories do not exist on disk. The pre-fix rebuild treated every digit
entry >= 0036 without a 0.pamt as a stale mod dir and removed it
("PAPGT rebuilt: 35 entries (4 removed, 0 added)" in EnefFlow's report),
and the overlay allocator squatted slot 0037, inheriting the
placeholder's is_optional=1 flags — the game then silently never mounted
the overlay: apply "succeeds", zero in-game effect.

Covers:
- rebuild keeps dangling placeholder entries (snapshot-independent rule)
- rebuild restores placeholders stripped by a pre-fix apply (heal)
- restored entry over an excluded squatted dir gets vanilla flags+hash
- overlay allocator skips PAPGT-reserved numbers -> 0041
"""
from __future__ import annotations

import struct
from pathlib import Path

from cdumm.archive.papgt_manager import (
    PapgtManager,
    read_papgt_entries,
    reserved_papgt_dir_numbers,
)

# Live 2.0 placeholder flags (build 24934353): is_optional=1 low byte,
# single-bit lang_type. Exact values measured from a clean install.
_PLACEHOLDERS = [
    ("0036", 0x00008001, 0x5BB5F227),
    ("0037", 0x00010001, 0x9E7C2F72),
    ("0038", 0x00000401, 0xBBDA9FF9),
    ("0039", 0x00080001, 0x65639AB3),
    ("0040", 0x00002001, 0x03FC3F20),
]


def _build_papgt(entries: list[tuple[str, int, int]]) -> bytes:
    """Minimal PAPGT with (dir_name, flags, pamt_hash) entries."""
    string_table = bytearray()
    offsets: list[int] = []
    for dir_name, _f, _h in entries:
        offsets.append(len(string_table))
        string_table += dir_name.encode("ascii") + b"\x00"

    body = bytearray()
    for (dir_name, f, h), off in zip(entries, offsets):
        body += struct.pack("<III", f, off, h)
    body += struct.pack("<I", len(string_table))
    body += string_table

    out = bytearray()
    out += b"\x01\x02\x03\x04"
    out += b"\x00\x00\x00\x00"
    out += bytes([len(entries), 0xFF, 0xFF, 0xFF])
    out += body
    return bytes(out)


def _entries_by_name(papgt_path: Path) -> dict[str, tuple[int, int]]:
    entries = read_papgt_entries(papgt_path)
    assert entries is not None
    return {n: (f, h) for n, f, h in entries}


def _make_game(tmp_path: Path, papgt_entries) -> Path:
    game = tmp_path / "game"
    (game / "meta").mkdir(parents=True)
    (game / "meta" / "0.papgt").write_bytes(_build_papgt(papgt_entries))
    for name, _f, _h in papgt_entries:
        if name.isdigit() and int(name) < 36:
            d = game / name
            d.mkdir()
            (d / "0.pamt").write_bytes(b"\x00" * 16)
    return game


_VANILLA_REAL = [("0000", 0x007FFF00, 0x11111111),
                 ("0035", 0x00200000, 0x22222222)]


def test_rebuild_keeps_dangling_placeholders_without_snapshot(tmp_path):
    """Dangling entries survive even with NO vanilla snapshot (the
    snapshot is stale/absent right after a game update)."""
    game = _make_game(tmp_path, _VANILLA_REAL + _PLACEHOLDERS)

    mgr = PapgtManager(game, vanilla_dir=None)
    rebuilt = mgr.rebuild(modified_pamts=None)
    (game / "meta" / "0.papgt").write_bytes(rebuilt)

    by_name = _entries_by_name(game / "meta" / "0.papgt")
    for name, flags, h in _PLACEHOLDERS:
        assert name in by_name, f"placeholder {name} removed"
        assert by_name[name] == (flags, h), f"placeholder {name} mutated"


def test_rebuild_restores_placeholders_stripped_by_prefix_apply(tmp_path):
    """Heal: base papgt already mutilated (35-entry state) — vanilla
    snapshot brings the placeholders back with vanilla flags+hash."""
    game = _make_game(tmp_path, _VANILLA_REAL)  # placeholders missing

    vanilla = tmp_path / "vanilla"
    (vanilla / "meta").mkdir(parents=True)
    (vanilla / "meta" / "0.papgt").write_bytes(
        _build_papgt(_VANILLA_REAL + _PLACEHOLDERS))

    mgr = PapgtManager(game, vanilla_dir=vanilla)
    rebuilt = mgr.rebuild(modified_pamts=None)
    (game / "meta" / "0.papgt").write_bytes(rebuilt)

    by_name = _entries_by_name(game / "meta" / "0.papgt")
    for name, flags, h in _PLACEHOLDERS:
        assert name in by_name, f"placeholder {name} not restored"
        assert by_name[name] == (flags, h)


def test_restored_entry_over_excluded_squatted_dir_gets_vanilla_form(tmp_path):
    """EnefFlow's exact state: CDUMM overlay squatted 0037 (dir with
    marker + pamt on disk, entry carries placeholder flags). Next apply
    excludes the stale overlay dir — the entry must come back as the
    vanilla placeholder, not keep the doomed pamt's hash."""
    game = _make_game(tmp_path, _VANILLA_REAL + _PLACEHOLDERS)
    squat = game / "0037"
    squat.mkdir()
    (squat / "0.pamt").write_bytes(b"\xAA" * 32)
    (squat / "_cdumm_overlay.marker").write_bytes(b"CDUMM overlay marker.")

    vanilla = tmp_path / "vanilla"
    (vanilla / "meta").mkdir(parents=True)
    (vanilla / "meta" / "0.papgt").write_bytes(
        _build_papgt(_VANILLA_REAL + _PLACEHOLDERS))

    mgr = PapgtManager(game, vanilla_dir=vanilla)
    rebuilt = mgr.rebuild(modified_pamts=None, exclude_dirs={"0037"})
    (game / "meta" / "0.papgt").write_bytes(rebuilt)

    by_name = _entries_by_name(game / "meta" / "0.papgt")
    assert by_name["0037"] == (0x00010001, 0x9E7C2F72)


def test_reserved_papgt_dir_numbers_sees_placeholders(tmp_path):
    game = _make_game(tmp_path, _VANILLA_REAL + _PLACEHOLDERS)
    reserved = reserved_papgt_dir_numbers(game)
    assert {36, 37, 38, 39, 40} <= reserved


def test_overlay_allocator_skips_reserved_numbers(tmp_path):
    """Allocator must return 0041 on a 2.0 install even though no
    0036+ directory exists on disk."""
    game = _make_game(tmp_path, _VANILLA_REAL + _PLACEHOLDERS)

    from cdumm.engine.apply_engine import ApplyWorker
    eng = ApplyWorker.__new__(ApplyWorker)  # allocator needs only these attrs
    eng._game_dir = game
    eng._vanilla_dir = tmp_path / "no-snapshot"
    assert eng._allocate_overlay_dir() == "0041"


def test_import_next_dir_skips_reserved_numbers(tmp_path):
    game = _make_game(tmp_path, _VANILLA_REAL + _PLACEHOLDERS)
    import cdumm.engine.import_handler as ih
    ih._assigned_dirs.clear()
    try:
        assert ih._next_paz_directory(game) == "0041"
    finally:
        ih._assigned_dirs.clear()
