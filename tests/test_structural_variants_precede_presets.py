"""Regression for GitHub #190 (lurkser/woowoots): a character-creator
style mod that ships both loose Format-3 JSON modules AND per-race PAZ
variant folders must show the race picker, not get swallowed by the
JSON-preset picker.

Mod 837 (Character Creator) extracts to a single ``CharacterCreator/``
wrapper holding four ``.json`` modules, an ``.asi`` and six race folders
(HumanFemale, GoblinMale, OrcFemale ...), each with its own ``0036/0.paz``.
``find_json_presets`` matched two of the JSON modules, so the import
showed the preset picker and returned before the folder-variant picker
ran. The user never got to pick their gender/race.

``has_structural_folder_variants`` is the guard the import flow uses to
detect this: when the drop carries two or more variant folders (each
with game content), the structural choice wins and the JSON-preset
shortcut is skipped. These tests pin that it fires for the mod-837
shape (including the single-wrapper case) but stays quiet for a plain
JSON multi-preset mod, which must keep using the preset picker.
"""
from __future__ import annotations

from pathlib import Path

from cdumm.gui.preset_picker import has_structural_folder_variants


def _race_folder(parent: Path, name: str) -> None:
    """A race variant folder with a numbered PAZ dir, like mod 837."""
    d = parent / name / "0036"
    d.mkdir(parents=True)
    (d / "0.paz").write_bytes(b"PAZ\x00" * 8)
    (d / "0.pamt").write_bytes(b"PAMT" + b"\x00" * 20)


def test_mod837_shape_with_wrapper_is_structural(tmp_path: Path):
    """The real shape: a single CharacterCreator/ wrapper holding loose
    JSON modules plus six race folders. The detector must descend the
    wrapper and report structural variants."""
    root = tmp_path / "extract"
    cc = root / "CharacterCreator"
    cc.mkdir(parents=True)
    # Loose Format-3 JSON modules + an ASI at the wrapper root.
    (cc / "Female Animations.json").write_text("{}", encoding="utf-8")
    (cc / "Female Armor Module.json").write_text("{}", encoding="utf-8")
    (cc / "CharacterCreatorHead.asi").write_bytes(b"MZ\x00")
    for race in ("HumanFemale", "HumanMale", "GoblinFemale",
                 "GoblinMale", "OrcFemale", "OrcMale"):
        _race_folder(cc, race)

    assert has_structural_folder_variants(root) is True


def test_race_folders_without_wrapper_is_structural(tmp_path: Path):
    """Same content but no wrapper folder (extracted flat)."""
    root = tmp_path / "flat"
    root.mkdir()
    for race in ("HumanFemale", "OrcMale"):
        _race_folder(root, race)
    assert has_structural_folder_variants(root) is True


def test_plain_json_preset_mod_is_not_structural(tmp_path: Path):
    """A plain multi-preset JSON mod (no per-variant folders) must NOT
    be treated as structural, so it keeps using the preset picker."""
    root = tmp_path / "jsonmod"
    root.mkdir()
    (root / "Option A.json").write_text("{}", encoding="utf-8")
    (root / "Option B.json").write_text("{}", encoding="utf-8")
    (root / "Option C.json").write_text("{}", encoding="utf-8")
    assert has_structural_folder_variants(root) is False


def test_format3_variant_pack_is_not_structural(tmp_path: Path):
    """A Format-3 variant pack ships loose .field.json levels, no
    folders. It must stay on the preset/format3 path, not be hijacked
    by the folder-variant guard."""
    root = tmp_path / "wings"
    root.mkdir()
    for lvl in ("CrimsonWings_10pct.field.json",
                "CrimsonWings_50pct.field.json",
                "CrimsonWings_100pct.field.json"):
        (root / lvl).write_text("{}", encoding="utf-8")
    assert has_structural_folder_variants(root) is False
