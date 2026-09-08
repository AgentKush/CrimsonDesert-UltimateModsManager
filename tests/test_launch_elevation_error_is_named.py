"""GitHub #415 (ombre03): a launch blocked by RUNASADMIN blamed Steam.

``_on_launch_game`` spawns the exe directly for a non-storefront
install, and for a Steam install when ``steam_launch_method`` is
``exe``. When ``CrimsonDesert.exe`` carries the RUNASADMIN
compatibility flag, Windows refuses that spawn with **WinError 740,
"the requested operation requires elevation"**. The old handler caught
it in the blanket ``except`` and, for a Steam install, showed
``main.launch_failed_steam_not_running`` -- telling the user to check a
Steam client that was running perfectly well.

ombre03 reported exactly that loop: every launch method tried, same
outcome, no mention of the real cause. The flag was sitting in his own
bug report the whole time, because ``gui/bug_report.py`` already detects
it. The launch path now names it too.

The check is on the source rather than a live Qt launch because
``_on_launch_game`` needs a constructed main window, a database and a
game directory. What matters is the branch order: the WinError 740 test
must come BEFORE the Steam branch, or the Steam message wins again.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_KEY = "main.launch_failed_needs_elevation"


def _launch_body() -> str:
    src = (_ROOT / "src" / "cdumm" / "gui"
           / "fluent_window.py").read_text(encoding="utf-8")
    start = src.index("def _on_launch_game")
    nxt = src.index("\n    def ", start + 1)
    return src[start:nxt]


def test_winerror_740_is_handled_at_all():
    body = _launch_body()
    assert "740" in body, (
        "the launch handler no longer special-cases WinError 740, so an exe "
        "with RUNASADMIN set will be reported as some other failure")
    assert _KEY in body


def test_the_elevation_check_runs_before_the_steam_message():
    """Order is the whole fix. Steam-first would mask it again."""
    body = _launch_body()
    assert body.index("740") < body.index(
        "main.launch_failed_steam_not_running"), (
        "the WinError 740 branch must be tested before the Steam branch, "
        "otherwise a Steam install still reports 'could not reach Steam'")


def test_the_message_names_the_compatibility_flag_not_steam():
    """The text has to send the user somewhere that helps."""
    en = json.loads((_ROOT / "src" / "cdumm" / "translations" / "en.json")
                    .read_text(encoding="utf-8"))
    msg = en[_KEY].lower()
    assert "administrator" in msg
    assert "compatibility" in msg
    assert "crimsondesert.exe" in msg
    assert "not a steam problem" in msg


def test_every_parity_locale_carries_the_key():
    """en and de are the pair tests/test_i18n_key_parity.py enforces."""
    for lang in ("en", "de"):
        d = json.loads((_ROOT / "src" / "cdumm" / "translations"
                        / f"{lang}.json").read_text(encoding="utf-8"))
        assert _KEY in d, f"{lang}.json is missing {_KEY}"
