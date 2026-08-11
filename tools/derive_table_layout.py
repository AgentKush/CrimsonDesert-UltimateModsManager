"""Derive on-disk layouts for undecoded .pabgb tables, and prove them.

The blocker for the ~116 undecoded tables is not field ORDER -- that comes
from ``extract_field_order_win`` -- it is field WIDTH. The shipped schema
has no usable width for most of their fields (3 of them for
``actionpointinfo``, 110 of 205 for ``gimmickinfo``), and a walk with one
missing width ends short no matter how right the order is.

This derives the widths from the game itself, in two stages, and accepts
nothing that is not proven.

Stage 1 -- static, from the exe
    Each field is read either as a primitive with its width stated inline
    (``mov r8d, N`` before the vtable call) or through a typed sub-reader
    (``call <imm>``). The sub-readers are compositions, so they are solved
    recursively:

        fixed N    one sized read, no loop, no unaccounted subcall
        4 + n      a 4-byte length, an early-out when it is zero, then a
                   width taken from that slot -- the CString reader
        composite  a sequence of the above, plus already-solved subcalls

    A subcall that performs NO stream read consumes zero bytes. Getting
    that wrong is expensive: requiring every helper to have a model
    (hash-lookup helpers, allocators, loggers) solved 2 of 193 readers
    instead of 5.

    Any reader containing a BACKWARD BRANCH is refused. That is a
    count-prefixed list, and its element width is not derivable this way.

Stage 2 -- by constraint, from the table data
    A list reader is ``u32 count`` then ``count * E``. No game file states
    E: ``.debug$P`` is an array of code pointers rather than CodeView type
    records, and no shipped asset carries a schema. But the DATA
    constrains E -- it must make every record tile exactly. So E is
    searched, with the acceptance rule this project already used for
    storeinfo's ``ORDER_ELEM_SIZE``:

        Exactly one size satisfies every record, and it satisfies it for
        all of them. A size that "nearly" works fails outright -- so this
        is unique-or-nothing rather than best-fit.

    Two widths that both tile means the data cannot separate them, and the
    answer is "unknown". That case is real: ``dialogvoiceinfo`` accepts all
    128 candidates because every one of its lists is empty. Reporting it as
    ambiguous rather than taking the first is the difference between this
    being trustworthy and being a guess.

    Solving one reader can make another table single-unknown, so stage 2
    iterates to a fixed point.

THE GATE, for everything: exact tiling on 100% of records. Not "reached the
last field" -- a walk can run out of fields with bytes to spare and has
then understood nothing.

WHAT THIS DOES NOT ESTABLISH: semantics. Tiling proves the record
STRUCTURE and gives every named field its byte offset. It does not prove
that ``_limitDistance`` means a distance. Nothing here is writable until
its values are cross-checked against real records and gated to
``_verified_fields`` -- the community stat mapping was wrong on 18 of 24
entries, all flagged "verified", which is why that gate exists.

Needs ``capstone`` + ``pefile`` (analysis-only, not app dependencies) and a
game install. Read-only throughout::

    python -m pip install capstone pefile
    python tools/derive_table_layout.py --game-dir "E:/.../Crimson Desert"
"""
from __future__ import annotations

import argparse
import collections
import struct
import sys
from bisect import bisect_left
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "tools"))

#: Tables that already have a decoder/writer; nothing to derive for them.
DECODED = {
    "iteminfo", "characterinfo", "dropsetinfo", "equipslotinfo",
    "multichangeinfo", "skill", "storeinfo", "stringinfo", "fieldinfo",
    "stageinfo", "regioninfo", "vehicleinfo", "wantedinfo", "statusinfo",
    "statusgroupinfo", "buffinfo", "knowledgeinfo", "interactioninfo",
    "inventory",
}

#: Consumed by the ENTRY HEADER, not the record body -- ``_stringKey`` IS
#: the entry name. Walking them again double-counts, and it is not a small
#: effect: leaving them in drops RegionInfo's walker from 21 fields to 2.
HEADER_FIELDS = ("_stringKey", "_key")

#: Widest plausible list element for the constraint search.
MAX_ELEM = 128


def _cs():
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    return md


class Deriver:
    def __init__(self, game_dir: Path):
        from extract_field_order_win import find_field_strings, find_lea_xrefs, open_image
        self.game = game_dir
        self.md = _cs()
        self.img = open_image(game_dir / "bin64" / "CrimsonDesert.exe")
        strings = find_field_strings(self.img)
        xrefs = find_lea_xrefs(self.img, {s.va for s in strings})
        self.by_cls: dict[str, list[tuple[str, int]]] = {}
        for s in strings:
            for lea in xrefs.get(s.va, []):
                self.by_cls.setdefault(s.cls, []).append((s.fld, lea))
        self.model: dict[int, tuple[str, int]] = {}
        self.elem: dict[int, int] = {}
        self._memo: dict[int, tuple[str, int] | None] = {}

    # ── stage 1: static reader models ────────────────────────────────────

    def solve_reader(self, va: int, depth: int = 0):
        """('fixed'|'str'|'strplus', base) or None when not derivable."""
        from capstone import CS_OP_IMM, CS_OP_MEM, CS_OP_REG
        if va in self._memo:
            return self._memo[va]
        if depth > 4:
            return None
        self._memo[va] = None                       # cycle guard
        off = self.img.va_to_off(va)
        if off is None:
            return None
        ins = list(self.md.disasm(self.img.data[off:off + 600], va))
        if not ins:
            return None
        reads: list[int] = []
        subs: list[int] = []
        pend = None
        var_read = False
        for i in ins:
            o = i.operands
            if (i.mnemonic in ("mov", "lea") and len(o) == 2
                    and o[0].type == CS_OP_REG
                    and "r8" in i.op_str.split(",")[0]):
                if o[1].type == CS_OP_IMM:
                    pend = o[1].imm
                elif (i.mnemonic == "lea" and o[1].type == CS_OP_MEM
                      and o[1].mem.disp >= 0):
                    pend = o[1].mem.disp            # lea r8d,[rsi+4], rsi=0
                else:
                    pend, var_read = None, True     # width from a slot
            if i.mnemonic == "call":
                if len(o) == 1 and o[0].type == CS_OP_MEM:
                    if pend is not None:
                        reads.append(pend)
                    pend = None
                elif len(o) == 1 and o[0].type == CS_OP_IMM:
                    subs.append(o[0].imm)
            if (i.mnemonic.startswith("j") and len(o) == 1
                    and o[0].type == CS_OP_IMM and va <= o[0].imm < i.address):
                return None                         # loop: not derivable here
        if var_read and reads[:1] == [4]:
            self._memo[va] = ("str", 0)
            return self._memo[va]
        extra, kind = 0, "fixed"
        for s in subs:
            r = self.solve_reader(s, depth + 1)
            if r is None:
                return None
            if r[0] in ("str", "strplus"):
                kind = "strplus"
            extra += r[1]
        got = (("fixed", 0) if (not reads and extra == 0)
               else (kind, sum(reads) + extra))
        self._memo[va] = got
        return got

    # ── field order + per-field read shape ───────────────────────────────

    def field_reads(self, cls: str):
        """[(name, 'fixed'|'call'|'?', width_or_callee)] in hot-path order."""
        from capstone import CS_OP_IMM, CS_OP_REG
        from extract_field_order_win import SWEEP_PAD
        pairs = self.by_cls[cls]
        leas = sorted(a for _f, a in pairs)
        lo = leas[0] - SWEEP_PAD
        off = self.img.va_to_off(lo)
        span = (leas[-1] + SWEEP_PAD) - lo
        ins = list(self.md.disasm(self.img.data[off:off + span], lo))
        addrs = [i.address for i in ins]
        inbound = collections.defaultdict(list)
        targets: set[int] = set()
        for i in ins:
            o = i.operands
            if (i.mnemonic.startswith("j") and len(o) == 1
                    and o[0].type == CS_OP_IMM):
                targets.add(o[0].imm)
                inbound[o[0].imm].append(i.address)
            if i.mnemonic in ("ret", "jmp", "int3", "ud2"):
                targets.add(i.address + i.size)
        blocks = sorted(targets | {lo})

        def hot(lea: int):
            j = bisect_left(blocks, lea) - 1
            b = blocks[j] if j >= 0 else blocks[0]
            src = inbound.get(b)
            return (min(src), lea) if src else (lea, lea)

        out = []
        for fld, lea in sorted(pairs, key=lambda p: hot(p[1])):
            i = bisect_left(addrs, lea)
            if i >= len(addrs) or addrs[i] != lea:
                out.append((fld, "?", None))
                continue
            j, call = i - 1, None
            while j >= 0 and i - j < 24:
                if ins[j].mnemonic == "call":
                    call = ins[j]
                    break
                j -= 1
            if call is None:
                out.append((fld, "?", None))
                continue
            o = call.operands
            if len(o) == 1 and o[0].type == CS_OP_IMM:
                out.append((fld, "call", o[0].imm))
                continue
            k, start = j - 1, max(0, j - 24)
            while k >= 0 and j - k < 24:
                if ins[k].mnemonic == "call":
                    start = k + 1
                    break
                k -= 1
            w = None
            for x in ins[start:j]:
                oo = x.operands
                if (x.mnemonic == "mov" and len(oo) == 2
                        and oo[0].type == CS_OP_REG
                        and oo[1].type == CS_OP_IMM
                        and "r8" in x.op_str.split(",")[0]):
                    w = oo[1].imm
            out.append((fld, "fixed", w) if w is not None
                       else (fld, "?", None))
        return out

    # ── the gate ─────────────────────────────────────────────────────────

    def _consume(self, body, p, end, fields, elem):
        for name, kind, val in fields:
            if kind == "fixed":
                p += val
            elif kind == "call":
                m = self.model.get(val)
                if m is not None:
                    t, base = m
                    p += base
                    if t in ("str", "strplus"):
                        if p + 4 > end:
                            return None, name
                        n = struct.unpack_from("<I", body, p)[0]
                        p += 4
                        if n > 2_000_000 or p + n > end:
                            return None, name
                        p += n
                elif val in elem:
                    if p + 4 > end:
                        return None, name
                    cnt = struct.unpack_from("<I", body, p)[0]
                    p += 4
                    if cnt > 100_000 or p + cnt * elem[val] > end:
                        return None, name
                    p += cnt * elem[val]
                else:
                    return None, name
            else:
                return None, name
            if p > end:
                return None, name
        return p, None

    def tiles(self, body, ent, key_size, fields, elem, subset=None) -> bool:
        """Exact tiling on EVERY record, or False. No partial credit.

        ``subset`` restricts which records are checked, used only to PRUNE
        a search: a width that tiles all records must also tile any subset,
        so a subset failure is a sound rejection. Every survivor is then
        re-checked against the full table before it is believed.
        """
        rng = subset if subset is not None else range(len(ent))
        for i in rng:
            o = ent[i][1]
            end = ent[i + 1][1] if i + 1 < len(ent) else len(body)
            if o + key_size + 4 > end:
                return False
            nlen = struct.unpack_from("<I", body, o + key_size)[0]
            got, _bad = self._consume(
                body, o + key_size + 4 + nlen, end, fields, elem)
            if got != end:
                return False
        return True

    def search(self, body, ent, key_size, fields, unknown: list[int]):
        """Every element-width assignment that tiles the WHOLE table.

        One or two unknowns. Two is 128*128, so a spread of records prunes
        first and only survivors are verified in full -- sound, because a
        subset failure cannot be a false rejection.
        """
        n = len(ent)
        step = max(1, n // 24)
        probe = list(range(0, n, step))[:24] or [0]

        if len(unknown) == 1:
            c = unknown[0]
            cheap = [e for e in range(1, MAX_ELEM + 1)
                     if self.tiles(body, ent, key_size, fields,
                                   {**self.elem, c: e}, probe)]
            return [(e,) for e in cheap
                    if self.tiles(body, ent, key_size, fields,
                                  {**self.elem, c: e})]

        a, b = unknown
        cheap = []
        for ea in range(1, MAX_ELEM + 1):
            for eb in range(1, MAX_ELEM + 1):
                if self.tiles(body, ent, key_size, fields,
                              {**self.elem, a: ea, b: eb}, probe):
                    cheap.append((ea, eb))
        return [(ea, eb) for ea, eb in cheap
                if self.tiles(body, ent, key_size, fields,
                              {**self.elem, a: ea, b: eb})]


def load_table(game: Path, stem: str):
    from cdumm.engine.v2_to_format3 import _load_vanilla_table
    from cdumm.semantic.parser import parse_pabgh_index
    body = _load_vanilla_table(game, stem + ".pabgb")
    header = _load_vanilla_table(game, stem + ".pabgh")
    key_size, offsets = parse_pabgh_index(header, stem)
    return body, sorted(offsets.items(), key=lambda kv: kv[1]), key_size


def find_index(explicit: Path | None = None) -> Path | None:
    """The game index CDUMM's Game Data tab builds, newest first.

    Also accepts the timestamped ``.bak`` copies, because a machine that
    has re-indexed since a patch keeps the previous one and the table LIST
    barely changes between builds -- it is only used to enumerate
    candidates, never to read bytes.
    """
    import os
    if explicit:
        return explicit if explicit.exists() else None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    d = Path(local) / "cdumm"
    if not d.is_dir():
        return None
    cands = sorted(d.glob("game_index*.sqlite*"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def table_stems(index: Path) -> list[str]:
    import sqlite3
    con = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    return sorted({p.split("/")[-1][:-6] for (p,) in con.execute(
        "SELECT path FROM data_tables WHERE path LIKE '%.pabgb'")})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Derive + prove table layouts")
    ap.add_argument("--game-dir", required=True, type=Path)
    ap.add_argument("--tables", nargs="*",
                    help="table stems to try (default: every undecoded one "
                         "the game index knows)")
    ap.add_argument("--index", type=Path,
                    help="path to a cdumm game_index sqlite (default: the "
                         "newest under %%LOCALAPPDATA%%/cdumm)")
    args = ap.parse_args(argv)

    try:
        d = Deriver(args.game_dir)
    except ImportError as e:
        print(f"needs capstone + pefile: {e}")
        return 2

    if args.tables:
        stems = list(args.tables)
    else:
        index = find_index(args.index)
        if index is None:
            print("No game index found under %LOCALAPPDATA%/cdumm. Build one "
                  "from CDUMM's Game Data tab, pass --index, or name tables "
                  "with --tables.")
            return 2
        print(f"index: {index.name}")
        stems = [s for s in table_stems(index) if s.lower() not in DECODED]
    if not stems:
        print("No candidate tables.")
        return 2
    lower = {k.lower(): k for k in d.by_cls}
    stems = [s for s in stems if s.lower() in lower]
    print(f"candidate tables with a reflection class: {len(stems)}")

    fields = {s: d.field_reads(lower[s.lower()]) for s in stems}

    # stage 1
    callees = {v for f in fields.values() for _n, k, v in f if k == "call"}
    for c in callees:
        self_memo = d._memo
        self_memo.clear()
        got = d.solve_reader(c)
        if got is not None:
            d.model[c] = got
    print(f"sub-readers solved statically: {len(d.model)} of {len(callees)}")

    loaded = {}
    for s in stems:
        try:
            body, ent, ks = load_table(args.game_dir, s)
        except Exception as e:                       # noqa: BLE001
            # A table absent from this build, or one whose index this
            # parser cannot frame, is simply not a candidate.
            print(f"  skip {s}: {type(e).__name__}")
            continue
        if ent:
            loaded[s] = (body, ent, ks)
    print(f"tables loaded from the install: {len(loaded)}")

    # stage 2, to a fixed point
    proven: dict[str, int] = {}
    ambiguous: dict[str, list[int]] = {}   # keyed, so a table
    # re-tested on a later round is not reported twice
    for rnd in range(1, 9):
        added = 0
        for stem in sorted(loaded):
            if stem in proven:
                continue
            f = [x for x in fields[stem] if x[0] not in HEADER_FIELDS]
            if any(k == "?" for _n, k, _v in f):
                continue
            body, ent, ks = loaded[stem]
            unknown = sorted({v for _n, k, v in f if k == "call"
                              and v not in d.model and v not in d.elem})
            if not unknown:
                if d.tiles(body, ent, ks, f, d.elem):
                    proven[stem] = len(ent)
                continue
            if len(unknown) > 2:
                continue

            sols = d.search(body, ent, ks, f, unknown)
            if not sols:
                continue

            # A reader is determined when EVERY tiling solution agrees on
            # it. With two unknowns that is strictly more informative than
            # all-or-nothing: one reader can be pinned while the other is
            # left unconstrained (its lists are empty in this table, so
            # nothing here can speak to it). Extracting the pinned one is
            # safe; assuming the free one is not.
            pinned = {}
            for i, c in enumerate(unknown):
                vals = {s[i] for s in sols}
                if len(vals) == 1:
                    pinned[c] = next(iter(vals))
            note = ""
            if len(unknown) == 2:
                note = (", both of 2 unknowns" if len(pinned) == 2
                        else ", 1 of 2 unknowns (the other is unconstrained "
                             "here)")
            for c, e in pinned.items():
                if c not in d.elem:
                    d.elem[c] = e
                    added += 1
                    print(f"  round {rnd}: sub_{c:X} = 4 + count*{e}"
                          f"   (from {stem}, {len(ent)} records{note})")
            if len(pinned) == len(unknown):
                proven[stem] = len(ent)
            else:
                free = [c for c in unknown if c not in pinned]
                ambiguous[stem] = sorted({s[unknown.index(free[0])]
                                          for s in sols})
        if not added:
            break

    print()
    print(f"PROVEN -- exact tiling on 100% of records: {len(proven)} tables, "
          f"{sum(proven.values()):,} records")
    for s, n in sorted(proven.items()):
        print(f"   {s:<32} {n:>7} records")
    if ambiguous:
        print(f"\nAMBIGUOUS (more than one width tiles -> reported, NOT "
              f"guessed): {len(ambiguous)}")
        for s, hits in sorted(ambiguous.items())[:10]:
            print(f"   {s:<32} {len(hits)} widths tile, e.g. {hits[:6]}")
    print("\nReminder: tiling proves STRUCTURE, not semantics. Gate every "
          "new field to _verified_fields after a value spot-check before "
          "anything writes to it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
