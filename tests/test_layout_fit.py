"""The Game Data grid must say when it cannot read a table's layout.

Measured on game buildid 24613230, walking all 135 tables in an install:
38 tables whose only decoder is the generic schema frame no record
correctly, 61,795 records' worth. Nothing there is editable -- the grid
refuses every field when ``verified_fields is None`` -- so it is not
dangerous. But it still renders a grid, and a grid built from a layout that
is wrong the moment anyone checks reads as data. ``layout_fit`` is what
labels it.

The distinction these tests exist to pin, because getting it wrong produces
a warning on the app's best tables:

  * ``exact`` (byte-exact tiling) is the right gate for DERIVING and
    SHIPPING a layout, and the derivation pipeline is held to it.
  * ``complete`` (the walk gets through the whole field list) is the right
    signal for DISPLAY. A schema that models the first N fields correctly
    and leaves unmodelled tail bytes shows N correct columns and still
    fails tiling -- iteminfo, characterinfo, stageinfo, regioninfo and
    storeinfo all tile 0 of 200 sampled records.

Only ``complete == 0`` earns the strong warning: the walk dies partway, so
every column after that point is noise.
"""
from __future__ import annotations

import pytest

from cdumm.engine.schema_verify import LayoutFit, layout_fit
from tests.fixture_loaders import load_vanilla113


def _fit(**kw) -> LayoutFit:
    base = {"records": 100, "checked": 10, "complete": 10, "exact": 10,
            "schema_fields": 5, "median_fields": 5}
    base.update(kw)
    return LayoutFit(**base)


# ── the verdict properties ───────────────────────────────────────────────

def test_all_records_walking_the_whole_field_list_is_usable():
    f = _fit(complete=10, checked=10)
    assert f.usable
    assert not f.broken
    assert f.ratio == 1.0


def test_no_record_completing_is_broken():
    """The strong signal: the schema cannot frame this build's data."""
    f = _fit(complete=0, exact=0, median_fields=2, schema_fields=15)
    assert f.broken
    assert not f.usable
    assert f.ratio == 0.0


def test_a_partial_read_is_neither_usable_nor_broken():
    """Most records fine, some not -- a caution, not a condemnation.

    iteminfo is the real case: 173 of 200 sampled records walk all 109
    fields. Calling that broken would slander a table whose grid is
    overwhelmingly correct.
    """
    f = _fit(complete=173, checked=200)
    assert not f.usable
    assert not f.broken
    assert f.ratio == pytest.approx(0.865)


def test_completing_without_tiling_is_still_usable():
    """The distinction the whole feature turns on.

    A schema can walk every field it models and still leave bytes at the end
    of the record, because the table has fields the schema does not model.
    The columns it DOES show are fine, so this must not warn as if the
    layout were wrong.
    """
    f = _fit(complete=200, checked=200, exact=0)
    assert f.usable
    assert not f.broken
    assert f.exact == 0


def test_nothing_checked_is_neither_verdict():
    """An empty sample must not read as a clean bill of health."""
    f = _fit(checked=0, complete=0, exact=0)
    assert not f.usable
    assert not f.broken
    assert f.ratio == 0.0


# ── against a committed fixture ──────────────────────────────────────────

def test_layout_fit_reports_on_a_real_table():
    """It must return a populated verdict for a real table's bytes.

    Uses the committed vanilla 1.13 iteminfo fixture so this runs in CI
    with no game install.
    """
    body = load_vanilla113("iteminfo.pabgb")
    header = load_vanilla113("iteminfo.pabgh")
    fit = layout_fit("iteminfo", body, header)
    assert fit is not None, "iteminfo has a schema; the check must apply"
    assert fit.records > 1000
    assert fit.checked > 0
    assert fit.schema_fields > 0
    # Whatever the verdict, the counts must be internally consistent:
    # a record cannot tile without also completing.
    assert fit.exact <= fit.complete <= fit.checked


def test_sampling_is_bounded_and_spread():
    """The check runs on the display path, so it must stay cheap.

    actionpointinfo has 28,958 records and the answer does not get more
    true after the two-hundredth.
    """
    body = load_vanilla113("iteminfo.pabgb")
    header = load_vanilla113("iteminfo.pabgh")
    fit = layout_fit("iteminfo", body, header, max_samples=25)
    assert fit is not None
    assert fit.checked <= 25


def test_unknown_table_returns_none_rather_than_broken():
    """No schema means "unknown", never "broken".

    A table CDUMM has no schema for must not be labelled unreadable -- the
    caller has to be able to tell those two apart.
    """
    body = load_vanilla113("iteminfo.pabgb")
    header = load_vanilla113("iteminfo.pabgh")
    assert layout_fit("NoSuchTableInfo", body, header) is None
