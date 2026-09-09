"""GitHub #379: 'Check for Mod Updates' must say what it actually checked.

Nexus report (vizionblind): with an API key configured and every mod
imported from manually downloaded archives, the check always said
"All mods are up to date!". Those mods have no ``nexus_mod_id``, so
``check_mod_updates`` skips them entirely -- the check was reporting
success while checking nothing.

The fix counts linked vs unlinked mods when the check starts and picks
the summary from that:

* 0 linked, some unlinked -> a WARNING saying nothing was checkable;
* some of each            -> success that names both counts;
* all linked              -> the original message.

Source-level tests, matching this suite's convention for settings-page
behavior (see test_settings_view_patch_notes_button.py).
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _src() -> str:
    return (_ROOT / "src" / "cdumm" / "gui" / "pages" / "settings_page.py"
            ).read_text(encoding="utf-8")


def _en() -> dict:
    return json.loads((_ROOT / "src" / "cdumm" / "translations" / "en.json"
                       ).read_text(encoding="utf-8"))


def test_linked_and_unlinked_are_counted_when_the_check_starts():
    src = _src()
    assert "_pending_linked" in src and "_pending_unlinked" in src
    # The count must happen where the mod list is built (main thread),
    # not in the worker -- the worker only sees what it was handed.
    build = src.find("combined = mods + asi_mods")
    assert build != -1
    assert src.find("_pending_linked", build) - build < 1500, (
        "linked/unlinked must be counted right after `combined` is "
        "assembled")


def test_zero_linked_mods_is_a_warning_not_a_success():
    src = _src()
    anchor = src.find("def _show_nexus_results")
    body = src[anchor:anchor + 6000]
    assert "nexus_none_linked" in body, (
        "a library with no Nexus-linked mods must get its own message "
        "instead of 'All mods are up to date!'")
    none_at = body.find("nexus_none_linked")
    assert "WARNING" in body[none_at - 200:none_at + 200], (
        "'nothing was checkable' is a warning state, not a success")
    # The all-up-to-date success must come AFTER the guarded cases so it
    # can only be reached when every mod really was checked.
    assert none_at < body.find("nexus_all_up_to_date")


def test_partial_coverage_names_both_counts():
    src = _src()
    anchor = src.find("def _show_nexus_results")
    body = src[anchor:anchor + 6000]
    assert "nexus_up_to_date_partial" in body
    en = _en()
    partial = en["settings.nexus_up_to_date_partial"]
    assert "{checked}" in partial and "{unlinked}" in partial, (
        "the partial message must state how many mods were checked AND "
        "how many were not checkable, or it repeats the #379 ambiguity")


def test_translation_keys_exist_in_english():
    en = _en()
    assert "settings.nexus_none_linked" in en
    assert "settings.nexus_up_to_date_partial" in en
    assert "{count}" in en["settings.nexus_none_linked"]
    # All 15 locales carry the keys too -- test_i18n_key_parity enforces
    # it repo-wide, and its CI failure on this branch's first push is
    # what corrected the "en-only is fine via fallback" assumption.
