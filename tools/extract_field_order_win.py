"""Recover per-table field READ ORDER from the WINDOWS CrimsonDesert.exe.

`extract_field_order.py` reads the reflection error strings in string-table
order. That is read order on the unstripped macOS binary and it is NOT read
order on the Windows exe, which is why that script warns and gives up on a
`MZ` file. This module gets the order from the Windows exe instead, by
looking at where the code REFERENCES those strings rather than where the
linker happened to put them.

Read-only throughout: the exe is opened for reading and mmapped. Never
copied, patched or executed.

The method
----------
Each field read is emitted as a guarded call, and the failure branch names
the field::

        lea  rdx, [rsi + 0x70]                  ; destination in the struct
        mov  r8d, 8                             ; stream bytes (primitives)
        call qword ptr [rax + 8]                ; the sized reader
        test al, al
        jne  ok
        lea  rax, [rip + "CharacterInfo의 _gender를 ..."]   <-- the xref
        jmp  fail
    ok: ...

So every field has exactly one `lea` that loads its message, and those
`lea`s appear in field order -- as long as they are laid out where they
were emitted.

Why sorting by the lea address is wrong
---------------------------------------
They are not always laid out there. A conditionally read field gets its
error block OUTLINED: the compiler moves the cold path to the end of the
function and leaves a forward branch behind. Sorting by the lea's own
address therefore drops every such field to the end of the order.

That failure is measurable. On ItemInfo -- the largest table with a verified
order, 101 fields shared with the shipped schema -- naive lea-address
ordering puts `_itemUseInfoList`, `_cooltime` and `_maxChargedUseableCount`
at the end of the table instead of at indices 9, 67 and 70, and disagrees
with the verified order at index 9.

The fix
-------
Order by the HOT-PATH branch that reaches the error block, not by where the
block landed. The branch sits immediately after the field's read, in true
sequence, whether or not the block was outlined. Fields whose error block is
fallen into rather than branched to keep their own address, which is already
in sequence.

With that change ItemInfo's 101 shared fields match the verified order
exactly, and RegionInfo's `_key` moves from last to index 0.

What this does and does not establish
-------------------------------------
It establishes order for the fields that HAVE an error string. That is not
every field: 9 of CharacterInfo's 164 schema fields and 12 of ItemInfo's 113
are never named this way, so the output is not a drop-in `_ordered_fields`
-- something still has to place the omitted ones. Verification therefore
runs through `verify_order_source_relative`, which compares on shared names
and reports the unplaced ones rather than pretending they do not exist.

NAMED IS NOT SERIALISED. This is the trap, and it is worth stating on its
own because the inference is so tempting: an error string proves the field
exists ON THE TYPE and is read by the deserializer. It says NOTHING about
whether that field occupies bytes in the record body.

Two measured counterexamples:

  * `_stringKey` / `_key` are named here, but the ENTRY HEADER consumes
    them -- `_stringKey` IS the entry name. Splicing this order into a
    verified one without pinning those two takes RegionInfo's walker from
    a median of 21 fields to 2.
So treat the output as an ORDER over the fields that are serialised, not
as a list of what to read. A field this tool names and a walker does not
consume is a hypothesis, and the only thing that settles it is decoding
real records.

DO NOT OVER-APPLY THAT. A named field the walker skips is far more often a
real missing field than a memory-only one, and this caveat has already
been used to wave away a genuine bug:

    `SkillInfo._isNoAlert` (index 25) is named here and was not read by
    `_vendor/skillinfo_parser`, which took a run of seven consecutive u8
    flags for six. It IS serialised, and inserting it is the fix for
    GitHub #355 -- 2013/2013 records on CD 1.16, up from 1424.

    An earlier revision of this file cited it as a counterexample on the
    strength of a measurement showing 449/2013. That measurement was
    wrong: it patched the field READER without widening the matching
    flag-run skip in the boundary probe, so the brute-force search then
    landed on the wrong offsets. The field was right; the test of it was
    incomplete.

The lesson is narrower than "distrust the tool". It is: decide with an
AGGREGATE decode over a whole table, on both an old and a new build.
Per-record signals cannot referee here -- the boundary search makes a
wrong layout land exactly on the record end, and the record then
round-trips byte-exact too, because it writes back whatever it read.

Requires `capstone` and `pefile`, which are analysis-only and deliberately
not runtime dependencies of the app::

    python -m pip install capstone pefile
    python tools/extract_field_order_win.py "<game>/bin64/CrimsonDesert.exe"
"""
from __future__ import annotations

import mmap
import re
import struct
import sys
from bisect import bisect_right
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cdumm.engine.schema_verify import (
    tables_with_verified_order,
    verify_order_source_relative,
)

# <Class>의 ... _<field>를   —  의 = ec 9d 98, 를 = eb a5 bc
_PAIR = re.compile(
    rb"([A-Za-z][A-Za-z0-9]{1,60})\xec\x9d\x98"
    rb".{0,48}?"
    rb"(_[A-Za-z][A-Za-z0-9]{0,60})\xeb\xa5\xbc",
    re.DOTALL)

#: rip-relative lea: REX.W(48/4C) + 8D + modrm(mod=00, rm=101) + disp32.
#: That encoding has no shorter alias, so the only false positives are the
#: bytes appearing inside another instruction's operand -- filtered out
#: later by requiring the hit to decode as a `lea` in the function sweep.
_LEA = re.compile(
    rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....",
    re.DOTALL)

#: How far either side of a class's xref cluster to disassemble. The
#: deserializer is one densely packed function (CharacterInfo's 190 xrefs
#: span 9.3 KB with no gap above 512 bytes), so a small pad reaches the
#: whole thing including outlined blocks past the last xref.
SWEEP_PAD = 0x400

_UNCOND_END = frozenset({"ret", "jmp", "int3", "ud2"})


@dataclass(frozen=True)
class Section:
    name: str
    va: int
    vsize: int
    raw: int
    rsize: int


@dataclass
class Image:
    """A mapped PE, addressed by virtual address."""

    data: mmap.mmap
    base: int
    sections: tuple[Section, ...]
    #: (va, size) of the .pdata exception directory, (0, 0) if absent.
    pdata: tuple[int, int] = (0, 0)
    #: cache for _pdata_map, which is expensive and asked for per class.
    _pd: object = None

    def va_to_off(self, va: int) -> int | None:
        for s in self.sections:
            if s.va <= va < s.va + max(s.vsize, s.rsize):
                d = va - s.va
                if d < s.rsize:
                    return s.raw + d
        return None

    def off_to_va(self, off: int) -> int | None:
        for s in self.sections:
            if s.raw <= off < s.raw + s.rsize:
                return s.va + (off - s.raw)
        return None

    def raw_sections(self) -> tuple[Section, ...]:
        # Walk every section with raw bytes. Section names change between
        # builds -- one shipped no `.xpdata` -- so nothing is hardcoded.
        return tuple(s for s in self.sections if s.rsize > 0)


def open_image(path: Path) -> Image:
    import pefile
    fh = open(path, "rb")                       # noqa: SIM115 (lives with mm)
    mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    pe = pefile.PE(str(path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    secs = tuple(
        Section(name=s.Name.rstrip(b"\x00").decode("ascii", "replace"),
                va=base + s.VirtualAddress,
                vsize=s.Misc_VirtualSize,
                raw=s.PointerToRawData,
                rsize=s.SizeOfRawData)
        for s in pe.sections)
    d = pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]        # ENTRY_EXCEPTION
    pdata = (base + d.VirtualAddress, d.Size) if d.VirtualAddress else (0, 0)
    pe.close()
    return Image(data=mm, base=base, sections=secs, pdata=pdata)


@dataclass(frozen=True)
class FieldString:
    cls: str
    fld: str
    va: int


def find_field_strings(img: Image) -> list[FieldString]:
    out = []
    for m in _PAIR.finditer(img.data):
        va = img.off_to_va(m.start())
        if va is None:
            continue
        out.append(FieldString(cls=m.group(1).decode("ascii", "replace"),
                               fld=m.group(2).decode("ascii", "replace"),
                               va=va))
    return out


def find_lea_xrefs(img: Image, targets: set[int]) -> dict[int, list[int]]:
    """{string_va: [address of each rip-relative lea that loads it]}."""
    hits: dict[int, list[int]] = {}
    for sec in img.raw_sections():
        blob = img.data[sec.raw:sec.raw + sec.rsize]
        for m in _LEA.finditer(blob):
            p = m.start()
            va = sec.va + p
            tgt = va + 7 + struct.unpack_from("<i", blob, p + 3)[0]
            if tgt in targets:
                hits.setdefault(tgt, []).append(va)
    return hits


# ── function sweep ───────────────────────────────────────────────────────

@dataclass
class Func:
    """A linear sweep of one function, indexed for branch queries."""

    start: int
    end: int
    addrs: list[int] = field(default_factory=list)
    block_starts: list[int] = field(default_factory=list)
    #: basic-block start -> addresses that branch to it
    inbound: dict[int, list[int]] = field(default_factory=dict)

    def block_start_of(self, va: int) -> int:
        i = bisect_right(self.block_starts, va) - 1
        return self.block_starts[i] if i >= 0 else self.start

    def hot_key(self, lea_va: int) -> tuple[int, int]:
        """Ordering key for the field whose error-lea is at ``lea_va``.

        The earliest branch into the lea's basic block, so an outlined
        block orders by where it is branched FROM. Falls back to the lea's
        own address when nothing branches in -- the fall-through case,
        already in sequence. The lea address is the tiebreak so the result
        is a total order.
        """
        srcs = self.inbound.get(self.block_start_of(lea_va))
        return (min(srcs), lea_va) if srcs else (lea_va, lea_va)


def _pdata_map(img: Image):
    """``(starts, by_begin, root, ext)`` from the exception directory.

    .pdata does not enumerate functions, it enumerates UNWIND ranges. A
    function with outlined blocks has a primary RUNTIME_FUNCTION plus
    continuations flagged UNW_FLAG_CHAININFO (0x4), each carrying a
    trailing RUNTIME_FUNCTION pointing at its parent. Anchoring on the
    nearest BeginAddress therefore bounds a FRAGMENT, not a function;
    following the chain to its root and merging brings every fragment of
    one function under a single extent.

    UNWIND_INFO: byte0 = Version(3) | Flags(5), Flags & 4 == CHAININFO;
    byte2 = CountOfCodes; the chained RUNTIME_FUNCTION follows the
    unwind-code array, padded to an even count.

    Lifted from ``tools/derive_table_layout.py``, which needed exactly
    this and worked out the chain-following. Kept here rather than
    imported the other way because that module already imports from this
    one.
    """
    if img._pd is not None:
        return img._pd
    va, size = img.pdata
    rf = []
    off = img.va_to_off(va) if va else None
    if off is not None:
        blob = img.data[off:off + size]
        for q in range(0, len(blob) - 11, 12):
            b, e, u = struct.unpack_from("<III", blob, q)
            if b and e > b:
                rf.append((img.base + b, img.base + e,
                           img.base + u if u else 0))
    rf.sort()
    by_begin = {b: (b, e, u) for b, e, u in rf}

    def parent(u):
        if not u:
            return None
        o = img.va_to_off(u)
        if o is None or o + 4 > len(img.data):
            return None
        if not ((img.data[o] >> 3) & 0x4):
            return None                       # not a chained fragment
        n = img.data[o + 2]                   # CountOfCodes
        c = o + 4 + 2 * ((n + 1) & ~1)
        if c + 12 > len(img.data):
            return None
        pb, _pe, _pu = struct.unpack_from("<III", img.data, c)
        return img.base + pb

    root: dict[int, int] = {}

    def resolve(b, seen=frozenset()):
        if b in root:
            return root[b]
        if b in seen:
            return b                          # cycle guard
        ent = by_begin.get(b)
        pa = parent(ent[2]) if ent else None
        r = (resolve(pa, seen | {b})
             if pa is not None and pa in by_begin else b)
        root[b] = r
        return r

    for b, _e, _u in rf:
        resolve(b)
    ext: dict[int, list[int]] = {}
    for b, e, _u in rf:
        cur = ext.setdefault(root[b], [b, e])
        cur[0] = min(cur[0], b)
        cur[1] = max(cur[1], e)
    img._pd = ([b for b, _e, _u in rf], by_begin, root, ext)
    return img._pd


def function_extent(img: Image, va: int) -> tuple[int, int] | None:
    """``(lo, hi)`` of the whole function containing ``va``, or None."""
    starts, by_begin, root, ext = _pdata_map(img)
    i = bisect_right(starts, va) - 1
    if i < 0:
        return None
    b, e, _u = by_begin[starts[i]]
    if not (b <= va < e):
        return None
    lo, hi = ext[root[starts[i]]]
    return (lo, hi)


def sweep_for_leas(img: Image, leas: list[int]) -> Func:
    """Sweep the real function(s) containing ``leas``, not a padded window.

    ``capstone.Cs.disasm`` is a generator that STOPS at the first byte
    sequence it cannot decode. A sweep starting at ``leas[0] - SWEEP_PAD``
    begins mid-instruction, desyncs, hits an invalid opcode and truncates,
    often before the first field lea. The block map is then empty, every
    lea falls back to its own address, and the order silently degrades to
    the naive lea-address order the hot-path rule exists to avoid. That is
    not hypothetical: on this build ContentsPhaseInfo, StageInfo and
    FactionOperationGroupInfo decoded 2, 4 and 1 instructions respectively
    across their whole span.

    Starting from the .pdata function start makes every instruction
    boundary genuine. Falls back to the padded window only when a lea sits
    outside .pdata, because a missing field is worse than an imprecise one.
    """
    spans, orphans = set(), []
    for a in leas:
        x = function_extent(img, a)
        if x is None:
            orphans.append(a)
        else:
            spans.add(x)
    if not spans and not orphans:
        orphans = list(leas)
    parts = sorted(spans)
    for a in orphans:
        parts.append((a - SWEEP_PAD, a + SWEEP_PAD))
    merged = Func(start=min(p[0] for p in parts),
                  end=max(p[1] for p in parts))
    seen: set[int] = set()
    for lo, hi in sorted(parts):
        off = img.va_to_off(lo)
        if off is None:
            continue
        f = sweep_bytes(img.data[off:off + (hi - lo)], lo)
        for a in f.addrs:
            if a not in seen:
                seen.add(a)
                merged.addrs.append(a)
        merged.block_starts += f.block_starts
        for t, srcs in f.inbound.items():
            merged.inbound.setdefault(t, []).extend(srcs)
    merged.addrs.sort()
    merged.block_starts = sorted(set(merged.block_starts))
    return merged


def sweep_function(img: Image, lo: int, hi: int) -> Func:
    """Linear-sweep ``[lo, hi)`` and index its branches.

    A linear sweep rather than a recursive traversal: this is one
    compiler-emitted deserializer, so it is contiguous code with no data
    islands, and every instruction is wanted in address order.
    """
    off = img.va_to_off(lo)
    if off is None:
        raise ValueError(f"VA {lo:#x} is not in a mapped section")
    return sweep_bytes(img.data[off:off + (hi - lo)], lo)


def sweep_bytes(blob: bytes, lo: int) -> Func:
    """``sweep_function`` on a raw blob — the unit-testable half."""
    from capstone import CS_ARCH_X86, CS_MODE_64, CS_OP_IMM, Cs
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True

    hi = lo + len(blob)
    f = Func(start=lo, end=hi)
    targets: set[int] = set()
    for ins in md.disasm(blob, lo):
        f.addrs.append(ins.address)
        m = ins.mnemonic
        if m.startswith("j"):
            ops = ins.operands
            if len(ops) == 1 and ops[0].type == CS_OP_IMM:
                t = ops[0].imm
                if lo <= t < hi:
                    targets.add(t)
                    f.inbound.setdefault(t, []).append(ins.address)
        # A conditional branch ends a block too, so the instruction after
        # it starts one. Without this leader an inline (non-outlined)
        # error block is folded into whatever block precedes the field's
        # own "jne ok", and hot_key then orders it by a branch that has
        # nothing to do with the field. That is what swapped _itemDesc
        # and _itemDesc2 on ItemInfo: _itemDesc's lea sits in the
        # fall-through at 0x14147CE47, which was attributed to the block
        # at 0x14147CE2D and keyed on an inbound edge at 0x14147CE5D,
        # while _itemDesc2's outlined block keyed on 0x14147CE45 and
        # sorted ahead of it.
        if m in _UNCOND_END or m.startswith("j"):
            nxt = ins.address + ins.size
            if lo <= nxt < hi:
                targets.add(nxt)
    f.block_starts = sorted(targets | {lo})
    return f


# ── the extraction ───────────────────────────────────────────────────────

def extract_orders(path: Path, naive: bool = False
                   ) -> tuple[dict[str, list[str]], dict[str, int]]:
    """{ClassName: [field, ...]} in read order, plus a few stats.

    ``naive=True`` reproduces the broken lea-address ordering, which is
    what the tests compare against so the fix cannot silently regress.
    """
    img = open_image(path)
    strings = find_field_strings(img)
    xrefs = find_lea_xrefs(img, {s.va for s in strings})

    by_cls: dict[str, list[tuple[str, int]]] = {}
    for s in strings:
        for lea in xrefs.get(s.va, []):
            by_cls.setdefault(s.cls, []).append((s.fld, lea))

    orders: dict[str, list[str]] = {}
    for cls, pairs in by_cls.items():
        leas = sorted(a for _f, a in pairs)
        if naive:
            def key(pair):
                return (pair[1], pair[1])
        else:
            func = sweep_for_leas(img, leas)

            def key(pair, _f=func):
                return _f.hot_key(pair[1])

        seen: set[str] = set()
        out: list[str] = []
        for fld, _a in sorted(pairs, key=key):
            if fld not in seen:                   # first occurrence wins
                seen.add(fld)
                out.append(fld)
        orders[cls] = out

    stats = {"strings": len(strings),
             "classes": len(by_cls),
             "xrefs": sum(len(v) for v in xrefs.values()),
             "unreferenced": len(strings) - len(xrefs)}
    return orders, stats


def main(argv: list[str]) -> int:
    # The field names are ASCII but the error strings around them are
    # Korean, so a cp1252 console would raise mid-report. Best effort:
    # a console that cannot be reconfigured still prints the ASCII.
    with suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 2:
        print("usage: python tools/extract_field_order_win.py <exe-path>")
        return 2
    path = Path(argv[1])
    with path.open("rb") as fh:
        data_head = fh.read(2)
    if data_head != b"MZ":
        print("!! Not a Windows PE. For the macOS binary the strings are "
              "already in read order — use tools/extract_field_order.py.")
        return 2

    print(f"binary: {path}  ({path.stat().st_size:,} bytes)")
    orders, stats = extract_orders(path)
    print(f"field strings {stats['strings']:,} across "
          f"{stats['classes']:,} classes; {stats['xrefs']:,} lea xrefs, "
          f"{stats['unreferenced']:,} strings never referenced")

    results = verify_order_source_relative(orders)
    known = tables_with_verified_order()
    print(f"\nverified tables: {len(known)}  covered: {len(results)}")
    ok = 0
    for r in results:
        print("  " + r.summary())
        ok += r.matches
    print(f"\nagree on shared names: {ok}/{len(results)}")

    for r in results:
        if r.matches:
            continue
        i = r.first_divergence
        print(f"\n{r.table} diverges at shared index {i}:")
        print(f"  verified  {r.shared[i:i + 4]}")
        print(f"  extracted {r.candidate_sequence[i:i + 4]}")

    unplaced = {r.table: r.verified_only for r in results if r.verified_only}
    if unplaced:
        print("\nFields the binary never names — this order cannot place "
              "them, so it is NOT a drop-in _ordered_fields:")
        for t, fs in sorted(unplaced.items()):
            print(f"  {t}: {len(fs)}  {fs[:6]}{' ...' if len(fs) > 6 else ''}")

    if ok != len(results):
        print("\nNOT fully verified — see the divergences above.")
        return 1
    print("\nEvery covered table agrees on the fields it shares. Treat the "
          "other classes as corroboration, and gate each new table to "
          "_verified_fields after a value spot-check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
