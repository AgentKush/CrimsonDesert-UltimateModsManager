"""GitHub #241: compound archives (a standalone NNNN/0.paz model mod +
a sibling Format 3 JSON) must import BOTH halves.

Female Armor Module (Nexus 3029) ships, in one zip under a wrapper
folder: ``0036/0.paz`` (+ ``0.pamt``) — the armor-model swap — plus
``meta/0.papgt`` and a Format 3 ``...With Icons.json``. CDUMM's Format 3
branch used to ``rglob`` for the JSON, import only it, and return —
silently dropping the ``0036/`` model data. In-game the icons changed
but the armor models did not (woowoots, issue #241).

Fix: a wrapper-aware compound guard (``_bundled_paz_root``). When a
bundled PAZ-dir mod is present, the Format 3 JSON is deferred; the
PAZ-dir model mod imports as primary, then the JSON is re-imported as a
sibling via ``_import_sibling_format3``. Same shape as the #34 guard that
already protects the Format 2 branch.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

from cdumm.engine import import_handler as ih
from cdumm.engine.import_handler import ModImportResult, _bundled_paz_root


def _f3(target: str = "stringinfo.pabgb") -> str:
    return json.dumps({
        "modinfo": {"title": "FAM Icons"},
        "format": 3, "target": target,
        "intents": [{"entry": "X", "key": 1, "field": "y",
                     "op": "set", "new": 1}],
    })


def _make_game(tmp_path: Path) -> Path:
    g = tmp_path / "game"
    (g / "bin64").mkdir(parents=True)
    (g / "bin64" / "CrimsonDesert.exe").write_bytes(b"EXE")
    return g


def _write_compound_tree(base: Path) -> Path:
    """base/Female Armor Module/{0036/0.paz,0036/0.pamt,meta/0.papgt,icons.json}"""
    inner = base / "Female Armor Module"
    (inner / "0036").mkdir(parents=True)
    (inner / "0036" / "0.paz").write_bytes(b"PAZ" * 100)
    (inner / "0036" / "0.pamt").write_bytes(b"PAMT")
    (inner / "meta").mkdir()
    (inner / "meta" / "0.papgt").write_bytes(b"PAPGT")
    (inner / "Female Armor Module - With Icons.json").write_text(
        _f3(), encoding="utf-8")
    return inner


# ── _bundled_paz_root unit ──────────────────────────────────────────

def test_bundled_paz_root_sees_through_wrapper(tmp_path):
    inner = _write_compound_tree(tmp_path)
    # Extract root has only the wrapper as a child; the 0036/0.paz mod
    # is one level down. The guard must still find it.
    assert _bundled_paz_root(tmp_path) == inner


def test_bundled_paz_root_none_for_json_only(tmp_path):
    d = tmp_path / "mod"
    d.mkdir()
    (d / "icons.json").write_text(_f3(), encoding="utf-8")
    assert _bundled_paz_root(tmp_path) is None


def test_bundled_paz_root_direct_no_wrapper(tmp_path):
    (tmp_path / "0036").mkdir()
    (tmp_path / "0036" / "0.paz").write_bytes(b"x")
    assert _bundled_paz_root(tmp_path) == tmp_path


# ── functional spies ────────────────────────────────────────────────

def _spies(monkeypatch):
    calls = {"pef": 0, "f3": 0}

    def stub_pef(*a, **k):
        calls["pef"] += 1
        r = ModImportResult("Female Armor Module")
        r.changed_files = ["0036/0.paz"]
        r.mod_id = 1
        return r

    def stub_f3(*a, **k):
        calls["f3"] += 1
        return ModImportResult("FAM Icons")

    monkeypatch.setattr(ih, "_process_extracted_files", stub_pef)
    monkeypatch.setattr(ih, "import_from_natt_format_3", stub_f3)
    return calls


def test_compound_zip_imports_paz_model_and_f3_icons(tmp_path, monkeypatch):
    build = tmp_path / "build"
    inner = _write_compound_tree(build)
    z = tmp_path / "fam.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in inner.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(
                    Path("Female Armor Module") / p.relative_to(inner)))

    calls = _spies(monkeypatch)
    ih.import_from_zip(
        zip_path=z, game_dir=_make_game(tmp_path), db=MagicMock(),
        snapshot=MagicMock(), deltas_dir=tmp_path)

    assert calls["pef"] == 1, (
        "#241 regression: the PAZ-dir model mod was dropped — the Format 3 "
        "branch short-circuited instead of importing the 0036/ model data")
    assert calls["f3"] == 1, "icons JSON must still import as a sibling"


def test_compound_folder_imports_paz_model_and_f3_icons(
        tmp_path, monkeypatch):
    folder = tmp_path / "drop"
    _write_compound_tree(folder)  # folder/Female Armor Module/...

    calls = _spies(monkeypatch)
    ih.import_from_folder(
        folder_path=folder, game_dir=_make_game(tmp_path), db=MagicMock(),
        snapshot=MagicMock(), deltas_dir=tmp_path)

    assert calls["pef"] == 1, (
        "#241 regression (folder path): PAZ model data dropped")
    assert calls["f3"] == 1


def test_json_only_zip_still_short_circuits_to_format3(
        tmp_path, monkeypatch):
    """Guard: a zip with ONLY a Format 3 JSON (no PAZ dir) must still
    route straight to the Format 3 handler and NOT reach the PAZ path.
    The compound fix is additive, not a behaviour change for the common
    single-JSON case."""
    p = tmp_path / "solo.json"
    p.write_text(_f3(), encoding="utf-8")
    z = tmp_path / "solo.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(p, arcname="solo.json")

    calls = _spies(monkeypatch)
    ih.import_from_zip(
        zip_path=z, game_dir=_make_game(tmp_path), db=MagicMock(),
        snapshot=MagicMock(), deltas_dir=tmp_path)

    assert calls["f3"] == 1
    assert calls["pef"] == 0, (
        "a JSON-only Format 3 zip must not fall through to the PAZ path")
