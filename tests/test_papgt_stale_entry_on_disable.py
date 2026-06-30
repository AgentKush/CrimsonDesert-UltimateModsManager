"""GitHub #225 (falobos76): disabling a mod that added a whole new PAZ
directory leaves a stale entry in meta/0.papgt, so Post-Apply
Verification reports "Missing directory NNNN" and the user has to
re-verify the game files.

Root cause (traced in apply_engine.py): a disabled mod's new files
(e.g. 0037/0.pamt, 0037/0.paz) are queued in ``deferred_file_deletions``
and deleted POST-commit. The Phase 4 PAPGT rebuild runs PRE-commit, when
those files are still on disk, so the rebuilt index keeps the 0037 entry.
Right after the commit the now-empty 0037 dir is removed, leaving
0.papgt pointing at a directory that no longer exists.

``exclude_dirs`` already drops whole-directory deletions
(``deferred_dir_deletions``) from the rebuild, but it never covered dirs
that get emptied by ``deferred_file_deletions``. The fix computes those
dirs (any whose 0.pamt index file is queued for deletion) and adds them
to ``exclude_dirs`` so the rebuilt index matches the post-commit
on-disk state.
"""
from __future__ import annotations

import struct
from pathlib import Path


def _build_papgt(entries: list[tuple[str, int]]) -> bytes:
    """Minimal PAPGT with the given (dir_name, pamt_hash) entries."""
    string_table = bytearray()
    offsets: list[int] = []
    for dir_name, _h in entries:
        offsets.append(len(string_table))
        string_table += dir_name.encode("ascii") + b"\x00"

    body = bytearray()
    for (dir_name, h), off in zip(entries, offsets):
        body += struct.pack("<III", 0x003FFF00, off, h)
    body += struct.pack("<I", len(string_table))
    body += string_table

    out = bytearray()
    out += b"\x01\x02\x03\x04"          # header meta 0:4
    out += b"\x00\x00\x00\x00"          # header meta 4:8 (hash, recomputed)
    out += bytes([len(entries), 0xFF, 0xFF, 0xFF])  # entry count at byte 8
    out += body
    return bytes(out)


def _dir_names(papgt: bytes) -> set[str]:
    entry_count = papgt[8]
    entry_start = 12
    str_off = entry_start + entry_count * 12 + 4
    names = set()
    for i in range(entry_count):
        pos = entry_start + i * 12
        name_off = struct.unpack_from("<I", papgt, pos + 4)[0]
        abs_off = str_off + name_off
        end = papgt.index(0, abs_off)
        names.add(papgt[abs_off:end].decode("ascii"))
    return names


# --- apply-engine helper: which dirs lose their index post-commit ----

def test_dirs_losing_pamt_identifies_disabled_mod_dirs():
    from cdumm.engine.apply_engine import _dirs_losing_pamt
    game = Path("D:/Game")
    deferred = [
        game / "0037" / "0.pamt", game / "0037" / "0.paz",
        game / "0038" / "0.pamt", game / "0038" / "0.paz",
    ]
    assert _dirs_losing_pamt(deferred) == {"0037", "0038"}


def test_dirs_losing_pamt_ignores_non_index_deletions():
    from cdumm.engine.apply_engine import _dirs_losing_pamt
    game = Path("D:/Game")
    # A .paz deleted without its .pamt does not empty the index entry,
    # and a plain reverted data file is not a directory index at all.
    assert _dirs_losing_pamt([game / "0040" / "0.paz"]) == set()
    assert _dirs_losing_pamt([game / "0005" / "some.bin"]) == set()
    assert _dirs_losing_pamt([]) == set()


# --- the rebuild mechanism the fix relies on -------------------------

def test_rebuild_drops_dir_whose_index_is_being_deleted(tmp_path: Path):
    """With 0037/0.pamt still on disk (deletion deferred to post-commit),
    a plain rebuild KEEPS the 0037 entry (this is the stale-entry source);
    excluding 0037 — what the #225 fix now does — drops it so the index
    matches the post-commit state."""
    from cdumm.archive.papgt_manager import PapgtManager

    game_dir = tmp_path / "game"
    (game_dir / "meta").mkdir(parents=True)
    (game_dir / "0001").mkdir()
    (game_dir / "0037").mkdir()
    (game_dir / "0001" / "0.pamt").write_bytes(b"PAMT" + b"\x00" * 8 + b"\xAA" * 64)
    (game_dir / "0037" / "0.pamt").write_bytes(b"PAMT" + b"\x00" * 8 + b"\xBB" * 64)

    papgt_base = _build_papgt([("0001", 0), ("0037", 0)])
    (game_dir / "meta" / "0.papgt").write_bytes(papgt_base)

    mgr = PapgtManager(game_dir)

    kept = _dir_names(mgr.rebuild())
    assert "0037" in kept, "pre-fix: 0037 survives because its 0.pamt is still on disk"

    dropped = _dir_names(mgr.rebuild(exclude_dirs={"0037"}))
    assert "0037" not in dropped, "fix: excluding the dir drops the stale entry"
    assert "0001" in dropped, "vanilla dir must remain"
