"""GitHub #400 follow-up (woowoots): Inspect Mod reported "No recognized
mod format detected" for Dye Hard CD2.00.02, a Format 3 mod that imports
and applies fine.

The detector required a top-level ``target`` string plus ``intents``
list, which is only the SINGULAR dialect. Every multi-target export (the
plural ``targets`` shape current mods ship) fell through to the
unsupported branch, so the report called a working mod broken.

It now asks parse_format3_mod_targets, the same parser the importer
uses, so the two cannot drift apart again.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from cdumm.engine.mod_diagnostics import _parse_format3_pairs

PLURAL = {
    "modinfo": {"version": "CD2.00.02"},
    "format": 3, "format_minor": 1,
    "targets": [
        {"file": "npcinfo.pabgb", "intents": [
            {"entry": "n", "key": 1, "field": "dye_color_group_data_list",
             "op": "set", "new": []},
            {"entry": "n", "key": 1, "field": "dye_texture_set_data_list",
             "op": "set", "new": []},
        ]},
        {"file": "storeinfo.pabgb", "intents": [
            {"entry": "s", "key": 2, "field": "reset_day", "op": "set", "new": 1},
        ]},
    ],
}

SINGULAR = {
    "format": 3, "target": "iteminfo.pabgb",
    "intents": [{"entry": "i", "key": 3, "field": "price", "op": "set", "new": 1}],
}


def _zip(tmp_path: Path, name: str, payload) -> Path:
    z = tmp_path / "mod.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(name, json.dumps(payload))
    return z


def test_plural_targets_are_recognised(tmp_path):
    z = _zip(tmp_path, "DyeHard_CD2.00.02.json", PLURAL)
    pairs = _parse_format3_pairs("DyeHard_CD2.00.02.json", z)
    assert pairs is not None, "plural Format 3 must not read as unsupported"
    assert {t for t, _i in pairs} == {"npcinfo.pabgb", "storeinfo.pabgb"}
    assert sum(len(i) for _t, i in pairs) == 3


def test_singular_dialect_still_recognised(tmp_path):
    z = _zip(tmp_path, "m.json", SINGULAR)
    pairs = _parse_format3_pairs("m.json", z)
    assert pairs is not None
    assert [t for t, _i in pairs] == ["iteminfo.pabgb"]


def test_non_format3_json_returns_none(tmp_path):
    z = _zip(tmp_path, "m.json", {"patches": [{"offset": 0}]})
    assert _parse_format3_pairs("m.json", z) is None


def test_malformed_json_never_raises(tmp_path):
    z = tmp_path / "mod.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("m.json", "{not json")
    assert _parse_format3_pairs("m.json", z) is None
