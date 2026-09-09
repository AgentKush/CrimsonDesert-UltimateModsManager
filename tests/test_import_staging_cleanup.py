"""GitHub #371 (SyDant): crashed or force-killed imports leave extracted
archives under CDMods/_import_staging/ forever — 25+ GB accumulated.
Startup now sweeps the folder (safe: single-instance .gui_lock held)."""
from __future__ import annotations

from pathlib import Path

from cdumm.engine.import_handler import cleanup_import_staging, get_cdmods_root


def _staging(game_dir: Path) -> Path:
    return get_cdmods_root(None, game_dir) / "_import_staging"


def test_cleanup_removes_leftover_staging_dirs(tmp_path):
    game = tmp_path / "game"
    st = _staging(game)
    (st / "deadbeef" / "nested").mkdir(parents=True)
    (st / "deadbeef" / "nested" / "0.paz").write_bytes(b"x" * 4096)
    (st / "cafebabe").mkdir()
    (st / "cafebabe" / "mod.zip").write_bytes(b"y" * 1024)
    (st / "loose.tmp").write_bytes(b"z" * 100)

    freed = cleanup_import_staging(game)

    assert freed == 4096 + 1024 + 100
    assert st.is_dir()
    assert list(st.iterdir()) == []


def test_cleanup_noop_when_missing(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    assert cleanup_import_staging(game) == 0


def test_startup_calls_cleanup():
    """Source-level check (repo convention): _deferred_startup wires the
    sweep in."""
    src = (Path(__file__).parent.parent / "src" / "cdumm" / "gui" /
           "fluent_window.py").read_text(encoding="utf-8")
    body = src.split("def _deferred_startup", 1)[1]
    body = body.split("\n    def ", 1)[0]
    assert "cleanup_import_staging" in body
