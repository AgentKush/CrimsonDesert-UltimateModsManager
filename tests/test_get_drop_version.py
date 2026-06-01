"""Pin CdummWindow._get_drop_version precedence.

The static helper is consulted by the post-import block (GitHub #187
version_picker) when the manifest path failed to produce a version.
Its own internal precedence matters because Format 3 mods often come
with multiple version hints in different files:

  1. modinfo.json on a folder drop -> highest trust
  2. JSON patch top-level "version" field
  3. Nexus filename slot (the "-MODID-V-V-TIMESTAMP" tail)
  4. Regex match for vN.N.N or vN inside the filename

These tests don't reach into the engine layer; they exercise the
static method directly with on-disk fixtures so a future refactor
that reorders the precedence is caught immediately.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdumm.gui.fluent_window import CdummWindow


def test_modinfo_json_wins_on_folder_drop(tmp_path):
    folder = tmp_path / "MyMod-123-1-1-1700000000"
    folder.mkdir()
    (folder / "modinfo.json").write_text(
        json.dumps({"name": "MyMod", "version": "2.5.0"}),
        encoding="utf-8")
    assert CdummWindow._get_drop_version(folder) == "2.5.0"


def test_nexus_filename_recovered_when_no_modinfo(tmp_path):
    """No modinfo.json on disk: Nexus filename's version slot wins."""
    folder = tmp_path / "MyMod-123-2-5-1700000000"
    folder.mkdir()
    # _parse_nexus_filename normalises 2-5 to 2.5
    assert CdummWindow._get_drop_version(folder) == "2.5"


def test_v_pattern_fallback_when_nexus_match_misses(tmp_path):
    """No modinfo, no Nexus shape, but the filename carries a vN.N.N
    style version tag. The regex fallback catches that."""
    folder = tmp_path / "MyMod_v3.2.1_release"
    folder.mkdir()
    assert CdummWindow._get_drop_version(folder) == "3.2.1"


def test_empty_when_nothing_parseable(tmp_path):
    folder = tmp_path / "JustAFolder"
    folder.mkdir()
    assert CdummWindow._get_drop_version(folder) == ""


def test_nexus_slot_wins_on_json_file_when_patches_absent(tmp_path):
    """A bare .json file whose contents are not a recognised Format 3
    patch falls through to the Nexus filename slot. This documents
    why the GitHub #187 fix lives in the version_picker layer, not
    here: _get_drop_version alone cannot rescue this case because
    detect_json_patch needs a real ``patches`` structure to fire."""
    json_file = tmp_path / "Easier_QTE_x2-664-1-1-1780000000.json"
    json_file.write_text(json.dumps({
        "name": "Easier QTE x2",
        "version": "1.2.1",  # ignored: not a recognised patch shape
    }), encoding="utf-8")
    # Nexus filename returns "1.1"; #187 lives in version_picker
    # which still preserves the manifest path via DB lookup.
    assert CdummWindow._get_drop_version(json_file) == "1.1"
