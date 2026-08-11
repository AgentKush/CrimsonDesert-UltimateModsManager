"""The verification harness must have teeth AND not cry wolf.

Every gate that unlocks new tables (a licensed reader-order parser, an ASI
reflection dump) will feed CDUMM a claimed field order. The harness is what
decides whether to believe it. So the harness itself has to be proven:

  * it must PASS the ground truth (no false rejections), and
  * it must REJECT a wrong order (real teeth) -- by identity always, and by
    byte-decode for gross errors.

The one honest caveat, pinned below: the byte-decode score has a blind spot
(same-width fields swapped upstream of the walker's stall decode
identically). Order-identity is what covers it. Both are tested.
"""
from __future__ import annotations

import random

import pytest

from tests.fixture_loaders import load_vanilla113

from cdumm.engine.schema_verify import (
    DecodeScore, decode_score, relative_order_matches,
    tables_with_verified_order, verified_order, verify_order_source,
    verify_order_source_relative)

ITEM = "ItemInfo"


def _fixture():
    return load_vanilla113("iteminfo.pabgb"), load_vanilla113("iteminfo.pabgh")


def _ground_truth() -> dict[str, list[str]]:
    return {t: verified_order(t) for t in tables_with_verified_order()}


# ── ground truth: the harness must not reject what's real ────────────────

#: The seven tables whose order was reverse engineered by hand.
HAND_RE = {"CharacterInfo", "FieldInfo", "ItemInfo", "RegionInfo",
           "StageInfo", "VehicleInfo", "WantedInfo"}

#: Thirteen tables whose order was DERIVED and proven by exact tiling
#: (tools/derive_table_layout.py, game buildid 24613230). Their order is
#: byte-exact on every record, which is what this harness means by verified
#: order -- but their `_verified_fields` is empty, because tiling proves
#: structure and not semantics.
DERIVED = {"AIDialogTypeInfo", "BreakableObjectInfo", "CategoryGroupInfo",
           "DetectInfo", "DialogVoiceInfo", "FailMessageInfo",
           "GameAdviceGroupInfo", "GamePlayVariableInfo",
           "GimmickEventTableInfo", "JobInfo", "LocalStringInfo",
           "MaterialRelationInfo", "VibratePatternInfo"}


def test_the_verified_order_set_is_exactly_what_we_expect():
    """A change here must be deliberate, not accidental.

    It grew from 7 to 20 deliberately: the 13 in DERIVED have an
    `_ordered_fields` proven by exact tiling on 100% of their records, so a
    candidate order source now has to reproduce them too. That is a real
    strengthening of the gate, and the reason it is spelled out rather than
    left as a count.
    """
    tabs = tables_with_verified_order()
    assert ITEM in tabs
    assert set(tabs) == HAND_RE | DERIVED
    assert len(tabs) == 20


def test_ground_truth_passes_itself():
    body, header = _fixture()
    rep = verify_order_source(_ground_truth(), {ITEM: (body, header)})
    assert rep.trustworthy
    assert len(rep.passed) == rep.known_tables == len(HAND_RE | DERIVED)
    item = next(r for r in rep.results if r.table == ITEM)
    assert item.order_matches is True
    assert item.decode_ok is True


def test_decode_score_anchors_ground_truth_to_real_bytes():
    """"Verified" must mean "decodes real records", not "asserted".

    The baseline decode of the ground-truth order over the committed
    fixture has to actually reach into all 6508 records, not zero.
    """
    body, header = _fixture()
    base = decode_score(ITEM, verified_order(ITEM), body, header)
    assert base.records == 6508
    assert base.median_fields >= 10, (
        "the ground-truth ItemInfo order barely decodes the real table -- "
        "either the fixture or the order is wrong")


# ── teeth: the harness must reject wrong orders ──────────────────────────

def test_scrambled_order_is_rejected_by_both_gates():
    body, header = _fixture()
    truth = verified_order(ITEM)
    scrambled = truth[:]
    random.Random(42).shuffle(scrambled)

    cand = _ground_truth()
    cand[ITEM] = scrambled
    rep = verify_order_source(cand, {ITEM: (body, header)})

    assert not rep.trustworthy
    item = next(r for r in rep.results if r.table == ITEM)
    assert item.order_matches is False              # identity gate
    assert item.candidate.median_fields < item.baseline.median_fields, (
        "a fully scrambled order should decode strictly worse than truth")
    assert item.passed is False


def test_single_adjacent_swap_is_caught_by_identity():
    """A one-field swap is the subtle error a real parser might make.

    This is the honest edge: two same-width fields swapped upstream of the
    walker's stall can decode to the SAME byte count, so the decode score
    may not flag it. Identity always does. This test pins that division of
    labour -- if decode ever gets sharp enough to catch it too, great, but
    the guarantee lives in identity.
    """
    body, header = _fixture()
    truth = verified_order(ITEM)
    swapped = truth[:]
    swapped[5], swapped[6] = swapped[6], swapped[5]

    cand = _ground_truth()
    cand[ITEM] = swapped
    rep = verify_order_source(cand, {ITEM: (body, header)})

    item = next(r for r in rep.results if r.table == ITEM)
    assert item.order_matches is False, "identity must catch a single swap"
    assert item.passed is False
    assert not rep.trustworthy


def test_missing_field_is_rejected():
    body, header = _fixture()
    truth = verified_order(ITEM)
    cand = _ground_truth()
    cand[ITEM] = truth[:-1]                          # drop the last field
    rep = verify_order_source(cand, {ITEM: (body, header)})
    item = next(r for r in rep.results if r.table == ITEM)
    assert item.order_matches is False
    assert item.passed is False


def test_extra_field_is_rejected():
    truth = verified_order(ITEM)
    cand = _ground_truth()
    cand[ITEM] = truth + ["_totallyMadeUpField"]
    rep = verify_order_source(cand)                  # order check alone
    item = next(r for r in rep.results if r.table == ITEM)
    assert item.order_matches is False
    assert item.passed is False


# ── coverage semantics ───────────────────────────────────────────────────

def test_uncovered_tables_do_not_count_as_passed():
    """A candidate that only speaks for some tables is judged only on
    those -- but an EMPTY candidate is not vacuously trustworthy."""
    rep = verify_order_source({})
    assert rep.covered == []
    assert rep.trustworthy is False
    assert all(r.order_matches is None for r in rep.results)


def test_partial_candidate_is_trustworthy_on_what_it_covers():
    cand = {ITEM: verified_order(ITEM)}              # one table only
    rep = verify_order_source(cand)
    assert len(rep.covered) == 1
    assert rep.trustworthy is True                  # got its one table right
    # ...but reports every known table, hand-RE'd and derived alike
    assert len(rep.results) == len(HAND_RE | DERIVED)


def test_partial_candidate_still_fails_if_its_one_table_is_wrong():
    cand = {ITEM: list(reversed(verified_order(ITEM)))}
    rep = verify_order_source(cand)
    assert rep.trustworthy is False


# ── DecodeScore unit ─────────────────────────────────────────────────────

def test_decode_score_at_least_ordering():
    a = DecodeScore(100, 11.0, 0.0, None)
    b = DecodeScore(100, 4.0, 0.0, "x")
    assert a.at_least(b)
    assert not b.at_least(a)


# ── superset candidates: relative_order_matches ──────────────────────────
#
# A reflection-derived order names the fields the binary has an error string
# for. That set overlaps the shipped schema without equalling it in either
# direction, so `cand == truth` rejects it for being differently shaped
# rather than for being wrong. These pin the weaker check that does carry
# information — and pin that it is genuinely weaker, so it cannot be
# mistaken for the real gate.


def test_relative_order_passes_the_ground_truth():
    r = relative_order_matches(ITEM, verified_order(ITEM))
    assert r.matches
    assert r.complete
    assert r.shared == verified_order(ITEM)
    assert r.candidate_only == [] and r.verified_only == []


def test_relative_order_still_rejects_a_scrambled_order():
    r = relative_order_matches(ITEM, list(reversed(verified_order(ITEM))))
    assert not r.matches
    assert r.first_divergence == 0


def test_relative_order_catches_one_field_moved_to_the_end():
    """The cold-block failure mode, in the abstract.

    One field relocated to the end is the whole bug the Windows extractor
    had, so the check must fail on exactly that and not shrug it off as a
    shape difference.
    """
    truth = verified_order(ITEM)
    moved = truth[:5] + truth[6:] + [truth[5]]
    r = relative_order_matches(ITEM, moved)
    assert not r.matches
    assert r.first_divergence == 5
    assert r.complete, "no field was dropped, only moved"


def test_relative_order_tolerates_extra_names_but_reports_them():
    truth = verified_order(ITEM)
    cand = ["_someFieldTheSchemaLacks"] + truth + ["_andAnother"]
    r = relative_order_matches(ITEM, cand)
    assert r.matches
    assert r.candidate_only == ["_someFieldTheSchemaLacks", "_andAnother"]
    assert r.complete


def test_a_matching_but_incomplete_candidate_is_not_complete():
    """Matching on shared names does not earn `_ordered_fields`.

    A source that omits fields cannot place them, so `complete` is what
    separates "corroborates the order" from "can be the order".
    """
    truth = verified_order(ITEM)
    r = relative_order_matches(ITEM, truth[:-3])
    assert r.matches
    assert not r.complete
    assert r.verified_only == truth[-3:]


def test_relative_order_is_weaker_than_identity_and_says_so():
    """A candidate the strict gate rejects can pass the relative one.

    That is the point, and it is also the risk: this asserts the gap
    exists so nobody swaps one for the other by accident.
    """
    truth = verified_order(ITEM)
    cand = truth[:20]                             # a correct prefix only
    assert verify_order_source({ITEM: cand}).trustworthy is False
    assert relative_order_matches(ITEM, cand).matches is True


def test_relative_order_raises_for_a_table_with_no_verified_order():
    with pytest.raises(KeyError):
        relative_order_matches("NoSuchTableInfo", ["_a", "_b"])


def test_verify_relative_skips_uncovered_tables_rather_than_failing_them():
    results = verify_order_source_relative({ITEM: verified_order(ITEM)})
    assert [r.table for r in results] == [ITEM]
    assert all(r.matches for r in results)
    assert verify_order_source_relative({}) == []
