"""The half of the canary that does not need the game installed.

``post_update_check.py`` answers "did the patch break us?" against a live
install, and its ``--baseline`` pass answers the other half of that
question -- "or did we break ourselves?" -- against the tables committed
to this repo. Only the second half can run here, and until now nothing
ran it: no test imported the canary, so every fixture decode depended on
somebody with the game remembering to pass ``--baseline``.

That is the wrong way round. The fixture pass needs no install, so CI is
exactly where it belongs, and a code change that stops decoding a table
we have bytes for should fail here rather than surface days later on
somebody's desktop.

It also pins the coverage itself. The pass used to walk a hardcoded
``("vanilla113", "vanilla115", "vanilla116")`` and only the three
hand-written checks, so five of the eleven decodes we had bytes for were
being exercised -- including, silently, none of vanilla1161, the newest
table in the repo. A count that nothing asserts drifts back down.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

puc = pytest.importorskip("post_update_check")

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def rows():
    return puc.run_fixture_checks()


def test_every_committed_fixture_directory_is_reached():
    """Discovery, not a list. The bug this replaced was a hardcoded three."""
    on_disk = {p.name for p in _FIXTURES.glob("vanilla*") if p.is_dir()}
    assert set(puc.fixture_versions()) == on_disk
    assert "vanilla1161" in on_disk, (
        "the 1.16.1 storeinfo table is what #365/#366 turned on; if it has "
        "gone, this pass has lost its most recent evidence")


def test_every_pinned_fixture_check_passes(rows):
    """The regression net. Each of these decoded once, so a failure is
    ours -- there is no game patch in reach of a committed byte."""
    failures = [(label, detail) for label, ok, detail, gating in rows
                if gating and not ok]
    assert not failures, "\n".join(f"{l}: {d}" for l, d in failures)


def test_the_pinned_set_is_all_reachable(rows):
    """A pin for a table with no bytes gates nothing while looking like it
    does. Deleting a fixture must show up here, not as quiet coverage loss.
    """
    seen = {tuple(label.split("/", 1)) for label, _ok, _d, _g in rows}
    assert puc._FIXTURE_GREEN <= seen, (
        f"pinned but never run: {sorted(puc._FIXTURE_GREEN - seen)}")
    order_pairs = {(ver, table.lower())
                   for ver, table in puc._FIXTURE_ORDER_BASELINE}
    assert order_pairs <= seen, (
        f"order baseline pinned but never run: {sorted(order_pairs - seen)}")


def test_the_pass_covers_more_than_the_three_hand_written_checks(rows):
    """Coverage is the point of the change; assert it rather than trust it.

    Seventeen decodes across seven builds, from the named checks plus the
    ordered tables that have fixtures. Pinned as a floor, not an equality:
    committing a new capture should raise this, never lower it -- the CD
    2.0 capture (b24934353) raised it from fifteen with no code change,
    which is the discovery design working.
    """
    assert len(rows) >= 17
    assert len({label.split("/", 1)[0] for label, *_ in rows}) >= 7
    tables = {label.split("/", 1)[1] for label, *_ in rows}
    assert {"skill", "storeinfo", "statusgroupinfo"} < tables, (
        "the ordered tables are meant to be scored against fixtures too -- "
        "that was the other half of the hardcoded-list bug")


def test_iteminfo_gets_a_row_from_each_of_its_two_readers(rows):
    """One table, two readers, two rows -- and they must stay separable.

    The row label is what keeps them apart, because both load the same
    two files. If they ever collapse to one label, one silently overwrites
    the other in _FIXTURE_GREEN and half the coverage disappears without
    a failure anywhere.
    """
    labels = [label for label, *_ in rows]
    assert len(labels) == len(set(labels)), (
        f"duplicate row labels: {sorted({x for x in labels if labels.count(x) > 1})}")
    for ver in ("vanilla116", "vanilla_b24773079", "vanilla_b24934353"):
        assert f"{ver}/iteminfo" in labels
        assert f"{ver}/iteminfo-native" in labels


def test_an_unpinned_fixture_reports_without_gating(monkeypatch):
    """The semantics a fresh capture depends on.

    A brand-new fixture that fails to decode is the game patch this canary
    exists to report -- not a regression in this repo -- so it must not be
    counted as a problem or inflate the exit code. Pin it once its numbers
    have been read.
    """
    monkeypatch.setattr(puc, "_FIXTURE_GREEN", frozenset())
    rows = puc.run_fixture_checks(versions=["vanilla115"])
    assert rows, "the pass still has to run the checks, just not gate them"
    assert not any(gating for *_rest, gating in rows)


def test_an_old_fixture_is_not_judged_against_the_live_build():
    """Why the order pins are split in two.

    ``_ORDER_BASELINE`` tracks the installed build; a 1.10 table reads
    shallower than a 1.16 one and is not regressed for it. Judging the
    1.10 fixture against the live figure reports a break in a table that
    has not changed since it was frozen.
    """
    import zlib
    d = _FIXTURES / "vanilla110"
    body = zlib.decompress((d / "iteminfo.pabgb.zlib").read_bytes())
    header = zlib.decompress((d / "iteminfo.pabgh.zlib").read_bytes())

    ok, _detail = puc.check_ordered_table(
        "ItemInfo", body, header,
        puc._FIXTURE_ORDER_BASELINE[("vanilla110", "ItemInfo")])
    assert ok

    live, detail = puc.check_ordered_table(
        "ItemInfo", body, header, puc._ORDER_BASELINE["ItemInfo"])
    assert not live and "REGRESSED" in detail, (
        "if the live pin now fits the 1.10 table, the two baselines have "
        "converged and this split is no longer buying anything")
