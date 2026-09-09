"""Regression for the PAPGT integrity check in snapshot_manager.

GitHub context: the old `entry_count > 35` threshold was pinned to one
game version's vanilla count (33 entries pre-2.0). The Crimson Desert 2.0
update legitimately raised that to 39 (confirmed against a clean post-2.0
install with no CDUMM mod directories present), and the tight threshold
treated every clean 2.0 install as "modded", permanently blocking
Reset/Rescan since Steam Verify can't lower a legitimately-higher vanilla
count.

A wider hardcoded threshold (e.g. 100) has the same underlying flaw --
it's still an assumed count, just a bigger one, and would eventually
false-positive again on some future version's legitimate growth (or worse,
stay loose enough to miss real corruption on an OLDER version where the
true baseline is much lower). The check isn't a vanilla-count tracker any
more: papgt_manager.rebuild only ever adds a PAPGT entry for a directory
that has real content on disk, so "extra entries" and "a mod directory
exists on disk" are the same fact -- the mod-directory check right above
this one in the same function already catches that precisely, for any
entry count, on any game version. What's left here is PAPGT's own
structural validity (does the declared entry table actually fit in the
file), which is a real, version-independent corruption signal regardless
of how many entries a given game version's vanilla PAPGT legitimately has.
"""
import struct
from pathlib import Path

from cdumm.engine.snapshot_manager import SnapshotWorker
from cdumm.storage.database import Database


def _make_valid_papgt(entry_count: int) -> bytes:
    """A structurally well-formed PAPGT with ``entry_count`` entries, each
    naming a distinct (fake) directory, and a matching string table --
    exactly what the entry-count field promises actually follows it."""
    header = bytearray(12)
    header[8] = entry_count
    body = bytearray()
    names = [f"{i:04d}\x00".encode("ascii") for i in range(entry_count)]
    string_table = b"".join(names)
    off = 0
    for i, name in enumerate(names):
        body += struct.pack("<III", 0x003FFF00, off, 0)
        off += len(name)
    body += struct.pack("<I", len(string_table))
    body += string_table
    return bytes(header) + bytes(body)


def _worker(tmp_path: Path, db: Database, papgt_bytes: bytes) -> SnapshotWorker:
    game_dir = tmp_path / "game"
    (game_dir / "meta").mkdir(parents=True)
    (game_dir / "meta" / "0.papgt").write_bytes(papgt_bytes)
    worker = SnapshotWorker(game_dir, tmp_path / "unused.db")
    worker._thread_db = db
    return worker


def test_pre_2_0_install_is_not_flagged(tmp_path, db):
    worker = _worker(tmp_path, db, _make_valid_papgt(33))
    assert worker._check_pre_snapshot() == []


def test_clean_2_0_install_is_not_flagged(tmp_path, db):
    worker = _worker(tmp_path, db, _make_valid_papgt(39))
    assert worker._check_pre_snapshot() == []


def test_large_but_well_formed_entry_count_is_not_flagged(tmp_path, db):
    """A big entry count is not, by itself, evidence of anything -- only
    entries backed by an actual on-disk directory (check #1) are. A
    structurally valid PAPGT with many entries must pass regardless of
    the number, since there's no version-specific baseline to compare
    against any more."""
    worker = _worker(tmp_path, db, _make_valid_papgt(90))
    assert worker._check_pre_snapshot() == []


def test_corrupt_entry_count_is_flagged(tmp_path, db):
    """entry_count claims more entries than the file actually has room
    for -- real corruption, independent of any game version."""
    papgt = bytearray(_make_valid_papgt(5))
    papgt[8] = 200  # now claims 200 entries in a file sized for 5
    worker = _worker(tmp_path, db, bytes(papgt))
    problems = worker._check_pre_snapshot()
    assert any("PAPGT is corrupt" in p for p in problems)
