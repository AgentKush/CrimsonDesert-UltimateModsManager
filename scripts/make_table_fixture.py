#!/usr/bin/env python3
"""Freeze a table out of an installed game into a committable fixture.

Every layout regression in this project's history was diagnosed twice:
once by whoever had the game installed, and again by whoever had to
reproduce it without one. `tests/fixtures/vanilla116/` exists because
somebody did this by hand on 2 Aug, and every storeinfo test since has
been able to run in CI. When the next patch moves a layout, the table it
moved is on exactly one machine, and nothing can be proven anywhere else
until it is committed.

This is that step, as one command instead of a manual zlib round-trip::

    python scripts/make_table_fixture.py --game-dir "E:/.../Crimson Desert" \\
        --table storeinfo --version 117

writes ``tests/fixtures/vanilla117/storeinfo.pabgb.zlib`` and
``storeinfo.pabgh.zlib`` -- the same names, and the same zlib framing,
that ``post_update_check.py`` and the layout tests already read.

It pulls the bytes through ``_load_vanilla_table``, the production
loader the apply path uses, rather than reading files off disk. That is
the same PRODUCTION ENTRY POINTS ONLY rule the canary is built on: a
table fetched some other way can differ from the one the writer sees,
and a fixture that disagrees with production is worse than no fixture.

Sizes are printed so the result can be sanity-checked against the
canary's report before committing (#365: storeinfo went 432 -> 437
entries and 866,221 -> 841,452 bytes across the 15 Aug patch).
"""
from __future__ import annotations

import argparse
import sys
import zlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

#: Matches the fixture layout the tests already glob for.
_FIXTURE_ROOT = _REPO / "tests" / "fixtures"

#: Every table that already has a committed fixture on some build, which
#: is the set worth re-freezing when a patch lands. Derived once and
#: written down rather than globbed at runtime: a table that stops being
#: captured should be a visible edit here, not a silent gap.
#:
#: Note this is WIDER than what post_update_check.py checks. The canary
#: drives three tables (skill, storeinfo, statusgroupinfo) because those
#: are the ones with a hand-written check; the other twelve have fixtures
#: and layout tests but no canary entry, so a patch that moves them is
#: caught only when the test suite runs against a new fixture. Capturing
#: all of them is what makes that possible.
ALL_TABLES = (
    "aieventtableinfo",
    "buffinfo",
    "characterappearanceindexinfo",
    "characterinfo",
    "dropsetinfo",
    "equipslotinfo",
    "interactioninfo",
    "inventory",
    "iteminfo",
    "knowledgeinfo",
    "skill",
    "statusgroupinfo",
    "statusinfo",
    "storeinfo",
    "stringinfo",
)

#: zlib level 9: these are committed binaries, so size beats speed, and
#: the existing vanilla116 fixtures were written the same way.
_LEVEL = 9


def _load(game_dir: Path, name: str) -> bytes:
    from cdumm.engine.v2_to_format3 import _load_vanilla_table
    return _load_vanilla_table(game_dir, name)


def _freeze(game_dir: Path, table: str, out_dir: Path,
            force: bool) -> tuple[int, str | None]:
    """Freeze one table's two files. Returns ``(raw_bytes, error)``."""
    total = 0
    for suffix in ("pabgb", "pabgh"):
        name = f"{table}.{suffix}"
        dest = out_dir / f"{name}.zlib"
        if dest.exists() and not force:
            return total, f"{dest.relative_to(_REPO)} exists (use --force)"
        try:
            raw = _load(game_dir, name)
        except Exception as exc:                        # noqa: BLE001
            # Naming the table and the loader beats a bare traceback:
            # the usual cause is a table that this build renamed or that
            # lives in a PAZ the vanilla index does not cover.
            return total, (f"could not load {name} via _load_vanilla_table: "
                           f"{type(exc).__name__}: {exc}")
        if not raw:
            return total, f"{name} loaded as zero bytes"
        dest.write_bytes(zlib.compress(raw, _LEVEL))
        total += len(raw)
        print(f"  {name:<32} {len(raw):>10,} raw -> "
              f"{dest.stat().st_size:>9,} on disk")
    return total, None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Freeze game tables into tests/fixtures/vanilla<version>/")
    ap.add_argument("--game-dir", required=True, type=Path,
                    help="the Crimson Desert install root")
    ap.add_argument("--table", action="append", metavar="NAME",
                    help="table basename, e.g. storeinfo (no extension). "
                         "Repeatable. Omit with --all to take every known "
                         "table.")
    ap.add_argument("--all", action="store_true",
                    help=f"freeze all {len(ALL_TABLES)} tables that have ever "
                         f"had a committed fixture — the set worth "
                         f"re-capturing after a patch")
    ap.add_argument("--version", required=True,
                    help="fixture version label, e.g. 1162 -> vanilla1162/")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing fixture")
    ap.add_argument("--keep-going", action="store_true",
                    help="with --all, continue past a table that fails to "
                         "load and report the failures at the end. A table "
                         "the build renamed or dropped is expected; the "
                         "point is to still capture the rest.")
    args = ap.parse_args()

    if args.all and args.table:
        print("FATAL: pass --all or --table, not both")
        return 2
    tables = list(ALL_TABLES) if args.all else (args.table or [])
    if not tables:
        print("FATAL: nothing to do — pass --table NAME (repeatable) or --all")
        return 2

    game_dir = args.game_dir.expanduser()
    if not game_dir.is_dir():
        print(f"FATAL: not a directory: {game_dir}")
        return 2

    out_dir = _FIXTURE_ROOT / f"vanilla{args.version}"
    out_dir.mkdir(parents=True, exist_ok=True)

    total_raw = 0
    failures: list[tuple[str, str]] = []
    for table in tables:
        print(f"{table}:")
        raw, err = _freeze(game_dir, table, out_dir, args.force)
        total_raw += raw
        if err:
            failures.append((table, err))
            print(f"  FAILED: {err}")
            if not args.keep_going:
                print("\nStopping. Use --keep-going to capture the rest and "
                      "collect the failures.")
                return 2

    ok = len(tables) - len(failures)
    print(f"\nfroze {ok}/{len(tables)} tables to vanilla{args.version}: "
          f"{total_raw:,} raw bytes")
    if failures:
        print(f"\n{len(failures)} table(s) did not load:")
        for table, err in failures:
            print(f"  {table:<32} {err}")
    print("\nCross-check the .pabgb sizes against the canary's report before "
          "committing, then run the layout tests against the new fixtures.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
