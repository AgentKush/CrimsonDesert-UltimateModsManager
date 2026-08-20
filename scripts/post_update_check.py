#!/usr/bin/env python3
"""Post-update canary: did a game patch break CDUMM's readers?

Run this after every Crimson Desert update, before anyone reports a mod
doing nothing. It reads the installed game and answers one question per
table: *can CDUMM still decode what mods target?*

Why this exists
---------------
Almost every release in this project's history is reactive -- a patch moves
the bytes, mods silently stop applying, a user reports it days later, and
the layout gets re-derived under time pressure. The information needed to
catch it arrives the moment the patch lands; nothing was looking.

CI cannot look: it has no game install. So this is the thing a human runs
on a machine that has one, and its exit code is the number of problems so
it can gate a release.

Design rule: PRODUCTION ENTRY POINTS ONLY
-----------------------------------------
Every check drives the same call the apply path drives. That rule is not
decoration -- it is the single most expensive mistake in this codebase's
history, made at least four times:

  * #351/#352 drove ``parse_stock_list`` with a constant instead of
    ``locate_stock_list``, read the resulting garbage as a format
    regression, and cost two people a day before both retracted it.
  * measuring skill with ``parse_skill_entry`` instead of ``parse_all``
    skips ``_detect_is_no_alert`` entirely and reports 1424/2013 on a
    build that actually reads 2013/2013 (#355).
  * scoring a table with ``verified_order`` instead of ``select_order``
    skips variant selection and reports iteminfo at 10 fields of 113
    instead of 109.

An internal function fed something it is not meant to receive returns
plausible garbage, and plausible garbage looks exactly like drift.

What a failure here means
-------------------------
These readers refuse rather than write to a position they cannot account
for, so the failure mode is "mods report no changes", not "saves get
damaged". That is why this is worth catching early: the symptom is silent.

Usage::

    python scripts/post_update_check.py --game-dir "E:/.../Crimson Desert"
    python scripts/post_update_check.py --game-dir <dir> --baseline
"""
from __future__ import annotations

import argparse
import sys
import zlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))


def _load(game_dir: Path, name: str) -> bytes:
    """A table's bytes out of the install, via the path the writer uses."""
    from cdumm.engine.v2_to_format3 import _load_vanilla_table
    return _load_vanilla_table(game_dir, name)


def _fixture(version: str, name: str) -> bytes | None:
    p = _REPO / "tests" / "fixtures" / version / (name + ".zlib")
    if not p.exists():
        return None
    return zlib.decompress(p.read_bytes())


# ── per-table checks, each through the production path ────────────────────

def check_skill(body: bytes, header: bytes) -> tuple[bool, str]:
    """``parse_all`` -- which is what runs the layout detection."""
    from cdumm._vendor import skillinfo_parser as sp
    sp._type_id_sizes.clear()
    entries = sp.parse_all(header, body)
    index = sp.parse_skill_pabgh(header)
    bounds = [o for _k, o in index] + [len(body)]
    total = len(index)
    rt = sum(1 for i, rec in enumerate(entries)
             if sp.serialize_entry(rec) == body[index[i][1]:bounds[i + 1]])
    structured = sum(1 for e in entries
                     if e.get("_buffLevelList") is not None)
    ok = len(entries) == total and rt == total
    return ok, (f"{len(entries)}/{total} parsed, {rt}/{total} byte-exact, "
                f"buff walk structured on {structured} "
                f"({structured / max(total, 1):.0%}), "
                f"_isNoAlert={sp._has_is_no_alert}")


def check_storeinfo(body: bytes, header: bytes) -> tuple[bool, str]:
    """``locate_stock_list`` -- no offset constant, the real mechanism."""
    from cdumm.engine.storeinfo_native_parser import (
        StoreListNotFound,
        _entry_payload,
        detect_storeinfo_layout,
        locate_stock_list,
        serialize_stock_list,
    )
    from cdumm.semantic.parser import parse_pabgh_index
    _ks, offs = parse_pabgh_index(header, "storeinfo")
    starts = sorted(offs.values())
    spans = starts + [len(body)]
    # Takes the ENTRY OFFSETS, not the header bytes. Passing the header
    # here silently picks a wrong layout instead of raising.
    layout = detect_storeinfo_layout(body, starts)
    located = records = empty = not_found = ambiguous = bad_rt = 0
    # Widest and narrowest failing entries, to say whether the failures
    # track list size -- the difference between "a few odd stores" and
    # "every store past N records".
    amb_span: list[int] = []
    for key, off in offs.items():
        end = spans[spans.index(off) + 1]
        try:
            recs, s, e = locate_stock_list(
                body, _entry_payload(body, off), end, key, layout)
        except StoreListNotFound as exc:
            # Read the flags, never the message. Substring-matching the
            # text bucketed "ambiguous, refusing" as not-found, which
            # hid the one distinction that decides what the fix is.
            if exc.provably_empty:
                empty += 1
            elif exc.ambiguous:
                ambiguous += 1
                amb_span.append(end - _entry_payload(body, off))
            else:
                not_found += 1
            continue
        except Exception:                       # noqa: BLE001
            not_found += 1
            continue
        located += 1
        records += len(recs)
        if serialize_stock_list(recs, layout) != body[s:e]:
            bad_rt += 1
    ok = (not_found == 0 and ambiguous == 0 and bad_rt == 0
          and located + empty == len(offs))
    msg = (f"layout {layout.label!r}: {located} located + {empty} "
           f"provably empty = {located + empty}/{len(offs)}, "
           f"{records} stock records, {not_found} not-found, "
           f"{ambiguous} ambiguous, {bad_rt} mis-round-tripped")
    if ambiguous:
        msg += (f" [ambiguous entries span {min(amb_span)}-{max(amb_span)} "
                f"payload bytes; the shape parses and round-trips, so "
                f"narrow the scan rather than re-derive the record]")
    return ok, msg


#: Ceiling on the share of iteminfo records carried opaque before this
#: reads as breakage rather than the known tail.
#:
#: Some opacity is correct and permanent: the 1.12 "*_Flag_I" guild flags
#: have never had a schema (#219), which is 64 records on vanilla110 and
#: 14 on vanilla116. Measured across every committed table the figure is
#: 1.0% or lower. The Steam buildid 24773079 break was 84.4%. Nothing
#: observed sits between, so the ceiling is set well clear of the tail and
#: nowhere near the failure -- the count is printed either way, so a rise
#: inside the band is still visible without failing a healthy build.
_ITEM_OPAQUE_CEILING = 0.05


def check_iteminfo_native(body: bytes, header: bytes) -> tuple[bool, str]:
    """``detect_iteminfo_layout`` -> ``parse_iteminfo_from_bytes`` -- the
    reader the item editor writes through.

    This is NOT the same check as the ``iteminfo`` row further down, and
    the difference is the point. That one scores the ``select_order``
    schema walk; this one drives the native parser. Two readers over one
    table, and they fail independently.

    On the Steam buildid 24773079 table the ordered walk reports a
    perfectly healthy "median 109/109 fields, 88% of 6,573 records
    complete" -- while this path carried 5,548 of those 6,573 items
    (84.4%) opaque, because PrefabData had gained a trailing u32 (#369).
    An opaque record can only be patched for ``is_blocked`` and
    ``max_stack_count``, so a mod editing prices imported cleanly,
    validated cleanly, and then silently dropped its edits at apply time.
    The canary said iteminfo was fine throughout.

    So the gate is the opaque share, under ``_ITEM_OPAQUE_CEILING``, plus
    a byte-exact round-trip. Round-trip alone cannot catch this: the
    opaque fallback carries bytes verbatim, which is precisely what keeps
    the round-trip green while the table stops being editable.

    Note what is deliberately NOT gated. ``detect_iteminfo_layout``
    returning ``None`` means *use the default schema*, not *detection
    failed* -- on the CD 1.10 table that is the correct answer, and it
    decodes 6,419 of 6,483 records with the other 64 being the #219 guild
    flags. The genuine "nothing round-trips" case also returns ``None``,
    but it is not told apart by the return value; it is told apart by
    every record going opaque, which the ceiling already catches.
    """
    from cdumm.engine.iteminfo_native_parser import (
        detect_iteminfo_layout,
        parse_iteminfo_from_bytes,
        serialize_iteminfo,
    )
    from cdumm.semantic.parser import parse_pabgh_index
    _ks, offs = parse_pabgh_index(header, "iteminfo")
    starts = sorted(offs.values())
    fields = detect_iteminfo_layout(body, starts)
    items = parse_iteminfo_from_bytes(body, starts, fields=fields)
    total = len(items)
    opaque = sum(1 for it in items if it.get("_opaque_record"))
    frac = opaque / max(total, 1)
    rt = serialize_iteminfo(items, fields=fields) == body

    ok = rt and frac <= _ITEM_OPAQUE_CEILING
    shape = ("default schema" if fields is None
             else f"{len(fields)}-field variant")
    detail = (f"{total - opaque}/{total} items decoded structurally, "
              f"{opaque} opaque ({frac:.1%}), "
              f"round-trip {'byte-exact' if rt else 'MISMATCH'}, "
              f"layout {shape}")
    if frac > _ITEM_OPAQUE_CEILING:
        detail += (f"   [over the {_ITEM_OPAQUE_CEILING:.0%} ceiling: a "
                   f"patch has almost certainly moved a field inside a "
                   f"list element. Opaque items accept only is_blocked "
                   f"and max_stack_count, so price/stat mods will apply "
                   f"as no-ops]")
    return ok, detail


def check_statusgroupinfo(body: bytes, header: bytes) -> tuple[bool, str]:
    from cdumm.engine.statusgroupinfo_writer import parse_record_full
    from cdumm.semantic.parser import parse_pabgh_index
    _ks, offs = parse_pabgh_index(header, "statusgroupinfo")
    starts = sorted(offs.values())
    ok_n = 0
    widths: set[int] = set()
    for o in offs.values():
        i = starts.index(o)
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        got = parse_record_full(body, o, end)
        if got is None:
            continue
        ok_n += 1
        widths.add(got[1][0][1])
    return ok_n == len(offs), (
        f"{ok_n}/{len(offs)} records parse, reverse-index width "
        f"{sorted(widths) or 'n/a'}")


#: Walk depth each ordered table reached when this canary was last
#: verified, as ``(order_label, median_fields)``.
#:
#: Pinned deliberately, because the useful question is "did this CHANGE",
#: not "is this complete". Several of these tables have never walked to the
#: end -- StageInfo reaches 25 of 81 fields and RegionInfo stalls on
#: ``_gimmickAliasPointerList`` -- and those are open modelling gaps, not
#: patch damage. An absolute threshold would print three failures on a
#: perfectly healthy build, and a check that is red every run is one people
#: stop reading. (Same reasoning as the pinned ruff version in
#: .github/workflows/windows-tests.yml.)
#:
#: A DROP is the signal. An improvement is reported and is also worth
#: knowing, since it means someone widened a layout and this line is stale.
#:
#: Verified 2026-08-11, fingerprint 2471644ba4ce9feb.
_ORDER_BASELINE: dict[str, tuple[str, float]] = {
    "ItemInfo": ("cd116", 109),
    "CharacterInfo": ("base", 14),
    "RegionInfo": ("base", 21),
    "StageInfo": ("base", 25),
    "VehicleInfo": ("base", 20),
    "FieldInfo": ("base", 19),
    # Was 2. #362's derived layouts took it to 3/3 on 100% of 35 records,
    # so the floor moves up with it -- a baseline left below the current
    # depth stops detecting the next regression above it.
    "WantedInfo": ("base", 3),
}


def check_ordered_table(table: str, body: bytes, header: bytes,
                        want: tuple[str, float] | None) -> tuple[bool, str]:
    """``select_order`` -- which applies the per-build variant.

    Judged against a pinned ``(order label, median fields)`` rather than an
    absolute bar: a regression means a patch moved something, while a table
    that has always stalled early is a known gap and must not read as
    breakage.

    The pin is passed in rather than looked up, because the live install and
    a committed fixture are pinned to different numbers. ``_ORDER_BASELINE``
    describes the build the game is on; a 1.10 fixture legitimately reads
    shallower than a 1.16 one, and judging it against the live figure would
    print a regression on a table that has never changed. ``None`` means
    unpinned: reported, not gated.
    """
    from cdumm.engine.schema_verify import decode_score, select_order
    label, order = select_order(table, body, header)
    s = decode_score(table, order, body, header)
    detail = (f"order {label!r}: median {s.median_fields:g}/{len(order)} "
              f"fields, {s.frac_reached_last:.0%} of {s.records} records "
              f"complete"
              + (f", stalls on {s.first_bail_field}"
                 if s.first_bail_field else ""))

    if want is None:
        return True, detail + "   [no baseline pinned]"
    want_label, want_median = want
    if s.median_fields < want_median:
        return False, (detail + f"   REGRESSED: was median {want_median:g} "
                                f"on order {want_label!r}")
    if label != want_label:
        return False, (detail + f"   LAYOUT CHANGED: baseline selected "
                                f"{want_label!r}")
    if s.median_fields > want_median:
        detail += (f"   (improved from {want_median:g} -- update "
                   f"_ORDER_BASELINE)")
    return True, detail


#: (label, loader-name, check). Loader-name is the .pabgb basename; the
#: .pabgh is derived. `None` body means the check loads its own.
_CHECKS = [
    ("skill", "skill", check_skill),
    ("storeinfo", "storeinfo", check_storeinfo),
    ("statusgroupinfo", "statusgroupinfo", check_statusgroupinfo),
    # Label deliberately differs from the loader name: iteminfo is read by
    # two independent readers and both get a row. "iteminfo-native" is the
    # editor's parser; the plain "iteminfo" row below is the select_order
    # schema walk. #369 broke one while the other stayed green.
    ("iteminfo-native", "iteminfo", check_iteminfo_native),
]

#: Tables with a verified field order, checked through select_order.
_ORDERED = [("iteminfo", "ItemInfo"), ("characterinfo", "CharacterInfo"),
            ("regioninfo", "RegionInfo"), ("stageinfo", "StageInfo"),
            ("vehicleinfo", "VehicleInfo"), ("fieldinfo", "FieldInfo"),
            ("wantedinfo", "WantedInfo")]


# ── the same checks, against the bytes committed to the repo ──────────────
#
# The live pass above needs a game install. This one needs nothing, which
# is the point: it separates "the patch moved the bytes" from "we broke the
# reader", and it is the only half that can run in CI.
#
# It used to walk a hardcoded ("vanilla113", "vanilla115", "vanilla116")
# and only the three hand-written checks -- so vanilla110 was never read,
# vanilla1161 was never read (the NEWEST table we have, frozen for the
# 15 Aug patch that #365/#366 turned on), and no ordered table was ever
# scored against a fixture at all. Five of the eleven decodes we have bytes
# for were being exercised.
#
# Discovering the directories instead means the next capture is covered by
# dropping it in: `make_table_fixture.py --version 1162 --all` writes
# tests/fixtures/vanilla1162/, and this reads it without an edit here.


def fixture_versions() -> list[str]:
    """Every committed fixture directory. Sorted so runs are comparable."""
    return sorted(p.name for p in (_REPO / "tests" / "fixtures").glob("vanilla*")
                  if p.is_dir())


#: ``(fixture version, table)`` pairs verified to decode with the code as
#: it stands. A pair listed here GATES: it decoded once, so a failure is a
#: regression in this repo and nothing else.
#:
#: A pair absent from this set is reported and does not gate, because a
#: fixture nobody has verified yet is exactly what a fresh capture is --
#: and a brand-new table failing to decode is the game patch this canary
#: exists to report, not a code regression. Add the pair once its numbers
#: have been looked at.
#:
#: The second element is the ROW LABEL, not the loader name -- iteminfo
#: contributes both "iteminfo-native" and "iteminfo" off one pair of files.
#:
#: Verified 2026-08-20, after merging upstream v3.15.0 (the fixes for
#: Steam buildid 24773079).
_FIXTURE_GREEN = frozenset({
    ("vanilla110", "iteminfo"), ("vanilla110", "iteminfo-native"),
    ("vanilla113", "skill"), ("vanilla113", "storeinfo"),
    ("vanilla113", "iteminfo"), ("vanilla113", "iteminfo-native"),
    ("vanilla113", "characterinfo"),
    ("vanilla115", "statusgroupinfo"),
    ("vanilla116", "skill"), ("vanilla116", "storeinfo"),
    ("vanilla116", "iteminfo"), ("vanilla116", "iteminfo-native"),
    ("vanilla1161", "storeinfo"),
    # The 17 Aug build. iteminfo-native reads 6,573/6,573 with the cd116b
    # layout #369 added; under cd116 it was 5,548 opaque (84.4%).
    ("vanilla_b24773079", "iteminfo"),
    ("vanilla_b24773079", "iteminfo-native"),
})

#: Per-fixture pins for the ordered tables, measured 2026-08-18.
#:
#: These are NOT ``_ORDER_BASELINE``. That one tracks the installed build;
#: these track a specific frozen table, and they differ -- 1.10's iteminfo
#: reads 64 of 113 fields on the base order and bails at
#: ``_enchantDataList``, while 1.16's reads 109 of 109 on the ``cd116``
#: order. Both are correct for their build. Pinning them separately is
#: what lets an old fixture be a regression test rather than noise.
_FIXTURE_ORDER_BASELINE: dict[tuple[str, str], tuple[str, float]] = {
    ("vanilla110", "ItemInfo"): ("base", 64),
    ("vanilla113", "ItemInfo"): ("base", 110),
    ("vanilla113", "CharacterInfo"): ("base", 14),
    ("vanilla116", "ItemInfo"): ("cd116", 109),
    # Identical to vanilla116's, and that is the finding, not a typo: the
    # ordered walk did not move at all across the patch that made 84% of
    # this same table opaque to the native parser. See
    # check_iteminfo_native.
    ("vanilla_b24773079", "ItemInfo"): ("cd116", 109),
}


def run_fixture_checks(
        versions: list[str] | None = None) -> list[tuple[str, bool, str, bool]]:
    """Every check that has committed bytes to run against.

    Returns ``(label, ok, detail, gating)`` rows in fixture order. Nothing
    is printed and nothing is skipped silently: a table with no fixture on
    a given build simply has no row.

    ``versions`` narrows the sweep to named fixture directories. The canary
    always wants all of them; it is there so a caller checking the *shape*
    of a row does not have to pay for a full decode to get one -- the 1.10
    iteminfo walk alone is 25 seconds.
    """
    rows: list[tuple[str, bool, str, bool]] = []
    for ver in (versions if versions is not None else fixture_versions()):
        # (row label, loader name, fn). The two are not the same thing:
        # iteminfo is read by two different readers, so it contributes two
        # rows off one pair of files, and the label is what keeps them
        # apart in _FIXTURE_GREEN.
        todo: list[tuple[str, str, object]] = list(_CHECKS)
        todo += [
            (name, name, lambda b, h, _c=cls, _v=ver: check_ordered_table(
                _c, b, h, _FIXTURE_ORDER_BASELINE.get((_v, _c))))
            for name, cls in _ORDERED]
        for label, name, fn in todo:
            body = _fixture(ver, f"{name}.pabgb")
            header = _fixture(ver, f"{name}.pabgh")
            if body is None or header is None:
                continue
            gating = (ver, label) in _FIXTURE_GREEN
            try:
                ok, detail = fn(body, header)
            except Exception as exc:            # noqa: BLE001
                ok = False
                detail = f"{type(exc).__name__}: {str(exc)[:70]}"
            rows.append((f"{ver}/{label}", ok, detail, gating))
    return rows


def build_stamp(game_dir: Path) -> str:
    from cdumm.engine.version_detector import detect_game_version
    exe = game_dir / "bin64" / "CrimsonDesert.exe"
    fp = detect_game_version(game_dir) or "?"
    when = ""
    if exe.exists():
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(
            exe.stat().st_mtime, tz=_dt.timezone.utc
        ).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"fingerprint {fp}   exe {when}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-dir", required=True, type=Path)
    ap.add_argument("--baseline", action="store_true",
                    help="also run the committed fixtures, so a live "
                         "failure can be told apart from a code regression")
    args = ap.parse_args(argv)
    game = args.game_dir
    if not (game / "bin64").is_dir():
        print(f"not a Crimson Desert install: {game}")
        return 2

    print(f"game: {game}")
    print(f"      {build_stamp(game)}")
    print()
    problems: list[str] = []

    def run(label: str, fn, body, header) -> None:
        try:
            ok, detail = fn(body, header)
        except Exception as e:                  # noqa: BLE001
            print(f"  {label:<18} ERROR  {type(e).__name__}: {str(e)[:70]}")
            problems.append(f"{label}: {type(e).__name__}")
            return
        print(f"  {label:<18} {'ok  ' if ok else 'FAIL'}  {detail}")
        if not ok:
            problems.append(f"{label}: {detail}")

    print("live install:")
    for label, name, fn in _CHECKS:
        try:
            body = _load(game, name + ".pabgb")
            header = _load(game, name + ".pabgh")
        except Exception as e:                  # noqa: BLE001
            print(f"  {label:<18} ERROR  cannot load: {str(e)[:60]}")
            problems.append(f"{label}: load failed")
            continue
        run(label, fn, body, header)

    for name, cls in _ORDERED:
        try:
            body = _load(game, name + ".pabgb")
            header = _load(game, name + ".pabgh")
        except Exception as e:                  # noqa: BLE001
            print(f"  {name:<18} ERROR  cannot load: {str(e)[:60]}")
            problems.append(f"{name}: load failed")
            continue
        run(name,
            lambda b, h, _c=cls: check_ordered_table(
                _c, b, h, _ORDER_BASELINE.get(_c)),
            body, header)

    if args.baseline:
        print("\ncommitted fixtures (a failure here is a CODE regression, "
              "not a game patch):")
        unpinned = 0
        for label, ok, detail, gating in run_fixture_checks():
            if not gating:
                unpinned += 1
                mark = "NEW "
                detail += "   [unpinned -- add to _FIXTURE_GREEN once read]"
            else:
                mark = "ok  " if ok else "FAIL"
            print(f"  {label:<34} {mark}  {detail}")
            if gating and not ok:
                problems.append(f"{label}: {detail}")
        if unpinned:
            print(f"\n  {unpinned} fixture check(s) are new and did not gate. "
                  f"A fresh capture failing here is the game patch, not a "
                  f"code regression -- read the numbers, then pin them.")

    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S) -- a game patch has very likely "
              f"moved something:")
        for p in problems:
            print(f"  * {p}")
        print("\nThese readers refuse rather than write to a position they "
              "cannot account for, so the symptom is mods reporting no "
              "changes -- not damaged saves.")
    else:
        print("No problems. Every checked table decodes on this build.")
    return len(problems)


if __name__ == "__main__":
    raise SystemExit(main())
