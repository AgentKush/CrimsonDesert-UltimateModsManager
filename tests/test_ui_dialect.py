"""Pre-1.16 UI markup detection (GitHub #344).

The failure this guards against is the quiet one: a UI mod built before
CD 1.16 installs perfectly and does nothing, because 1.16 renamed the
attributes the UI engine reads. Barber Unlocked (#337) is the reported
case; nothing about it is specific.

The numbers these tests encode were measured across all 367 UI files in
a live 1.16 install, not taken from the issue text -- and the sweep
disagreed with the issue in a way that matters. See
``test_only_class_is_treated_as_decisive``.
"""
from __future__ import annotations

import pytest

from cdumm.engine.ui_dialect import (
    CORROBORATING,
    DECIDING_NEW,
    DECIDING_OLD,
    RENAMED,
    compare,
    count_markers,
    is_ui_file,
)

# Shapes taken from the real files: a pre-1.16 view and its 1.16 successor.
PRE_116 = (b'<div class="panel" scriptobject="UIThing" template="X" '
           b'component="Y"><span class="label">hi</span></div>')
CD_116 = (b'<div css="panel" script="UIThing" component="X.Y">'
          b'<span css="label">hi</span></div>')


# ── the decision ────────────────────────────────────────────────────────

def test_a_pre_116_mod_against_a_116_game_is_flagged():
    v = compare(PRE_116, CD_116)
    assert v.stale
    assert DECIDING_OLD in v.reason
    msg = v.message("ui/barbershopview.html", "Barber Unlocked")
    assert "Barber Unlocked" in msg
    assert "no effect" in msg
    assert "update from its author" in msg


def test_a_current_mod_is_not_flagged():
    assert compare(CD_116, CD_116).stale is False


def test_a_pre_116_mod_against_a_pre_116_game_is_not_flagged():
    """The check must not fire on an older game, where the old dialect is
    the correct one. This is why the comparison reads the vanilla file
    rather than judging the mod on its own."""
    v = compare(PRE_116, PRE_116)
    assert v.stale is False
    assert "still uses the old dialect" in v.reason


def test_a_file_with_neither_dialect_is_not_flagged():
    v = compare(b"<div>plain</div>", b"<div>plain</div>")
    assert v.stale is False


def test_a_vanilla_file_using_neither_dialect_gives_nothing_to_compare():
    v = compare(PRE_116, b"<div>plain</div>")
    assert v.stale is False
    assert "nothing to compare" in v.reason


# ── why only class= decides ─────────────────────────────────────────────

def test_only_class_is_treated_as_decisive():
    """Measured, not assumed -- and the measurement corrected the issue.

    #344 reports ``scriptobject=`` and ``template=`` as 0 in 1.16, from a
    40-file sample. Across all 367 UI files they are 2 and 23: they
    survive in ``ui/basecontrollereditor.thtml`` (a template file, where
    ``template=`` is not stale markup), ``ui/commanddebugview.html`` and
    ``ui/freerecitalpanel.html``.

    ``class=`` is 0 of 0 across all 367, in markup that is otherwise
    HTML-shaped. So it decides, and the others only corroborate --
    triggering on them would fire on content matching the game's own
    current files.
    """
    assert DECIDING_OLD == "class"
    assert DECIDING_NEW == "css"
    assert RENAMED[DECIDING_OLD] == DECIDING_NEW
    assert "template" in CORROBORATING
    assert "scriptobject" in CORROBORATING

    # A mod carrying ONLY the survivors, against a 1.16 file that also
    # carries them, must not be flagged.
    mod = b'<div template="BaseControllerEditor" scriptobject="X"></div>'
    van = b'<div template="BaseControllerEditor" scriptobject="X" css="a">'
    assert compare(mod, van).stale is False


def test_the_survivors_are_still_counted_for_the_report():
    c = count_markers(PRE_116)
    assert c["class"] == 2
    assert c["scriptobject"] == 1
    assert c["template"] == 1
    assert c["css"] == 0


# ── scope ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("ui/barbershopview.html", True),
    ("ui/thing.thtml", True),
    ("ui/style.css", True),
    ("gamedata/iteminfo.pabgb", False),
    ("textures/foo.dds", False),
    ("readme.txt", False),
])
def test_only_ui_files_are_in_scope(name, expected):
    assert is_ui_file(name) is expected


def test_a_partially_updated_mod_is_still_flagged():
    """A mod half-converted to 1.16 still carries attributes the engine
    will not read, so the part that uses the old names is still inert."""
    mod = b'<div css="new">ok</div><span class="old">stale</span>'
    assert compare(mod, CD_116).stale is True


# ── the real install, when it is present ────────────────────────────────

def test_live_116_ui_files_never_use_class():
    """Pins the premise against real data rather than the fixture shapes
    above. Skips when the game is not installed."""
    game = _live_game_dir()
    if game is None:
        pytest.skip("no live game install")
    from cdumm.archive import paz_parse
    from cdumm.engine import game_index
    from cdumm.engine.json_patch_handler import _extract_from_paz

    seen = classes = css = 0
    for arch in game_index.archive_dirs(str(game)):
        d = game / arch
        entries = _tolerate(paz_parse.parse_pamt, str(d / "0.pamt"), str(d))
        for e in entries or ():
            if not is_ui_file(e.path):
                continue
            data = _tolerate(_extract_from_paz, e)
            if data is None:
                continue
            seen += 1
            c = count_markers(data)
            classes += c["class"]
            css += c["css"]
    if seen == 0:
        pytest.skip("no UI files readable from this install")
    assert classes == 0, f"{classes} 'class=' found across {seen} UI files"
    assert css > 0, "expected the 1.16 dialect in a 1.16 install"


# ── the warning reaches the user ────────────────────────────────────────

def test_the_import_helper_surfaces_the_warning(tmp_path, monkeypatch):
    """The check is only worth anything if it reaches result.info, which
    is what the GUI shows as a yellow InfoBar. Drives the real helper
    with the PAMT lookup stubbed to a 1.16 vanilla file."""
    from cdumm.engine import import_handler as ih

    mod_dir = tmp_path / "mod"
    (mod_dir / "ui").mkdir(parents=True)
    (mod_dir / "ui" / "barbershopview.html").write_bytes(PRE_116)

    monkeypatch.setattr(
        "cdumm.engine.json_patch_handler._find_pamt_entry",
        lambda name, gdir: object())
    monkeypatch.setattr(
        "cdumm.engine.json_patch_handler._extract_from_paz",
        lambda entry: CD_116)

    result = ih.ModImportResult("Barber Unlocked")
    ih._warn_on_stale_ui_dialect(mod_dir, tmp_path / "game", result)

    assert result.info, "the warning must reach the user, not just the log"
    assert "barbershopview.html" in result.info
    assert "no effect" in result.info
    assert result.error is None, "advice must not fail the import"


def test_a_current_ui_mod_produces_no_warning(tmp_path, monkeypatch):
    from cdumm.engine import import_handler as ih

    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()
    (mod_dir / "someview.html").write_bytes(CD_116)
    monkeypatch.setattr(
        "cdumm.engine.json_patch_handler._find_pamt_entry",
        lambda name, gdir: object())
    monkeypatch.setattr(
        "cdumm.engine.json_patch_handler._extract_from_paz",
        lambda entry: CD_116)

    result = ih.ModImportResult("Fine Mod")
    ih._warn_on_stale_ui_dialect(mod_dir, tmp_path / "game", result)
    assert result.info is None


def test_a_mod_with_no_ui_files_is_untouched(tmp_path):
    from cdumm.engine import import_handler as ih

    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()
    (mod_dir / "texture.dds").write_bytes(b"\x00" * 32)
    result = ih.ModImportResult("Texture Mod")
    ih._warn_on_stale_ui_dialect(mod_dir, tmp_path / "game", result)
    assert result.info is None


def test_a_broken_lookup_never_fails_the_import(tmp_path, monkeypatch):
    """Advice must never be able to break an install."""
    from cdumm.engine import import_handler as ih

    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()
    (mod_dir / "view.html").write_bytes(PRE_116)

    def boom(*a, **k):
        raise RuntimeError("PAMT exploded")

    monkeypatch.setattr(
        "cdumm.engine.json_patch_handler._find_pamt_entry", boom)
    result = ih.ModImportResult("Mod")
    ih._warn_on_stale_ui_dialect(mod_dir, tmp_path / "game", result)
    assert result.info is None
    assert result.error is None


def _tolerate(fn, *args):
    """Run ``fn``, or return None if the install refuses to cooperate.

    A machine-local archive read can fail for reasons that say nothing
    about the code under test -- a partially downloaded PAZ, a locked
    file, an archive shape this build predates. The assertion below is
    about the files that DO read, so anything that doesn't is skipped
    rather than turned into a failure.
    """
    try:
        return fn(*args)
    except Exception:                                  # noqa: BLE001
        return None


def _live_game_dir():
    from pathlib import Path

    from cdumm.storage.game_finder import find_game_directories
    dirs = _tolerate(find_game_directories)
    for d in dirs or []:
        p = Path(d)
        if (p / "meta" / "0.papgt").exists():
            return p
    return None
