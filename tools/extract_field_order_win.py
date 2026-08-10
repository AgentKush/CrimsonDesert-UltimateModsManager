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
  * `SkillInfo._isNoAlert` is named, and sits in a run of seven
    consecutive `u8` flags where `_vendor/skillinfo_parser` reads only
    six. Adding it as the obvious missing byte takes skill parsing from
    1,424/2,013 entries (70.7%) to 449/2,013 (22.3%). It is not in the
    record body. (GitHub #355.)

So treat the output as an ORDER over the fields that are serialised, not
as a list of what to read. A field this tool names and a walker does not
consume is a hypothesis, and the only thing that settles it is decoding
real records.

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
    pe.close()
    return Image(data=mm, base=base, sections=secs)


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
        if m in _UNCOND_END:
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
            func = sweep_function(
                img, leas[0] - SWEEP_PAD, leas[-1] + SWEEP_PAD)

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
