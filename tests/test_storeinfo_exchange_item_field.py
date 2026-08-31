"""GitHub #393: donr484's Greylight Special sets ``exchange_item_info_for_buy``
on the bond and contribution shops (the currency the player pays with).
CDUMM's storeinfo schema knows the engine field ``_exchangeItemInfoForBuy``
only as an opaque ``reader_4B``, so the validator refused it and the mod
silently applied nothing for those ten stores.

field_schema/storeinfo.json now pins it at payload offset 0 as a u32
(derivation in the file). These tests hold that against the committed
CD 2.0 storeinfo bytes.
"""
from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from cdumm.engine.field_schema import load_field_schema
from cdumm.engine.format3_handler import validate_intents
from cdumm.semantic.parser import _parse_entry_header, parse_pabgh_index
from tests.fixture_loaders import (
    has_vanilla_b24934353,
    load_vanilla_b24934353,
)

BOND_SHOP = 907          # Store_Her_Bond_Shop, pays with Hernand_Bond
HERNAND_BOND = 1004910
CAMP_BUTCHER = 4803      # Store_Camp_AnimalButcher: buy Copper / sell Camp_Food
COPPER, CAMP_FOOD = 1, 12


def _intent(key, field, new):
    return SimpleNamespace(key=key, entry="", field=field, op="set", new=new,
                           match=None, clone=None, old=None)


def test_field_schema_ships_the_exchange_fields():
    fs = load_field_schema("storeinfo")
    assert "exchange_item_info_for_buy" in fs
    assert fs["exchange_item_info_for_buy"].rel_offset == 0
    assert fs["exchange_item_info_for_buy"].data_type.lower() == "u32"
    assert fs["exchange_item_info_for_sell"].rel_offset == 8


def test_validator_accepts_the_field():
    v = validate_intents(
        "storeinfo.pabgb",
        [_intent(BOND_SHOP, "exchange_item_info_for_buy", 1)])
    assert len(v.supported) == 1, v.skipped


@pytest.mark.skipif(not has_vanilla_b24934353("storeinfo.pabgb"),
                    reason="CD 2.0 storeinfo fixture absent")
def test_offsets_match_the_vanilla_currency_keys():
    body = load_vanilla_b24934353("storeinfo.pabgb")
    header = load_vanilla_b24934353("storeinfo.pabgh")
    ks, offs = parse_pabgh_index(header, "storeinfo")
    fs = load_field_schema("storeinfo")
    buy, sell = fs["exchange_item_info_for_buy"], fs["exchange_item_info_for_sell"]

    def read(key, entry):
        _eid, _name, payload = _parse_entry_header(body, offs[key], ks)
        return struct.unpack_from("<I", body, payload + entry.rel_offset)[0]

    assert read(BOND_SHOP, buy) == HERNAND_BOND
    assert read(BOND_SHOP, sell) == HERNAND_BOND
    # The four camp stores are what fix which offset is which: the
    # animal butcher takes Copper from the player and hands back Camp
    # Food, so +0 is the buy currency and +8 the sell currency.
    assert read(CAMP_BUTCHER, buy) == COPPER
    assert read(CAMP_BUTCHER, sell) == CAMP_FOOD
