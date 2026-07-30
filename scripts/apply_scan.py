#!/usr/bin/env python3
"""Apply-level scanner: which Format-3 intents produce no bytes?

``coverage_scan.py`` asks whether ``validate_intents`` *claims* it can
apply an intent. That is a different question from whether bytes come
out, and the difference is where this project's real bugs have lived:

  * equipslotinfo ``etl_hashes`` validated clean and wrote nothing
  * iteminfo ``match`` selected zero records
  * skill ``use_resource_stat_list[N].d`` applied 0 bytes
  * characterinfo ``call_mercenary_spawn_duration`` was dropped as an
    unsupported name while its sibling applied, so "No CD Mount" mods
    half-worked

Every one of those passed the validate-level scan. On a 52-mod corpus
that scan printed "No uncovered Format-3 intents found" while the apply
path was silently dropping 1466 intents.

So this scanner drives the REAL apply path -- ``expand_format3_into_
aggregated``, including every whole-table writer -- against vanilla
bytes, and reports what the engine itself logged as skipped, refused or
unresolved. The engine already says exactly which field on which record
it could not place; nothing here re-derives that judgement.

Vanilla bytes come from the committed ``tests/fixtures/vanilla*``
snapshots by default, so this runs in CI with no game install. Pass
``--game-dir`` to scan against a real install instead, which covers
tables that have no fixture.

Usage::

    python scripts/apply_scan.py <dir-or-file> [more paths ...]
    python scripts/apply_scan.py --game-dir "D:/.../Crimson Desert" mods/

Exit code is the number of distinct drop classes (0 = every intent the
validator accepted also produced bytes), matching ``coverage_scan.py``
so CI can gate on it the same way.

IMPORTANT: a target whose vanilla bytes cannot be sourced is reported as
NOT SCANNED, never as clean. A scanner that silently skips what it can't
read is exactly the failure mode it exists to catch.
"""
from __future__ import annotations

import collections
import logging
import re
import sys
from pathlib import Path

# Allow running straight from a checkout without an editable install.
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cdumm.engine.format3_apply import (
    expand_format3_into_aggregated,
)
from cdumm.engine.format3_handler import (
    parse_format3_mod_targets,
)

# The engine's own vocabulary for "this intent did not produce bytes".
# Matching its log rather than re-implementing the judgement is the whole
# point: the writers know why they refused, and they already say so.
_DROP_MARKERS = (
    "skipped", "refus", "unresolved", "could not locate",
    "not supported", "produced 0", "left unwritten",
)

# Collapse per-record noise (keys, offsets, counts) so one field's
# refusal across 1400 records reads as one class, not 1400.
_DIGITS = re.compile(r"\b\d{3,}\b")


class _DropCapture(logging.Handler):
    """Collect the engine's skip/refuse warnings during one apply."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.msgs: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 -- a bad log record must not
            return        # abort a corpus scan
        if any(k in msg.lower() for k in _DROP_MARKERS):
            self.msgs.append(msg)

    def classes(self) -> collections.Counter:
        out: collections.Counter = collections.Counter()
        for m in self.msgs:
            out[_DIGITS.sub("N", m)[:160]] += 1
        return out


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, *_args):
        return _Cur(self._rows)


class _Db:
    """Minimal stand-in for the mod database the apply path reads."""

    def __init__(self, rows):
        self.connection = _Conn(rows)


def make_fixture_extractor():
    """vanilla_extractor backed by the committed test fixtures.

    Prefers the newest snapshot that has the table, so a scan reflects
    the layout users are most likely running.
    """
    from tests.fixture_loaders import (
        has_vanilla113,
        has_vanilla115,
        load_vanilla113,
        load_vanilla115,
    )

    def _extract(target: str):
        stem = Path(str(target)).name
        base = stem.rsplit(".", 1)[0]
        for has, load in ((has_vanilla115, load_vanilla115),
                          (has_vanilla113, load_vanilla113)):
            if has(f"{base}.pabgb"):
                head = (load(f"{base}.pabgh")
                        if has(f"{base}.pabgh") else b"")
                return load(f"{base}.pabgb"), head
        return None

    return _extract


def make_game_extractor(game_dir: Path):
    """vanilla_extractor reading a real install, read-only."""
    from cdumm.archive import paz_crypto
    from cdumm.engine.json_patch_handler import _find_pamt_entry

    cache: dict[str, bytes | None] = {}

    def _grab(logical: str):
        if logical in cache:
            return cache[logical]
        entry = _find_pamt_entry(logical, game_dir)
        raw = None
        if entry is not None:
            try:
                with open(entry.paz_file, "rb") as fh:
                    fh.seek(entry.offset)
                    raw = fh.read(entry.comp_size)
                if entry.encrypted:
                    raw = paz_crypto.decrypt(raw, Path(logical).name)
                if entry.comp_size != entry.orig_size and entry.orig_size:
                    raw = paz_crypto.lz4_decompress(raw, entry.orig_size)
            except Exception:  # noqa: BLE001 -- an unreadable table is a
                raw = None    # "not scanned", not a crash
        cache[logical] = raw
        return raw

    def _extract(target: str):
        stem = Path(str(target)).name
        base = stem.rsplit(".", 1)[0]
        body = _grab(f"gamedata/{base}.pabgb")
        if body is None:
            return None
        return body, (_grab(f"gamedata/{base}.pabgh") or b"")

    return _extract


def iter_mod_files(paths: list[str]):
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            yield p
        elif p.is_dir():
            yield from sorted(p.rglob("*.json"))


def find_format3_mods(paths: list[str]) -> list[Path]:
    """Same detector coverage_scan.py uses: does it parse as Format 3?

    Sniffing for a ``format: 3`` key instead finds a fraction of them --
    DMM ships several wrapper shapes that carry no such key.
    """
    mods: list[Path] = []
    for f in iter_mod_files(paths):
        try:
            pairs = parse_format3_mod_targets(f)
        except Exception:  # noqa: BLE001, S112 -- see below
            # Blind by design, same as coverage_scan.scan_file: this is
            # pointed at whole directories of mixed content, so
            # "unparseable" is the common case, not an error. Narrowing it
            # would mean enumerating every way a non-Format-3 file can
            # fail, and one miss aborts a corpus scan.
            continue
        if pairs and any(intents for _t, intents in pairs):
            mods.append(f)
    return mods


def scan(paths: list[str], extractor) -> tuple[
        collections.Counter, dict[str, set[str]], set[str], int]:
    """Drive the apply path over every Format-3 mod under ``paths``.

    Returns ``(drop_classes, mods_per_class, unscanned_targets, n_mods)``.
    """
    mods = find_format3_mods(paths)
    drops: collections.Counter = collections.Counter()
    who: dict[str, set[str]] = collections.defaultdict(set)
    unscanned: set[str] = set()

    for i, path in enumerate(mods):
        # A target we cannot source vanilla bytes for is NOT evidence of
        # coverage; record it so the summary can say so out loud.
        try:
            for target, _intents in parse_format3_mod_targets(path):
                if extractor(target) is None:
                    unscanned.add(Path(str(target)).name)
        except Exception as e:  # noqa: BLE001 -- never abort the corpus
            # find_format3_mods already parsed this file, so a failure
            # here is the extractor, not the mod. Log it rather than
            # swallow it: a scanner that goes quiet is the bug.
            logging.getLogger(__name__).warning(
                "could not determine vanilla availability for %s: %s",
                path.name, e)

        cap = _DropCapture()
        logging.getLogger("cdumm").addHandler(cap)
        try:
            expand_format3_into_aggregated(
                {}, {}, _Db([(1000 + i, path.stem[:40], str(path), 10)]),
                vanilla_extractor=extractor)
        except Exception as e:  # noqa: BLE001 -- one bad mod must not
            drops[f"APPLY CRASHED: {type(e).__name__}"] += 1
            who[f"APPLY CRASHED: {type(e).__name__}"].add(path.name)
        finally:
            logging.getLogger("cdumm").removeHandler(cap)

        for cls, n in cap.classes().items():
            drops[cls] += n
            who[cls].add(path.name)

    return drops, who, unscanned, len(mods)


def main(argv: list[str]) -> int:
    args = argv[1:]
    game_dir: Path | None = None
    if args and args[0] == "--game-dir":
        if len(args) < 2:
            print(__doc__)
            return 2
        game_dir = Path(args[1])
        args = args[2:]
    if not args:
        print(__doc__)
        return 2

    extractor = (make_game_extractor(game_dir) if game_dir
                 else make_fixture_extractor())
    source = f"game dir {game_dir}" if game_dir else "committed fixtures"

    drops, who, unscanned, n_mods = scan(args, extractor)
    print(f"Scanned {n_mods} Format-3 mod(s) against {source}.")

    if unscanned:
        print()
        print("NOT SCANNED -- no vanilla bytes available for these targets:")
        for t in sorted(unscanned):
            print(f"  {t}")
        print("  (these are unmeasured, not proven clean)")

    if not drops:
        print("\nNo intents dropped at apply time across the scanned mods.")
        return 0

    print()
    print("Dropped at apply time (the engine's own reason):")
    print("-" * 70)
    for cls, n in drops.most_common():
        ms = ", ".join(sorted(who[cls])[:3])
        print(f"  x{n}  {cls}")
        print(f"      in: {ms}")
    return len(drops)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
