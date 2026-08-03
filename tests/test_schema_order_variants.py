"""The walker's field order has to be chosen, not assumed.

There are two independent iteminfo readers: the hand-written
``iteminfo_native_parser``, and the generic walker driven by
``_ordered_fields``. Fixing one does nothing for the other, and the
walker is what the Format 3 generic path uses -- ``match``, nested
targets, the field grid.

CD 1.16 removed two ItemInfo fields. A removed field is the worst drift
there is: the walker keeps reading, takes the NEXT field's bytes as the
missing one, and desyncs everything after it. Measured on the live 1.16
table it fell from 110 of 113 fields to 10 -- the "11-field iteminfo
grid" wall returning one game version later, and no test went red.

So the order is now scored against the table in front of us, exactly as
iteminfo's record layout and storeinfo's already are.
"""
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from cdumm.engine import schema_verify as sv
from cdumm.semantic.parser import get_schema

_FX = Path(__file__).resolve().parent / "fixtures"


def _fixture(ver: str, name: str):
    d = _FX / f"vanilla{ver}"
    p = d / f"{name}.pabgb.zlib"
    if not p.exists():
        return None
    return (zlib.decompress(p.read_bytes()),
            zlib.decompress((d / f"{name}.pabgh.zlib").read_bytes()))


@pytest.fixture(autouse=True)
def _clear_cache():
    sv._ORDER_CACHE.clear()
    yield
    sv._ORDER_CACHE.clear()


# ── the declared order still wins where it should ───────────────────────

@pytest.mark.parametrize("ver", ["110", "113"])
def test_older_builds_keep_the_declared_order(ver):
    """Selection must not just always prefer the newest variant. On the
    builds the declared order was reverse-engineered against, it wins."""
    t = _fixture(ver, "iteminfo")
    if t is None:
        pytest.skip(f"no {ver} iteminfo fixture")
    label, order = sv.select_order("ItemInfo", t[0], t[1])
    assert label == "base"
    assert order == sv.verified_order("ItemInfo")


def test_the_cd116_variant_would_decode_worse_on_1_13():
    """Why the 1.16 variant is safe to offer: on a 1.13 table it is much
    worse, so it can never be selected there."""
    t = _fixture("113", "iteminfo")
    if t is None:
        pytest.skip("no 1.13 iteminfo fixture")
    base = sv.verified_order("ItemInfo")
    dropped = dict(sv.ORDER_VARIANTS["ItemInfo"])["cd116"]
    variant = [f for f in base if f not in dropped]

    b = sv.decode_score("ItemInfo", base, t[0], t[1])
    v = sv.decode_score("ItemInfo", variant, t[0], t[1])
    assert v.median_fields < b.median_fields


def test_the_variant_drops_the_two_removed_and_the_two_unreachable():
    """Guards the reason, not just the result.

    Two of these fields are GONE from the 1.16 binary; two still exist
    but sit inside a region 1.16 wrapped in opaque bytes, which a
    field-NAME order cannot express. Someone trimming this list back to
    "the fields 1.16 removed" would halve the walk depth, so the intent
    is pinned here.
    """
    dropped = set(dict(sv.ORDER_VARIANTS["ItemInfo"])["cd116"])
    assert dropped == {
        "_inventoryInfo",              # removed in 1.16
        "_gimmickVisualPrefabDataList",  # removed in 1.16
        "_repairDataList",             # exists; inside the wrapped region
        "_prefabDataList",             # exists; inside the wrapped region
    }


# ── tables with no variants are untouched ───────────────────────────────

@pytest.mark.parametrize("table", ["CharacterInfo", "StageInfo", "WantedInfo",
                                   "FieldInfo", "RegionInfo", "VehicleInfo"])
def test_tables_without_variants_are_a_pure_no_op(table):
    """The blast radius. Every table that declares no variant must get
    back the very same schema object ``get_schema`` returns — not an
    equal one, the same one — so nothing about their behaviour can have
    changed."""
    assert table not in sv.ORDER_VARIANTS
    assert sv.schema_for_table(table, b"", b"") is get_schema(table)
    assert sv.select_order(table, b"", b"")[0] == "base"


def test_selection_is_cached_per_table_body():
    """Scoring walks every record of a 6 MB table, so it must happen once
    per body, not once per intent."""
    t = _fixture("113", "iteminfo")
    if t is None:
        pytest.skip("no 1.13 iteminfo fixture")
    calls = []
    real = sv._select_order_uncached

    def counted(table, body, header):
        calls.append(table)
        return real(table, body, header)

    sv._select_order_uncached = counted
    try:
        for _ in range(5):
            sv.select_order("ItemInfo", t[0], t[1])
    finally:
        sv._select_order_uncached = real
    assert calls == ["ItemInfo"]


def test_a_variant_only_wins_by_decoding_strictly_better(monkeypatch):
    """A variant that ties with the declared order must NOT be selected.
    Ties are how a wrong order sneaks in — the declared one is the one
    that was actually verified against real bytes."""
    t = _fixture("113", "iteminfo")
    if t is None:
        pytest.skip("no 1.13 iteminfo fixture")
    # A "variant" that drops nothing is identical to the base, so it ties.
    monkeypatch.setitem(sv.ORDER_VARIANTS, "ItemInfo", (("twin", ()),))
    sv._ORDER_CACHE.clear()
    assert sv.select_order("ItemInfo", t[0], t[1])[0] == "base"
