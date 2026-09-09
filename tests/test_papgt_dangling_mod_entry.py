"""GitHub #393 (delichandelarosse): after Rescan, every apply reported
'[PAPGT] Missing directory 0052' and the game refused to start.

Chain: the rescan path deletes leftover mod dirs (0052) before hashing
but never rebuilt meta/0.papgt, so the snapshot captured a PAPGT that
still listed 0052. The #383 rebuild then treated every snapshot entry
as vanilla and restored 0052 on each apply -- an index entry for a dir
that does not exist, which the game treats as a broken install.

Rules now: a dangling entry is kept/restored ONLY if its own flags say
is_optional (2.0 language-pack placeholders); the snapshot path strips
dangling non-optional entries before hashing.
"""
from __future__ import annotations

import struct
from pathlib import Path

from cdumm.archive.papgt_manager import (
    PapgtManager,
    read_papgt_entries,
    strip_dangling_entries,
)

_VAN = 0x007FFF00
_OPT = 0x00010001       # is_optional=1 (live 0037)
_MODDIR = 0x003FFF00    # what CDUMM writes for a mod dir (is_optional=0)


def _papgt(entries):
    st = bytearray(); offs = []
    for n, _f, _h in entries:
        offs.append(len(st)); st += n.encode() + b"\x00"
    body = bytearray()
    for (n, f, h), o in zip(entries, offs):
        body += struct.pack("<III", f, o, h)
    body += struct.pack("<I", len(st)) + st
    return bytes(b"\x01\x02\x03\x04" + b"\x00" * 4 + bytes([len(entries), 0xFF, 0xFF, 0xFF]) + body)


def _names(p: Path):
    return {n for n, _f, _h in read_papgt_entries(p)}


def _game(tmp_path: Path, with_0052_dir: bool) -> Path:
    g = tmp_path / "game"; (g / "meta").mkdir(parents=True)
    (g / "meta" / "0.papgt").write_bytes(_papgt(
        [("0000", _VAN, 1), ("0035", _VAN, 2), ("0037", _OPT, 3), ("0052", _MODDIR, 4)]))
    for n in ("0000", "0035"):
        (g / n).mkdir(); (g / n / "0.pamt").write_bytes(b"\x00" * 16)
    if with_0052_dir:
        (g / "0052").mkdir(); (g / "0052" / "0.pamt").write_bytes(b"\xAA" * 16)
    return g


def test_rebuild_drops_dangling_non_optional_entry_without_snapshot(tmp_path):
    g = _game(tmp_path, with_0052_dir=False)
    out = PapgtManager(g, None).rebuild()
    (g / "meta" / "0.papgt").write_bytes(out)
    names = _names(g / "meta" / "0.papgt")
    assert "0052" not in names
    assert "0037" in names          # optional placeholder survives


def test_rebuild_does_not_restore_dangling_entry_from_snapshot(tmp_path):
    """The exact #393 state: the vanilla snapshot itself lists 0052."""
    g = _game(tmp_path, with_0052_dir=False)
    van = tmp_path / "vanilla"; (van / "meta").mkdir(parents=True)
    (van / "meta" / "0.papgt").write_bytes((g / "meta" / "0.papgt").read_bytes())
    # live papgt already clean; snapshot is the poisoned one
    (g / "meta" / "0.papgt").write_bytes(_papgt(
        [("0000", _VAN, 1), ("0035", _VAN, 2), ("0037", _OPT, 3)]))
    out = PapgtManager(g, van).rebuild()
    (g / "meta" / "0.papgt").write_bytes(out)
    names = _names(g / "meta" / "0.papgt")
    assert "0052" not in names
    assert "0037" in names


def test_rebuild_keeps_snapshot_entry_whose_dir_exists(tmp_path):
    g = _game(tmp_path, with_0052_dir=True)
    van = tmp_path / "vanilla"; (van / "meta").mkdir(parents=True)
    (van / "meta" / "0.papgt").write_bytes((g / "meta" / "0.papgt").read_bytes())
    out = PapgtManager(g, van).rebuild()
    (g / "meta" / "0.papgt").write_bytes(out)
    assert "0052" in _names(g / "meta" / "0.papgt")


def test_strip_dangling_entries_before_snapshot(tmp_path):
    g = _game(tmp_path, with_0052_dir=False)
    assert strip_dangling_entries(g) == 1
    names = _names(g / "meta" / "0.papgt")
    assert names == {"0000", "0035", "0037"}
    assert strip_dangling_entries(g) == 0     # idempotent


def test_snapshot_worker_wires_strip():
    src = (Path(__file__).parent.parent / "src" / "cdumm" / "engine" /
           "snapshot_manager.py").read_text(encoding="utf-8")
    body = src.split("def _create_snapshot", 1)[1].split("\n    def ", 1)[0]
    assert "strip_dangling_entries" in body
    # must run AFTER mod-dir removal and BEFORE hashing
    assert body.index("Removed mod directory before snapshot") < body.index("strip_dangling_entries") < body.index("files_to_hash")
