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

#: zlib level 9: these are committed binaries, so size beats speed, and
#: the existing vanilla116 fixtures were written the same way.
_LEVEL = 9


def _load(game_dir: Path, name: str) -> bytes:
    from cdumm.engine.v2_to_format3 import _load_vanilla_table
    return _load_vanilla_table(game_dir, name)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Freeze a game table into tests/fixtures/vanilla<version>/")
    ap.add_argument("--game-dir", required=True, type=Path,
                    help="the Crimson Desert install root")
    ap.add_argument("--table", required=True,
                    help="table basename, e.g. storeinfo (no extension)")
    ap.add_argument("--version", required=True,
                    help="fixture version label, e.g. 117 -> vanilla117/")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing fixture")
    args = ap.parse_args()

    game_dir = args.game_dir.expanduser()
    if not game_dir.is_dir():
        print(f"FATAL: not a directory: {game_dir}")
        return 2

    out_dir = _FIXTURE_ROOT / f"vanilla{args.version}"
    out_dir.mkdir(parents=True, exist_ok=True)

    total_raw = 0
    for suffix in ("pabgb", "pabgh"):
        name = f"{args.table}.{suffix}"
        dest = out_dir / f"{name}.zlib"
        if dest.exists() and not args.force:
            print(f"REFUSING: {dest.relative_to(_REPO)} exists (use --force)")
            return 1
        try:
            raw = _load(game_dir, name)
        except Exception as exc:                        # noqa: BLE001
            # Naming the table and the loader beats a bare traceback:
            # the usual cause is a table that this build renamed or that
            # lives in a PAZ the vanilla index does not cover.
            print(f"FATAL: could not load {name} via _load_vanilla_table: "
                  f"{type(exc).__name__}: {exc}")
            return 2
        if not raw:
            print(f"FATAL: {name} loaded as zero bytes")
            return 2
        dest.write_bytes(zlib.compress(raw, _LEVEL))
        total_raw += len(raw)
        print(f"  {name:<28} {len(raw):>10,} bytes raw -> "
              f"{dest.stat().st_size:>9,} on disk  "
              f"({dest.relative_to(_REPO)})")

    print(f"\n{args.table} frozen to vanilla{args.version}: "
          f"{total_raw:,} raw bytes across both files.")
    print("Cross-check the .pabgb size against the canary's report before "
          "committing, then run the layout tests against the new fixture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
