"""Re-dropping an updated variant pack must KEEP the variant the user
picked, not silently reset it to the alphabetically-first one.

GitHub #191 (falobos76). ``import_multi_variant`` carries the previously
enabled variant across a re-import, but its carryover branch runs only when
``initial_selection is None``. The engine-path caller passed a seed set
unconditionally, which made that branch unreachable: a user who selected
"Fat Stacks 999999x" and then re-dropped an updated pack was silently put
back on the 2x variant.

Seeding a default is right on a FIRST import and wrong on a re-import.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path


def _variant_json(mult: int) -> bytes:
    return json.dumps({
        "modinfo": {"name": f"Fat Stacks {mult}x"},
        "format": 3,
        "target": "iteminfo.pabgb",
        "intents": [
            {"entry": "Pyeonjeon_Arrow", "key": 2200,
             "field": "max_stack_count", "op": "set", "new": 1000 * mult},
            {"entry": "Mujeon_Arrow", "key": 2201,
             "field": "max_stack_count", "op": "set", "new": 1000 * mult},
        ],
    }).encode("utf-8")


def _fat_stacks_zip(tmp_path: Path, name: str) -> Path:
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as z:
        for m in (2, 3, 5, 10, 20, 50, 100, 999999):
            z.writestr(f"fat_stacks_{m}x.field.json", _variant_json(m))
    return zp


class _FakeSnapshot:
    def get_file_hash(self, p):
        return None

    def get_all_files(self):
        return []


def _enabled_names(db, mod_id: int) -> list[str]:
    row = db.connection.execute(
        "SELECT variants FROM mods WHERE id = ?", (mod_id,)).fetchone()
    assert row is not None and row[0]
    return sorted(
        v["filename"] for v in json.loads(row[0]) if v.get("enabled"))


def test_reimport_keeps_the_variant_the_user_selected(
        tmp_path, monkeypatch, db):
    from cdumm.engine import import_handler as ih
    monkeypatch.setattr(
        "cdumm.engine.version_detector.detect_game_version", lambda gd: None)

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    deltas_dir = tmp_path / "deltas"
    deltas_dir.mkdir()

    first = ih.import_from_zip(
        _fat_stacks_zip(tmp_path, "Fat Stacks - JSONv3 157.zip"),
        game_dir, db, _FakeSnapshot(), deltas_dir)
    assert first.error is None, f"first import must succeed: {first.error!r}"
    mod_id = first.mod_id
    assert mod_id is not None

    # The user opens the cog and switches to the 999999x variant.
    row = db.connection.execute(
        "SELECT variants FROM mods WHERE id = ?", (mod_id,)).fetchone()
    variants = json.loads(row[0])
    chosen = "fat_stacks_999999x.field.json"
    assert any(v["filename"] == chosen for v in variants), (
        "fixture must contain the 999999x variant")
    for v in variants:
        v["enabled"] = (v["filename"] == chosen)
    db.connection.execute(
        "UPDATE mods SET variants = ? WHERE id = ?",
        (json.dumps(variants), mod_id))
    db.connection.commit()
    assert _enabled_names(db, mod_id) == [chosen]

    # The author ships an update; the user re-drops the pack over the
    # existing mod.
    again = ih.import_from_zip(
        _fat_stacks_zip(tmp_path, "Fat Stacks - JSONv3 158.zip"),
        game_dir, db, _FakeSnapshot(), deltas_dir, existing_mod_id=mod_id)
    assert again.error is None, f"re-import must succeed: {again.error!r}"

    still = _enabled_names(db, again.mod_id or mod_id)
    assert still == [chosen], (
        f"re-import must keep the user's chosen variant {chosen!r}, "
        f"got {still!r} -- the pick was silently reset")
