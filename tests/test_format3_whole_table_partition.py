"""Release-review finding (2026-06-10): adding storeinfo.pabgb and
equipslotinfo.pabgb to _WHOLE_TABLE_TARGETS diverted EVERY intent on
those targets into the dedicated list writers, which only understand
stock_data_list / entries[N].etl_hashes. storeinfo has a PABGB schema,
so scalar intents (e.g. _buyableStockCount) used to flow through the
standard schema walk and produce byte changes; after the diversion
they silently produced nothing.

The fix partitions the batch: writer-supported fields go to the
dedicated writer, everything else falls through to
_intents_to_v2_changes. This test pins that a mod mixing
stock_data_list with a scalar storeinfo edit produces BOTH change
kinds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdumm.storage.database import Database

_BASE = Path(__file__).resolve().parents[1] / "issue_repro" / "183"
_BODY = _BASE / "vanilla" / "storeinfo.pabgb"
_HDR = _BASE / "vanilla" / "storeinfo.pabgh"
_MOD = _BASE / "IHateLacey.json"


def _have_fixtures() -> bool:
    return _BODY.exists() and _HDR.exists() and _MOD.exists()


@pytest.mark.skipif(not _have_fixtures(), reason="183 fixtures absent")
def test_mixed_storeinfo_intents_produce_list_and_scalar_changes(tmp_path):
    from cdumm.engine.format3_apply import expand_format3_into_aggregated

    body = _BODY.read_bytes()
    header = _HDR.read_bytes()

    stock = json.loads(_MOD.read_text(encoding="utf-8"))
    stock_intent = stock["targets"][0]["intents"][0]
    mixed = {
        "modinfo": {"title": "MixedStoreMod", "version": "1.0",
                    "author": "t", "description": "t"},
        "format": 3,
        "targets": [{
            "file": "storeinfo.pabgb",
            "intents": [
                stock_intent,
                {"entry": "Store_Her_General", "key": 3101,
                 "field": "_buyableStockCount", "op": "set", "new": 77},
            ],
        }],
    }
    src = tmp_path / "mixed.json"
    src.write_text(json.dumps(mixed), encoding="utf-8")

    db = Database(tmp_path / "t.db")
    db.initialize()
    db.connection.execute(
        "INSERT INTO mods (id, name, mod_type, enabled, priority, "
        "json_source) VALUES (1, 'MixedStoreMod', 'paz', 1, 1, ?)",
        (str(src),))
    db.connection.commit()

    def extractor(target):
        assert target == "storeinfo.pabgb"
        return body, header

    aggregated: dict = {}
    warnings: list[str] = []
    expand_format3_into_aggregated(
        aggregated, {}, db, extractor, warnings_out=warnings)
    db.close()

    changes = aggregated.get("storeinfo.pabgb", [])
    labels = [c.get("label", "") for c in changes]
    assert any("stock_data_list" in l for l in labels), (
        f"list-writer change missing: labels={labels}, "
        f"warnings={warnings}")
    assert len(changes) >= 2, (
        f"expected list + scalar changes, got {len(changes)}: "
        f"{labels} warnings={warnings}")
    # the companion pabgh rebuild must still be present (list grew)
    assert aggregated.get("storeinfo.pabgh"), "pabgh rebuild missing"
