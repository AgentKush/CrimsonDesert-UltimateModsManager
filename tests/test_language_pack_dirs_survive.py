"""Crimson Desert 2.0 ("Enhanced") ships five optional voice packs
(German, French, Spanish, Brazilian Portuguese, Japanese). Steam
downloads the selected one into a reserved slot 0036-0040 -- the PAPGT
entries flagged is_optional=1. Every "0036+ is a mod dir" rule in CDUMM
therefore has to exempt these, or a rescan / health check deletes the
user's language and the game drops back to English (Nexus, Dante1963,
27 Aug 2026: "no longer have access to the new languages").

Covers: the language_pack_dirs helper, the pre-snapshot check (which
feeds an rmtree), and the two other consumers at source level.
"""
from __future__ import annotations

import struct
from pathlib import Path

from cdumm.archive.papgt_manager import language_pack_dirs
from cdumm.engine.snapshot_manager import SnapshotWorker

_OPTIONAL_FR = 0x00010001   # is_optional=1, single language bit (live 0037)
_VANILLA = 0x007FFF00


def _build_papgt(entries: list[tuple[str, int]]) -> bytes:
    st = bytearray(); offs = []
    for n, _f in entries:
        offs.append(len(st)); st += n.encode() + b"\x00"
    body = bytearray()
    for (n, f), o in zip(entries, offs):
        body += struct.pack("<III", f, o, 0)
    body += struct.pack("<I", len(st)) + st
    return bytes(b"\x01\x02\x03\x04" + b"\x00" * 4 + bytes([len(entries), 0xFF, 0xFF, 0xFF]) + body)


def _game(tmp_path: Path) -> Path:
    g = tmp_path / "game"
    (g / "meta").mkdir(parents=True)
    (g / "meta" / "0.papgt").write_bytes(_build_papgt(
        [("0000", _VANILLA), ("0035", _VANILLA),
         ("0036", 0x00008001), ("0037", _OPTIONAL_FR), ("0038", 0x00000401)]))
    for n in ("0000", "0035"):
        (g / n).mkdir(); (g / n / "0.pamt").write_bytes(b"\x00" * 16)
    # French voice pack downloaded by Steam into its reserved slot
    (g / "0037").mkdir(); (g / "0037" / "0.pamt").write_bytes(b"\x00" * 16)
    (g / "0037" / "0.paz").write_bytes(b"\x00" * 64)
    return g


def test_helper_recognises_steam_language_pack(tmp_path):
    g = _game(tmp_path)
    assert language_pack_dirs(g) == {"0037"}


def test_helper_ignores_dangling_optional_slots(tmp_path):
    g = _game(tmp_path)
    # 0036 / 0038 are optional in the PAPGT but not on disk
    assert "0036" not in language_pack_dirs(g)


def test_helper_excludes_cdumm_overlay_squatting_a_slot(tmp_path):
    g = _game(tmp_path)
    (g / "0037" / "_cdumm_overlay.marker").write_bytes(b"CDUMM overlay marker.")
    assert language_pack_dirs(g) == set()


def test_helper_excludes_real_mod_dirs(tmp_path):
    g = _game(tmp_path)
    (g / "0041").mkdir(); (g / "0041" / "0.pamt").write_bytes(b"\x00" * 16)
    assert language_pack_dirs(g) == {"0037"}


def test_pre_snapshot_keeps_language_pack_but_flags_mod_dir(tmp_path):
    g = _game(tmp_path)
    (g / "0041").mkdir(); (g / "0041" / "0.pamt").write_bytes(b"\x00" * 16)
    from cdumm.storage.database import Database
    db = Database(tmp_path / "cdumm.db")
    db.initialize()
    w = SnapshotWorker.__new__(SnapshotWorker)
    w._game_dir = g
    w._thread_db = db
    problems = w._check_pre_snapshot()
    mod_dir_problems = [p for p in problems if p.startswith("Mod directory")]
    assert any("0041/" in p for p in mod_dir_problems)
    assert not any("0037/" in p for p in mod_dir_problems)


def test_other_consumers_exempt_language_packs():
    """Source-level (repo convention): the startup health check and the
    Verify State worker both consult language_pack_dirs."""
    root = Path(__file__).parent.parent / "src" / "cdumm"
    fw = (root / "gui" / "fluent_window.py").read_text(encoding="utf-8")
    wp = (root / "worker_process.py").read_text(encoding="utf-8")
    hc = fw.split("Startup health check: game files dirty", 1)
    assert "language_pack_dirs" in hc[0][-3000:] and "_lang_dirs" in hc[1][:1500]
    verify = wp.split("def _run_verify", 1)[1].split("\ndef ", 1)[0]
    assert "language_pack_dirs" in verify and "not in _lang_dirs" in verify
