"""Detect UI mods written for the pre-1.16 markup dialect (GitHub #344).

CD 1.16 renamed the attributes the UI engine reads. A mod that replaces
or patches a UI file and was built before that rename still installs
perfectly: the bytes land where they should, the file is valid, nothing
errors. The engine just never reads the attributes it carries, so the
mod does nothing at all. No skipped-patch message, no failure, no clue.

That is the failure mode this project works hardest to remove from its
own write path -- reporting success while achieving nothing -- arriving
from outside, through mod content going stale.

What actually changed
---------------------

Measured across **all 367 UI files** (``.html`` / ``.thtml`` / ``.css``)
in a live 1.16 install:

    attribute        1.16 occurrences
    class=                          0
    css=                       32,058
    scriptobject=                   2
    script=                     5,499
    template=                      23
    component=                  3,662

``class=`` is the reliable signal: zero occurrences, in an engine whose
markup is otherwise HTML-shaped, where ``class`` is the single most
common attribute there is. It became ``css=``.

``scriptobject=`` and ``template=`` did **not** go to zero, which is why
this module does not trigger on them. Their survivors are three files:

    template=      x22  ui/basecontrollereditor.thtml
    template=      x1   ui/commanddebugview.html
    scriptobject=  x2   ui/freerecitalpanel.html

A ``.thtml`` is a template file, so ``template=`` there is not stale
markup, and the other two are debug/leftover views. Treating either
attribute as proof of a pre-1.16 mod would fire on content that matches
the game's own current files. They are still counted and reported as
corroboration, but they never decide the verdict on their own.

Why the comparison is against the vanilla file
----------------------------------------------

The check is not "does this mod use ``class=``" -- it is "does this mod
use markup its own target file no longer uses". Reading the vanilla
counterpart is what makes the check correct on a pre-1.16 game, where
``class=`` is the right dialect and no warning should appear. It costs
nothing: the callers already hold both byte strings.

This module never repairs anything. Only a mod's author can do that, by
republishing against 1.16 markup. The goal is that CDUMM stops being
silent about it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Old attribute -> the 1.16 name that replaced it.
RENAMED: dict[str, str] = {
    "class": "css",
    "scriptobject": "script",
}

#: Only this pair decides a verdict. See the module docstring for why
#: ``scriptobject``/``template`` are counted but never decisive.
DECIDING_OLD = "class"
DECIDING_NEW = "css"

#: Counted for the message, not for the decision.
CORROBORATING = ("scriptobject", "script", "template", "component")

_ATTR_RE: dict[str, re.Pattern[bytes]] = {
    name: re.compile(rb"\b" + name.encode() + rb"\s*=")
    for name in (*RENAMED, *RENAMED.values(), "template", "component")
}

#: Extensions whose contents the UI engine parses for these attributes.
UI_SUFFIXES = (".html", ".thtml", ".css")


def count_markers(data: bytes) -> dict[str, int]:
    """How many times each dialect attribute appears in ``data``."""
    return {name: len(rx.findall(data)) for name, rx in _ATTR_RE.items()}


@dataclass(frozen=True)
class DialectVerdict:
    """Why a UI file was, or was not, judged stale."""
    stale: bool
    reason: str
    mod: dict[str, int] = field(default_factory=dict)
    vanilla: dict[str, int] = field(default_factory=dict)

    def message(self, file_label: str, mod_name: str = "") -> str:
        """A sentence for the user, naming the file and what to do."""
        who = f"'{mod_name}' " if mod_name else ""
        old = self.mod.get(DECIDING_OLD, 0)
        return (
            f"{who}targets '{file_label}' with pre-1.16 UI markup: it uses "
            f"{old} '{DECIDING_OLD}=' attribute(s), which CD 1.16 renamed to "
            f"'{DECIDING_NEW}='. The file will install correctly but the "
            f"game's UI engine will not read it, so the mod will have no "
            f"effect. It needs an update from its author."
        )


def is_ui_file(name: str) -> bool:
    return name.lower().endswith(UI_SUFFIXES)


def compare(mod_data: bytes, vanilla_data: bytes) -> DialectVerdict:
    """Judge ``mod_data`` against the game's own ``vanilla_data``.

    Stale means: the mod carries the old attribute, and the file it
    replaces does not carry it while it does carry the new one. Both
    halves matter --

    * without the mod-side test there is nothing to warn about;
    * without the vanilla-side test this would fire on a pre-1.16 game,
      where the old dialect is exactly right.
    """
    mod = count_markers(mod_data)
    van = count_markers(vanilla_data)

    if not mod.get(DECIDING_OLD):
        return DialectVerdict(False, "mod does not use the old dialect",
                              mod, van)
    if van.get(DECIDING_OLD):
        return DialectVerdict(
            False, "the game's own copy of this file still uses the old "
                   "dialect, so the mod matches it", mod, van)
    if not van.get(DECIDING_NEW):
        return DialectVerdict(
            False, "the game's copy uses neither dialect, so there is "
                   "nothing to compare against", mod, van)
    return DialectVerdict(
        True,
        f"mod uses {mod[DECIDING_OLD]}x '{DECIDING_OLD}=' where the game's "
        f"copy uses {van[DECIDING_NEW]}x '{DECIDING_NEW}=' and no "
        f"'{DECIDING_OLD}='",
        mod, van)
