"""Tests for scripts/apply_scan.py -- the apply-level gap scanner.

A scanner that reports a false all-clear is worse than no scanner, and
that is exactly the failure this one exists to fix: ``coverage_scan.py``
printed "No uncovered Format-3 intents found" over a 52-mod corpus while
the apply path was silently dropping 1466 intents.

So the properties that matter here are the honesty ones:
  * a drop the engine logged must be reported
  * a target whose vanilla bytes can't be sourced must be reported as
    NOT SCANNED, never counted as clean
  * the exit code must equal the number of distinct drop classes, so CI
    can gate on it exactly as it gates on coverage_scan.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

apply_scan = pytest.importorskip("apply_scan")


def _write_mod(tmp_path: Path, name: str, target: str, intents: list) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({
        "format": 3,
        "target": target,
        "intents": intents,
    }), encoding="utf-8")
    return p


# ── the capture handler ──────────────────────────────────────────────

def test_capture_keeps_only_drop_messages():
    cap = apply_scan._DropCapture()
    log = logging.getLogger("cdumm.test_capture")
    log.addHandler(cap)
    try:
        log.warning("field 'x' is not supported, skipping")
        log.warning("everything applied fine")   # not a drop
        log.warning("could not locate field 'y'")
    finally:
        log.removeHandler(cap)
    assert len(cap.msgs) == 2
    assert all("supported" in m or "locate" in m for m in cap.msgs)


def test_capture_ignores_info_level():
    """Only WARNING+ is a refusal; INFO chatter must not become a gap."""
    cap = apply_scan._DropCapture()
    log = logging.getLogger("cdumm.test_capture_info")
    log.setLevel(logging.DEBUG)
    log.addHandler(cap)
    try:
        log.info("nested path 'a.b' skipped (unresolved)")
    finally:
        log.removeHandler(cap)
    assert cap.msgs == []


def test_capture_collapses_per_record_noise_into_one_class():
    """One field refused on 3 records is ONE gap, not three.

    Without this the report is thousands of lines and the exit code is a
    record count rather than a gap count.
    """
    cap = apply_scan._DropCapture()
    log = logging.getLogger("cdumm.test_capture_collapse")
    log.addHandler(cap)
    try:
        for key in (114590, 134056, 124067):
            log.warning(
                "nested path 'sharpness_data.stat_data.stat_list_static' "
                "on key=%d skipped (unresolved)", key)
    finally:
        log.removeHandler(cap)
    classes = cap.classes()
    assert len(classes) == 1, classes
    assert next(iter(classes.values())) == 3


def test_capture_keeps_distinct_fields_distinct():
    """Collapsing digits must not merge two different fields."""
    cap = apply_scan._DropCapture()
    log = logging.getLogger("cdumm.test_capture_distinct")
    log.addHandler(cap)
    try:
        log.warning("field 'call_mercenary_spawn_duration' is not supported")
        log.warning("field 'character_weight' is not supported")
    finally:
        log.removeHandler(cap)
    assert len(cap.classes()) == 2


def test_capture_survives_an_unformattable_record():
    """A bad log record must not abort a corpus scan."""
    cap = apply_scan._DropCapture()
    rec = logging.LogRecord(
        "cdumm.x", logging.WARNING, __file__, 1,
        "skipping %d %d", (1,), None)   # too few args -> getMessage raises
    cap.emit(rec)   # must not raise
    assert cap.msgs == []


# ── unscanned targets are never counted as clean ─────────────────────

def test_target_without_vanilla_bytes_is_reported_not_scanned(tmp_path):
    """The whole point: silence about what we couldn't read is the bug."""
    _write_mod(tmp_path, "m.json", "stringinfo.pabgb",
               [{"entry": "E", "key": 1, "field": "buffer",
                 "op": "set", "new": "x"}])

    drops, _who, unscanned, n_mods = apply_scan.scan(
        [str(tmp_path)], lambda _t: None)

    assert n_mods == 1
    assert "stringinfo.pabgb" in unscanned
    assert not drops, "no vanilla bytes means unmeasured, not clean"


def test_main_prints_not_scanned_and_still_exits_zero(tmp_path, capsys):
    """Exit 0 is correct -- nothing was measured, so nothing failed --
    but the output has to say so out loud."""
    _write_mod(tmp_path, "m.json", "stringinfo.pabgb",
               [{"entry": "E", "key": 1, "field": "buffer",
                 "op": "set", "new": "x"}])
    monkey = apply_scan.make_fixture_extractor
    apply_scan.make_fixture_extractor = lambda: (lambda _t: None)
    try:
        code = apply_scan.main(["apply_scan.py", str(tmp_path)])
    finally:
        apply_scan.make_fixture_extractor = monkey
    out = capsys.readouterr().out
    assert code == 0
    assert "NOT SCANNED" in out
    assert "not proven clean" in out


# ── exit code contract, matching coverage_scan.py ────────────────────

def test_exit_code_is_the_number_of_distinct_drop_classes(monkeypatch,
                                                          tmp_path):
    """CI gates on this exactly as it gates on coverage_scan.py."""
    _write_mod(tmp_path, "m.json", "iteminfo.pabgb",
               [{"entry": "E", "key": 1, "field": "max_stack_count",
                 "op": "set", "new": 9}])

    def fake_expand(_agg, _sig, _db, **_kw):
        log = logging.getLogger("cdumm.fake")
        log.warning("field 'a' is not supported, skipping")
        log.warning("field 'a' is not supported, skipping")
        log.warning("field 'b' is not supported, skipping")

    monkeypatch.setattr(apply_scan, "expand_format3_into_aggregated",
                        fake_expand)
    monkeypatch.setattr(apply_scan, "make_fixture_extractor",
                        lambda: (lambda _t: (b"\x00" * 64, b"")))

    code = apply_scan.main(["apply_scan.py", str(tmp_path)])
    assert code == 2, "two distinct fields refused = two gaps"


def test_clean_corpus_exits_zero(monkeypatch, tmp_path, capsys):
    _write_mod(tmp_path, "m.json", "iteminfo.pabgb",
               [{"entry": "E", "key": 1, "field": "max_stack_count",
                 "op": "set", "new": 9}])
    monkeypatch.setattr(apply_scan, "expand_format3_into_aggregated",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(apply_scan, "make_fixture_extractor",
                        lambda: (lambda _t: (b"\x00" * 64, b"")))
    code = apply_scan.main(["apply_scan.py", str(tmp_path)])
    assert code == 0
    assert "No intents dropped" in capsys.readouterr().out


def test_a_crashing_mod_is_a_reported_gap_not_a_lost_scan(monkeypatch,
                                                          tmp_path):
    """One mod blowing up must not abort the corpus or vanish silently."""
    _write_mod(tmp_path, "m.json", "iteminfo.pabgb",
               [{"entry": "E", "key": 1, "field": "max_stack_count",
                 "op": "set", "new": 9}])

    def boom(*_a, **_k):
        raise RuntimeError("writer exploded")

    monkeypatch.setattr(apply_scan, "expand_format3_into_aggregated", boom)
    drops, who, _unscanned, n_mods = apply_scan.scan(
        [str(tmp_path)], lambda _t: (b"\x00" * 64, b""))
    assert n_mods == 1
    assert any("APPLY CRASHED" in k for k in drops)
    assert any("RuntimeError" in k for k in drops)
    assert who


# ── scope: v2 mods are counted, not silently ignored ─────────────────

def test_v2_byte_patch_mods_are_counted(tmp_path):
    """v2 is the larger share of the real population (226 of 498 JSONs on
    a Nexus corpus, vs 52 Format-3), and this scanner doesn't cover it.
    Counting them keeps a clean result from being read as "no gaps"."""
    (tmp_path / "v2_explicit.json").write_text(json.dumps({
        "name": "m", "format": 2, "patches": [{"file": "iteminfo.pabgb"}],
    }), encoding="utf-8")
    # Some v2 mods omit the format key entirely and are identified by
    # carrying patches without intents -- both shapes appear on Nexus.
    (tmp_path / "v2_implicit.json").write_text(json.dumps({
        "name": "m2", "patches": [{"file": "skill.pabgb"}],
    }), encoding="utf-8")
    _write_mod(tmp_path, "f3.json", "iteminfo.pabgb",
               [{"entry": "E", "key": 1, "field": "max_stack_count",
                 "op": "set", "new": 9}])

    assert apply_scan.count_v2_mods([str(tmp_path)]) == 2
    # ...and the Format-3 detector must not claim the v2 files.
    assert len(apply_scan.find_format3_mods([str(tmp_path)])) == 1


def test_format3_mod_is_not_counted_as_v2(tmp_path):
    """A Format-3 mod has intents, so the 'patches without intents' arm
    must not swallow it."""
    _write_mod(tmp_path, "f3.json", "iteminfo.pabgb",
               [{"entry": "E", "key": 1, "field": "max_stack_count",
                 "op": "set", "new": 9}])
    assert apply_scan.count_v2_mods([str(tmp_path)]) == 0


def test_main_reports_the_v2_count(tmp_path, monkeypatch, capsys):
    (tmp_path / "v2.json").write_text(json.dumps({
        "name": "m", "format": 2, "patches": [{"file": "iteminfo.pabgb"}],
    }), encoding="utf-8")
    _write_mod(tmp_path, "f3.json", "iteminfo.pabgb",
               [{"entry": "E", "key": 1, "field": "max_stack_count",
                 "op": "set", "new": 9}])
    monkeypatch.setattr(apply_scan, "expand_format3_into_aggregated",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(apply_scan, "make_fixture_extractor",
                        lambda: (lambda _t: (b"\x00" * 64, b"")))
    apply_scan.main(["apply_scan.py", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Skipped 1 v2 byte-patch mod(s)" in out
    assert "Format 3 only" in out


# ── mod detection matches coverage_scan.py ───────────────────────────

def test_non_format3_json_is_ignored(tmp_path):
    (tmp_path / "modinfo.json").write_text(
        json.dumps({"name": "a PAZ mod", "version": "1"}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert apply_scan.find_format3_mods([str(tmp_path)]) == []


def test_format3_mod_is_found_without_a_format_key(tmp_path):
    """DMM ships wrapper shapes with no ``format: 3`` field. Sniffing for
    that key found 4 mods out of 498 in the real corpus instead of 52, so
    detection goes through parse_format3_mod_targets like coverage_scan."""
    p = tmp_path / "wrapped.json"
    p.write_text(json.dumps({
        "modinfo": {"title": "t"},
        "format": 3,
        "format_minor": 1,
        "targets": [{
            "file": "iteminfo.pabgb",
            "intents": [{"entry": "E", "key": 1,
                         "field": "max_stack_count",
                         "op": "set", "new": 9}],
        }],
    }), encoding="utf-8")
    assert apply_scan.find_format3_mods([str(tmp_path)]) == [p]
