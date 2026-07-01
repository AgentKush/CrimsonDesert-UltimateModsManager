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
