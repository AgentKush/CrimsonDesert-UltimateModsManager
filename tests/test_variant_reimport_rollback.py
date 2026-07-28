"""A failed variant re-import must not destroy the installed mod.

GitHub #314. ``import_multi_variant`` used to delete the mod's deltas and
``rmtree`` its directory and only THEN start copying the new files in. A
disk-full, a locked file, an antivirus hold on a freshly written file or a
permissions blip between the two left the user's mod deleted with nothing
to put back. The caller catches broadly and reports "couldn't import", so
from the user's side it read as though nothing had happened while their
installed mod was actually gone.

#303 widened the exposure by routing zip / 7z / CLI imports through this
function, and re-import is the ordinary way to update a mod, so it runs
often.

Each test below fails the import at a different point and asserts the same
thing: the previously installed files are still there afterwards, and the
database still describes them.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cdumm.engine import variant_handler as vh


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE mods (id INTEGER PRIMARY KEY, name TEXT, "
        "mod_type TEXT, priority INTEGER, author TEXT, version TEXT, "
        "description TEXT, game_version_hash TEXT, configurable INTEGER, "
        "json_source TEXT, variants TEXT, enabled INTEGER DEFAULT 1, "
        "last_apply_skipped_count INTEGER DEFAULT 0, "
        "last_apply_skip_summary TEXT)")
    conn.execute(
        "CREATE TABLE mod_deltas (mod_id INTEGER, game_file TEXT)")
    conn.commit()
    return type("DB", (), {"connection": conn})()


def _preset(tmp: Path, name: str, patches: list | None = None):
    p = tmp / name
    doc = {"format": 3, "name": name, "patches": patches or []}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p, doc


@pytest.fixture
def installed(tmp_path, db):
    """A variant mod already on disk, as a first import would leave it."""
    src = tmp_path / "src"
    src.mkdir()
    mods_dir = tmp_path / "mods"
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    presets = [_preset(src, "alpha.json"), _preset(src, "beta.json")]

    res = vh.import_multi_variant(
        presets, src, game_dir, mods_dir, db,
        initial_selection={presets[0][0]})
    assert res is not None
    return {
        "mod_id": res["mod_id"],
        "mods_dir": mods_dir,
        "game_dir": game_dir,
        "src": src,
        "presets": presets,
        "json_source": db.connection.execute(
            "SELECT json_source FROM mods WHERE id = ?",
            (res["mod_id"],)).fetchone()[0],
    }


def _snapshot(mod_dir: Path) -> dict[str, bytes]:
    return {str(p.relative_to(mod_dir)): p.read_bytes()
            for p in sorted(mod_dir.rglob("*")) if p.is_file()}


def test_first_import_lands(installed):
    mod_dir = installed["mods_dir"] / str(installed["mod_id"])
    files = _snapshot(mod_dir)
    assert "variants\\alpha.json" in files or "variants/alpha.json" in files
    assert any(f.endswith("merged.json") for f in files)


def test_a_failed_copy_leaves_the_installed_mod_untouched(
        installed, db, monkeypatch):
    """The headline case: staging fails, so the old files are never
    touched in the first place."""
    mod_dir = installed["mods_dir"] / str(installed["mod_id"])
    before = _snapshot(mod_dir)
    assert before

    def boom(_presets, _dir):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(vh, "copy_variants_to_mod_dir", boom)

    newer = [_preset(installed["src"], "alpha.json", [{"x": 1}]),
             _preset(installed["src"], "beta.json")]
    with pytest.raises(OSError):
        vh.import_multi_variant(
            newer, installed["src"], installed["game_dir"],
            installed["mods_dir"], db,
            existing_mod_id=installed["mod_id"])

    assert _snapshot(mod_dir) == before, "installed files must survive"
    row = db.connection.execute(
        "SELECT json_source FROM mods WHERE id = ?",
        (installed["mod_id"],)).fetchone()
    assert row[0] == installed["json_source"], "db must still point at them"
    assert db.connection.execute(
        "SELECT COUNT(*) FROM mod_deltas").fetchone()[0] == 0


def test_a_silently_incomplete_copy_is_caught_before_the_swap(
        installed, db, monkeypatch):
    """copy_variants_to_mod_dir logs and continues when a copy fails, so
    returning is not evidence the files arrived. Staging is verified."""
    mod_dir = installed["mods_dir"] / str(installed["mod_id"])
    before = _snapshot(mod_dir)

    real = vh.copy_variants_to_mod_dir

    def only_the_first(presets, staged):
        out = real(presets[:1], staged)     # second variant never lands
        return out

    monkeypatch.setattr(vh, "copy_variants_to_mod_dir", only_the_first)

    newer = [_preset(installed["src"], "alpha.json"),
             _preset(installed["src"], "beta.json")]
    with pytest.raises(vh.VariantStagingError) as exc:
        vh.import_multi_variant(
            newer, installed["src"], installed["game_dir"],
            installed["mods_dir"], db,
            existing_mod_id=installed["mod_id"])
    assert "beta.json" in str(exc.value)
    assert _snapshot(mod_dir) == before


def test_a_failure_during_the_swap_restores_the_previous_directory(
        installed, db, monkeypatch):
    """The subtle one. The old directory has already been renamed aside
    when the second rename fails, so 'nothing was touched' is false and
    the backup must be put back rather than cleaned up."""
    import os as _os

    mod_dir = installed["mods_dir"] / str(installed["mod_id"])
    before = _snapshot(mod_dir)

    real_replace = _os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:          # the staging -> mod_dir rename
            raise PermissionError("file in use by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(vh.os, "replace", flaky)

    newer = [_preset(installed["src"], "alpha.json"),
             _preset(installed["src"], "beta.json")]
    with pytest.raises(PermissionError):
        vh.import_multi_variant(
            newer, installed["src"], installed["game_dir"],
            installed["mods_dir"], db,
            existing_mod_id=installed["mod_id"])

    assert mod_dir.is_dir(), "the mod directory must exist again"
    assert _snapshot(mod_dir) == before
    row = db.connection.execute(
        "SELECT json_source FROM mods WHERE id = ?",
        (installed["mod_id"],)).fetchone()
    assert row[0] == installed["json_source"]


def test_no_staging_or_backup_directories_are_left_behind(installed, db):
    """A successful re-import cleans up after itself."""
    newer = [_preset(installed["src"], "alpha.json", [{"x": 2}]),
             _preset(installed["src"], "beta.json")]
    res = vh.import_multi_variant(
        newer, installed["src"], installed["game_dir"],
        installed["mods_dir"], db,
        existing_mod_id=installed["mod_id"])
    assert res is not None
    leftovers = [p.name for p in installed["mods_dir"].iterdir()
                 if p.name.startswith(".")]
    assert not leftovers, leftovers


def test_a_successful_reimport_replaces_the_content(installed, db):
    mod_dir = installed["mods_dir"] / str(installed["mod_id"])
    (mod_dir / "variants" / "stale.json").write_text("{}", encoding="utf-8")

    newer = [_preset(installed["src"], "alpha.json", [{"x": 3}])]
    res = vh.import_multi_variant(
        newer, installed["src"], installed["game_dir"],
        installed["mods_dir"], db,
        existing_mod_id=installed["mod_id"])

    assert res["mod_id"] == installed["mod_id"]
    assert not (mod_dir / "variants" / "stale.json").exists(), (
        "the swap replaces the directory rather than merging into it")
    alpha = json.loads(
        (mod_dir / "variants" / "alpha.json").read_text(encoding="utf-8"))
    assert alpha["patches"] == [{"x": 3}]


def test_failures_are_reported_rather_than_swallowed(
        installed, db, monkeypatch):
    """import_multi_variant must raise so the caller can tell the user
    something went wrong; returning None here would read as 'nothing
    happened' while a re-import had actually been attempted."""
    def boom(_presets, _dir):
        raise OSError("nope")

    monkeypatch.setattr(vh, "copy_variants_to_mod_dir", boom)
    with pytest.raises(OSError):
        vh.import_multi_variant(
            [_preset(installed["src"], "alpha.json")], installed["src"],
            installed["game_dir"], installed["mods_dir"], db,
            existing_mod_id=installed["mod_id"])
