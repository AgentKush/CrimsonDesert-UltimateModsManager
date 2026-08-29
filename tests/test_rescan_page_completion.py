"""RescanPage never learned when its own rescan actually finished.

Report 2026-08-26: after updating and running Rescan, the snapshot
genuinely completed (the "Snapshot Complete" toast fired, twice --
the user re-clicked because the page looked stuck), but the Rescan
page itself stayed on "Initiating rescan..." forever and its stat
cards (file count, last scan date) never refreshed.

Root cause: RescanPage only emits ``rescan_requested`` into the main
window, which owns the actual SnapshotWorker QProcess. Every other
tool page on this base class runs its own worker and calls
``_set_running(False)`` when it completes; RescanPage never did,
so ``_set_running(True)`` was never called either -- the Rescan
button stayed enabled the whole time, which is how the user's
"looks frozen" click landed a second, real rescan instead of being a
no-op.

Fix: RescanPage now calls `_set_running(True)` before emitting the
signal, and polls the main window's `_active_worker` (the same
technique RecoveryFlow already uses to track the same QProcess) to
detect completion, then refreshes its stat cards and resets its own
status/button.

No qtbot in this environment (see test_rescan_blocks_modded_disk.py's
own "Wiring guard" section for the same constraint), so this is a
source-text guard rather than a widget-level test.
"""
from __future__ import annotations

from pathlib import Path


def _tool_page_src() -> str:
    return (Path(__file__).resolve().parents[1]
            / "src" / "cdumm" / "gui" / "pages" / "tool_page.py").read_text(
                encoding="utf-8")


def _rescan_page_body(src: str) -> str:
    anchor = src.find("class RescanPage(")
    assert anchor != -1, "RescanPage class not found"
    next_class = src.find("\nclass ", anchor + 10)
    return src[anchor:next_class if next_class != -1 else len(src)]


def test_on_run_clicked_disables_the_button_before_delegating():
    """Without _set_running(True), the button stays clickable for the
    whole (possibly long) rescan, which is how the user ended up
    triggering it twice."""
    body = _rescan_page_body(_tool_page_src())
    clicked_anchor = body.find("def _on_run_clicked")
    assert clicked_anchor != -1
    next_def = body.find("\n    def ", clicked_anchor + 20)
    clicked_body = body[clicked_anchor:next_def if next_def != -1 else clicked_anchor + 2000]
    set_running_idx = clicked_body.find("_set_running(True)")
    emit_idx = clicked_body.find("rescan_requested.emit")
    assert set_running_idx != -1, (
        "_on_run_clicked must call _set_running(True) so the button "
        "disables and Rescan can't be double-triggered")
    assert emit_idx != -1
    assert set_running_idx < emit_idx, (
        "_set_running(True) must run before the signal is emitted")


def test_completion_poll_checks_the_main_window_worker():
    body = _rescan_page_body(_tool_page_src())
    assert "_start_completion_poll" in body
    assert "_check_rescan_poll" in body
    assert '"_active_worker"' in body, (
        "completion poll must check the main window's _active_worker, "
        "the same flag RecoveryFlow polls for this exact QProcess")


def test_completion_poll_resets_status_and_refreshes_stats():
    body = _rescan_page_body(_tool_page_src())
    poll_anchor = body.find("def _check_rescan_poll")
    assert poll_anchor != -1
    next_def = body.find("\n    def ", poll_anchor + 20)
    poll_body = body[poll_anchor:next_def if next_def != -1 else poll_anchor + 1000]
    assert "_refresh_stats()" in poll_body, (
        "must refresh the file-count/last-scan stat cards, not just "
        "the status label")
    assert "_set_running(False)" in poll_body
    assert "tools.rescan.complete" in poll_body


def test_complete_translation_key_exists_in_every_locale():
    import json
    translations_dir = (Path(__file__).resolve().parents[1]
                        / "src" / "cdumm" / "translations")
    locale_files = sorted(translations_dir.glob("*.json"))
    assert len(locale_files) >= 15, "sanity check: expected the full locale set"
    missing = []
    for f in locale_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if "tools.rescan.complete" not in data:
            missing.append(f.name)
    assert not missing, f"tools.rescan.complete missing from: {missing}"
