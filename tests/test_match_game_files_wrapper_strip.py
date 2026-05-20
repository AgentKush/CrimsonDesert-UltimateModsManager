"""GitHub #146 (Axlred): mesh mods that wrap their replacement files
in a gamedata/ folder (gamedata/character/model/.../foo.pac) failed
to import unless the user renamed gamedata/ to files/.

Root cause: _match_game_files built parts[i:] candidate slices and
broke on the FIRST hit, trying _GAME_FILE_RE before exhausting the
snapshot exact-match across all slices. The gamedata-prefixed
candidate matched the regex, so the file imported as a NEW file at
gamedata/character/... and the real vanilla character/... file was
never replaced.

Fix: two passes. Pass 1 tries the snapshot exact-match for every
slice; pass 2 is the regex fallback. The correctly-stripped slice
that names a real vanilla file always wins.
"""
from __future__ import annotations

from pathlib import Path

from cdumm.engine.import_handler import _match_game_files


class _FakeSnapshot:
    """Minimal SnapshotManager stand-in: a fixed set of known
    vanilla file paths."""

    def __init__(self, known: set[str]) -> None:
        self._known = known

    def get_file_hash(self, rel_path: str):
        return "deadbeef" if rel_path in self._known else None


def _write(root: Path, rel: str) -> None:
    p = root
    for piece in rel.split("/"):
        p = p / piece
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(rel.encode("utf-8"))


def test_gamedata_wrapper_strips_to_canonical_vanilla_path(tmp_path):
    """A mesh file shipped at gamedata/character/model/foo.pac must
    resolve to the vanilla character/model/foo.pac entry, not import
    as a new gamedata/-prefixed file."""
    canonical = "character/model/1_pc/foo/cd_phm_02_sword_0042.pac"
    snapshot = _FakeSnapshot({canonical})

    mod_dir = tmp_path / "mesh_mod"
    mod_dir.mkdir()
    _write(mod_dir, "gamedata/" + canonical)

    game_dir = tmp_path / "game"
    game_dir.mkdir()

    matches = _match_game_files(mod_dir, game_dir, snapshot)
    assert len(matches) == 1
    rel, src, is_new = matches[0]
    assert rel == canonical, (
        f"gamedata/ wrapper should be stripped to {canonical!r}, "
        f"got {rel!r}")
    assert is_new is False, "vanilla file replacement, not a new file"


def test_files_wrapper_strips_to_canonical_vanilla_path(tmp_path):
    """The same file under a files/ wrapper resolves identically —
    proves the fix is wrapper-name-agnostic, not a gamedata special
    case."""
    canonical = "character/model/1_pc/foo/cd_phm_02_sword_0042.pac"
    snapshot = _FakeSnapshot({canonical})

    mod_dir = tmp_path / "mesh_mod"
    mod_dir.mkdir()
    _write(mod_dir, "files/" + canonical)

    game_dir = tmp_path / "game"
    game_dir.mkdir()

    matches = _match_game_files(mod_dir, game_dir, snapshot)
    assert len(matches) == 1
    rel, _src, is_new = matches[0]
    assert rel == canonical
    assert is_new is False


def test_unwrapped_file_still_matches(tmp_path):
    """Backwards-compat: a file shipped directly at its canonical
    path (no wrapper) still resolves."""
    canonical = "character/model/1_pc/foo/cd_phm_02_sword_0042.pac"
    snapshot = _FakeSnapshot({canonical})

    mod_dir = tmp_path / "mesh_mod"
    mod_dir.mkdir()
    _write(mod_dir, canonical)

    game_dir = tmp_path / "game"
    game_dir.mkdir()

    matches = _match_game_files(mod_dir, game_dir, snapshot)
    assert len(matches) == 1
    rel, _src, is_new = matches[0]
    assert rel == canonical
    assert is_new is False
