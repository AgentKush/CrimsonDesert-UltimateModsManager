"""Variant packs that ship a top-level ``mod.json`` alongside multiple
sibling NNNN/0.paz subfolders must surface every subfolder as a
distinct variant.

Bug 2026-05-09 (Democles85, GitHub #81 follow-up): Character Creator
mod 837 ships ``CharacterCreator/mod.json`` plus 6 sibling subdirs
(GoblinFemale, GoblinMale, HumanFemale, HumanMale, OrcFemale,
OrcMale), each carrying its own ``0036/0.paz``. The author intends
one body-type subfolder per game session.

CDUMM's ``_check_candidate`` matched the top-level mod.json via
Pattern 2 (mod.json + game files at root), returned a single
"Character Creator" candidate, and ``_walk`` stopped there without
recursing into the body-type subfolders. The variant picker never
fired because the detector saw only one candidate. Symptom:
"I only see FemaleAnimations and the ASI mod, without the popup
showing for selecting which body type."

Fix: surface each NNNN/0.paz-bearing sibling as its own variant
when this layout is detected.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _make_variant_pack(tmp_path: Path) -> Path:
    """Mimic the Character Creator 837 layout in miniature."""
    root = tmp_path / "CharacterCreator"
    root.mkdir()
    (root / "mod.json").write_text(
        '{"modinfo": {"title": "Character Creator", "version": "5.6"}}',
        encoding="utf-8")
    (root / "FemaleAnimations.json").write_bytes(b"{}")
    (root / "CharacterCreatorHead.asi").write_bytes(b"\x00")
    for variant in ("HumanFemale", "HumanMale", "GoblinFemale",
                    "GoblinMale", "OrcFemale", "OrcMale"):
        paz_dir = root / variant / "0036"
        paz_dir.mkdir(parents=True)
        (paz_dir / "0.paz").write_bytes(b"\x00")
        (paz_dir / "0.pamt").write_bytes(b"\x00")
    return tmp_path


def test_variants_surface_one_per_body_type(tmp_path):
    """6 sibling subfolders each with NNNN/0.paz under one mod.json
    must produce 6 candidates from ``find_loose_file_variants``."""
    from cdumm.engine.import_handler import find_loose_file_variants

    _make_variant_pack(tmp_path)
    variants = find_loose_file_variants(tmp_path)

    ids = sorted(v["id"] for v in variants)
    expected = sorted([
        "HumanFemale", "HumanMale", "GoblinFemale",
        "GoblinMale", "OrcFemale", "OrcMale",
    ])
    # Each variant id must contain the body-type token so the picker
    # UI can show meaningful labels. Allow either the bare body-type
    # name or "<title> - <body type>" composition.
    assert len(variants) == 6, (
        f"expected 6 body-type variants, got {len(variants)}: {ids}"
    )
    bodytype_hits = sum(
        1 for v in variants
        if any(bt in v["id"] for bt in expected)
    )
    assert bodytype_hits == 6, (
        f"every variant must surface its body-type name in the id; "
        f"got: {ids}"
    )


def test_each_variant_points_at_its_own_subfolder(tmp_path):
    """``_base_dir`` of each variant must be the body-type subfolder,
    not the parent. Otherwise the importer would pull in every
    body type's PAZ into one mod."""
    from cdumm.engine.import_handler import find_loose_file_variants

    pack = _make_variant_pack(tmp_path)
    variants = find_loose_file_variants(pack)

    bases = [Path(v["_base_dir"]).name for v in variants]
    expected = {"HumanFemale", "HumanMale", "GoblinFemale",
                "GoblinMale", "OrcFemale", "OrcMale"}
    assert set(bases) == expected, (
        f"each variant's _base_dir must be the body-type subfolder; "
        f"got: {bases}"
    )


def test_normal_single_mod_still_returns_one_variant(tmp_path):
    """Sanity: a normal mod.json + files/ layout (Pattern 1) must
    still return exactly one candidate, not get confused by the
    new variant pattern."""
    from cdumm.engine.import_handler import find_loose_file_variants

    root = tmp_path / "NormalMod"
    root.mkdir()
    (root / "mod.json").write_text(
        '{"modinfo": {"title": "Normal Mod"}}', encoding="utf-8")
    (root / "files").mkdir()
    (root / "files" / "0008").mkdir()
    (root / "files" / "0008" / "0.paz").write_bytes(b"\x00")

    variants = find_loose_file_variants(tmp_path)
    assert len(variants) == 1, (
        f"normal Pattern 1 mod must return one candidate; got "
        f"{len(variants)}: {[v['id'] for v in variants]}"
    )


def _make_pamt_only_variant_pack(tmp_path: Path) -> Path:
    """Character Creator 837 as shipped in v6.3+ (GitHub #189).

    The author switched each body-type folder from a packed ``0.paz``
    to a ``0.pamt`` index plus ``_directory_table.bin`` /
    ``_file_names.bin`` and a sibling ``meta/0.papgt`` -- there is no
    ``0.paz`` anywhere. ``0.pamt`` is a valid game file per
    ``_GAME_FILE_RE`` and each body-type folder imports fine on its own
    (Pattern 4), so the variant detector must still surface all six.
    """
    root = tmp_path / "CharacterCreator"
    root.mkdir()
    (root / "mod.json").write_text(
        '{"modinfo": {"title": "Character Creator", "version": "7.0"}}',
        encoding="utf-8")
    (root / "Female Animations.json").write_bytes(b"{}")
    (root / "Female Rapier and Shield Module.json").write_bytes(b"{}")
    (root / "CharacterCreatorHead.asi").write_bytes(b"\x00")
    for variant in ("HumanFemale", "HumanMale", "GoblinFemale",
                    "GoblinMale", "OrcFemale", "OrcMale"):
        data_dir = root / variant / "0036"
        data_dir.mkdir(parents=True)
        # 0.pamt index only -- NO 0.paz (the v6.3+ packaging change).
        (data_dir / "0.pamt").write_bytes(b"\x00")
        (data_dir / "_directory_table.bin").write_bytes(b"\x00")
        (data_dir / "_file_names.bin").write_bytes(b"\x00")
        meta_dir = root / variant / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "0.papgt").write_bytes(b"\x00")
    return tmp_path


def test_pamt_only_variants_surface(tmp_path):
    """v6.3+ Character Creator ships each body type as 0036/0.pamt with
    no 0.paz. All six body types must still surface as variants
    (GitHub #189). Before the fix the 0.paz-only marker check found
    zero variant subdirs, Pattern 5 was skipped, only the JSON modules
    + ASI imported, and the body-type picker never fired -- forcing the
    reporter to extract and drop each body-type folder by hand."""
    from cdumm.engine.import_handler import find_loose_file_variants

    _make_pamt_only_variant_pack(tmp_path)
    variants = find_loose_file_variants(tmp_path)

    ids = sorted(v["id"] for v in variants)
    expected = sorted([
        "HumanFemale", "HumanMale", "GoblinFemale",
        "GoblinMale", "OrcFemale", "OrcMale",
    ])
    assert len(variants) == 6, (
        f"expected 6 pamt-only body-type variants, got {len(variants)}: {ids}"
    )
    bodytype_hits = sum(
        1 for v in variants
        if any(bt in v["id"] for bt in expected)
    )
    assert bodytype_hits == 6, (
        f"every pamt-only variant must surface its body-type name; "
        f"got: {ids}"
    )
    bases = {Path(v["_base_dir"]).name for v in variants}
    assert bases == set(expected), (
        f"each variant's _base_dir must be its body-type subfolder; "
        f"got: {sorted(bases)}"
    )


_RACES_77 = ("Human Female", "Human Male", "Goblin Female", "Goblin Male",
             "Orc Female", "Orc Male", "Dwarf Female", "Dwarf Male")


def _make_loose_tree_variant_pack(tmp_path: Path) -> Path:
    """Character Creator 837 as shipped in 7.7 (GitHub #329).

    The third packaging change to this mod. 6.3 swapped ``0.paz`` for
    ``0.pamt`` (#189); 7.7 stops packing altogether and ships each body
    type as an UNPACKED tree -- ``0009/character/...`` and
    ``0012/ui/...``. Verified against the real Nexus file 13119: it
    contains **zero** ``0.paz`` or ``0.pamt`` entries anywhere.

    The NNNN dirs are still there and still correctly named, so the
    packed-only marker check found no variant subdirs, Pattern 5 was
    skipped, and the body-type picker silently never fired.
    """
    root = tmp_path / "Character Creator"
    root.mkdir()
    (root / "mod.json").write_text(
        '{"modinfo": {"title": "Character Creator", "version": "7.7"}}',
        encoding="utf-8")
    (root / "Female Animations.field.json").write_bytes(b"{}")
    (root / "CharacterCreatorHead.asi").write_bytes(b"\x00")
    for variant in _RACES_77:
        # 0009 -> character/, 0012 -> ui/ : loose trees, no archive.
        for nnnn, game_root in (("0009", "character"), ("0012", "ui")):
            leaf = root / variant / nnnn / game_root / "descriptors"
            leaf.mkdir(parents=True)
            (leaf / "player_001.xml").write_text("<x/>", encoding="utf-8")
    return tmp_path


def test_loose_tree_variants_surface(tmp_path):
    """7.7 ships unpacked NNNN/<root>/ trees with no .paz/.pamt at all.
    Every body type must still surface (GitHub #329)."""
    from cdumm.engine.import_handler import find_loose_file_variants

    _make_loose_tree_variant_pack(tmp_path)
    variants = find_loose_file_variants(tmp_path)

    ids = sorted(v["id"] for v in variants)
    assert len(variants) == len(_RACES_77), (
        f"expected {len(_RACES_77)} loose-tree body-type variants, "
        f"got {len(variants)}: {ids}"
    )
    bases = {Path(v["_base_dir"]).name for v in variants}
    assert bases == set(_RACES_77), (
        f"each variant's _base_dir must be its body-type subfolder; "
        f"got: {sorted(bases)}"
    )


def test_loose_marker_needs_a_directory_not_just_files(tmp_path):
    """The loose arm requires a SUBDIRECTORY inside NNNN, not merely a
    non-empty NNNN. A body-type folder whose NNNN holds only loose files
    is not a game tree, and widening the marker to "non-empty" would
    start classifying such shapes as variant packs."""
    from cdumm.engine.import_handler import find_loose_file_variants

    root = tmp_path / "Character Creator"
    root.mkdir()
    (root / "mod.json").write_text(
        '{"modinfo": {"title": "Character Creator"}}', encoding="utf-8")
    for variant in ("Human Female", "Human Male", "Orc Female"):
        d = root / variant / "0009"
        d.mkdir(parents=True)
        # files only -- no subdirectory, no archive
        (d / "readme.txt").write_text("notes", encoding="utf-8")
        (d / "preview.png").write_bytes(b"\x89PNG")

    variants = find_loose_file_variants(tmp_path)
    assert len(variants) < 2, (
        f"NNNN dirs holding only loose files must not be treated as "
        f"game trees; got {len(variants)}: {[v['id'] for v in variants]}"
    )


def test_nnnn_holds_game_content_arms(tmp_path):
    """The predicate itself: packed archive, loose subdirectory, and the
    two negatives."""
    from cdumm.engine.import_handler import _nnnn_holds_game_content

    packed_paz = tmp_path / "a" / "0009"
    packed_paz.mkdir(parents=True)
    (packed_paz / "0.paz").write_bytes(b"\x00")
    assert _nnnn_holds_game_content(packed_paz) is True

    packed_pamt = tmp_path / "b" / "0009"
    packed_pamt.mkdir(parents=True)
    (packed_pamt / "0.pamt").write_bytes(b"\x00")
    assert _nnnn_holds_game_content(packed_pamt) is True

    loose = tmp_path / "c" / "0009"
    (loose / "character").mkdir(parents=True)
    assert _nnnn_holds_game_content(loose) is True

    files_only = tmp_path / "d" / "0009"
    files_only.mkdir(parents=True)
    (files_only / "readme.txt").write_text("x", encoding="utf-8")
    assert _nnnn_holds_game_content(files_only) is False

    empty = tmp_path / "e" / "0009"
    empty.mkdir(parents=True)
    assert _nnnn_holds_game_content(empty) is False

    missing = tmp_path / "f" / "0009"
    assert _nnnn_holds_game_content(missing) is False
