"""Regression: _iteminfo_layout_roots must fail clean when
detect_iteminfo_layout can't recognize the installed game's iteminfo
record layout (e.g. a game update the parser doesn't model yet).

Report 2026-08-26: on a game version detect_iteminfo_layout can't
decode, it returns None, and `frozenset(f[0] for f in fields)` raised
`TypeError: 'NoneType' object is not iterable`. The broad except already
caught it and returned None either way (this function is documented as
a safety net that must fail open), so the only effect was a full
traceback logged on every apply for affected game versions -- noisy,
but not a real bug. The fix is a plain early return before the
iteration.
"""
from __future__ import annotations

from unittest.mock import patch

from cdumm.engine.format3_apply import _iteminfo_layout_roots


def test_returns_none_without_raising_when_layout_undetected():
    with patch("cdumm.engine.format3_apply.parse_pabgh_index",
               return_value=(4, {0: 0})), patch(
            "cdumm.engine.iteminfo_native_parser.detect_iteminfo_layout",
            return_value=None):
        assert _iteminfo_layout_roots(b"\x00" * 32, b"\x00" * 32) is None


def test_returns_none_when_layout_detects_as_empty_list():
    """Same guard, the other falsy value detect_iteminfo_layout could
    plausibly return instead of None."""
    with patch("cdumm.engine.format3_apply.parse_pabgh_index",
               return_value=(4, {0: 0})), patch(
            "cdumm.engine.iteminfo_native_parser.detect_iteminfo_layout",
            return_value=[]):
        assert _iteminfo_layout_roots(b"\x00" * 32, b"\x00" * 32) is None
