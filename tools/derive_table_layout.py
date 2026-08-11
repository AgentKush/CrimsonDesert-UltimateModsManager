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

    A stream read is a SIZED vtable call -- a width in ``r8d``, then
    ``call qword ptr [rax + 8]``. The width is the load-bearing half; a
    bare vtable call is an ordinary virtual call on some other object.

    A reader whose loop is on a stream-reading path is refused: that is a
    count-prefixed list, and stage 2 derives its element width. A loop in
    a helper that reads nothing consumes nothing and does not disqualify
    its caller.

    Readers stage 1 still cannot derive can be supplied by hand through
    ``HAND_VERIFIED``, kept separate and labelled so it is always visible
    which models are automatic and which are asserted. Those are held to
    the same tiling gate.

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

#: An escape hatch for readers stage 1 genuinely cannot derive: a model
#: read off the disassembly by hand, kept SEPARATE from the automatic ones
#: so it is always visible which is which. An unmarked hand constant is how
#: a schema stops being trustworthy.
#:
#: Currently EMPTY, and that is the part worth recording. It held
#: sub_141071DE0 = 13 + n, which I added because stage 1 refused it.
#: Bounding the disassembly to the function's own end (see ``body``) made
#: stage 1 derive that reader by itself, along with 34 others -- so the hand
#: model was never filling a gap in the method, it was covering for a bug in
#: my scan.
#:
#: Kept as a mechanism, kept empty, and kept with this note: the first thing
#: to suspect about a reader stage 1 cannot derive is stage 1.
#:
#: Any entry added here is still held to the tiling gate, and addresses are
#: BUILD-SPECIFIC -- on another build a stale entry simply fails to match a
#: callee, which is the safe direction.
HAND_VERIFIED: dict[int, tuple[str, int]] = {}


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
        self._reads: dict[int, bool] = {}

    # ── stage 1: static reader models ────────────────────────────────────

    def body(self, va: int, window: int = 600) -> list:
        """Instructions of the function at ``va``, STOPPING at its end.

        This is not a nicety. Disassembling a fixed window runs off the end
        of a short function into whatever follows it, and then attributes
        the neighbour's loops and calls to this reader. That is how
        sub_1412656D0 -- literally ``read(4)`` then a tail-call thunk --
        came out as "contains a loop, refused".

        Worse than the missed win: bytes from an unrelated function could
        contribute a sized read and produce a WRONG width, which the tiling
        gate would then have to catch. Better not to generate it.

        The end is the first ``ret`` that no forward branch jumps past. A
        ``ret`` before the furthest branch target is just one arm of the
        function, not its end.
        """
        from capstone import CS_OP_IMM
        off = self.img.va_to_off(va)
        if off is None:
            return []
        out = []
        furthest = va
        for i in self.md.disasm(self.img.data[off:off + window], va):
            out.append(i)
            o = i.operands
            if (i.mnemonic.startswith("j") and len(o) == 1
                    and o[0].type == CS_OP_IMM and o[0].imm > furthest):
                furthest = o[0].imm
            if i.mnemonic in ("ret", "int3") and i.address >= furthest:
                break
        return out

    def reads_stream(self, va: int, depth: int = 0,
                     seen: set[int] | None = None) -> bool:
        """Does this function touch the byte stream at all, transitively?

        The distinction this draws is the one that matters most in stage 1.
        A function that performs no stream read consumes ZERO bytes no
        matter what else it does -- including looping. Allocators, string
        builders and hash-lookup helpers all loop, and treating a loop as
        automatically undecidable let them refuse their callers.

        sub_141071DE0 is the case that exposed it: it reads 1 byte, 8
        bytes, then the CString reader, so it is plainly 13 + n. It was
        refused because a post-read helper it calls (sub_1410741B0, which
        just stores a pointer) contains a loop.

        A stream read is a SIZED vtable call: a width put in ``r8d``, then
        ``call qword ptr [rax + 8]``. The width is the load-bearing half.
        A bare ``call qword ptr [rax + 8]`` with no width is an ordinary
        virtual call on some other object, and counting it is what kept
        sub_141071DE0 refused -- its helper sub_1410741B0 makes exactly
        that call on ``r13 + 0x20``, a container rather than the stream.
        """
        from capstone import CS_OP_IMM, CS_OP_MEM, CS_OP_REG
        if va in self._reads:
            return self._reads[va]
        if seen is None:
            seen = set()
        if depth > 4 or va in seen:
            return False
        seen.add(va)
        off = self.img.va_to_off(va)
        if off is None:
            return False
        answer = False
        subs: list[int] = []
        pend = None
        rcx_is_stream = True          # rcx holds the stream on entry
        for i in self.body(va):
            o = i.operands
            dst = i.op_str.split(",")[0].strip()
            if (i.mnemonic in ("mov", "lea") and len(o) == 2
                    and o[0].type == CS_OP_REG and "r8" in dst):
                pend = o[1] if o[1].type in (CS_OP_IMM, CS_OP_MEM) else None
            elif i.mnemonic in ("mov", "lea") and len(o) == 2 and dst == "rcx":
                # rcx only still points at the stream if it came from a
                # register. Loaded from memory or a rip-relative global, it
                # is some other object -- which is how the hash-lookup
                # helper is called, and counting that as a stream read is
                # what refused sub_141297490 and the 10 tables behind it.
                rcx_is_stream = (i.mnemonic == "mov"
                                 and o[1].type == CS_OP_REG)
            elif i.mnemonic == "call" and len(o) == 1:
                if o[0].type == CS_OP_MEM:
                    if pend is not None:          # a SIZED read
                        answer = True
                        break
                elif o[0].type == CS_OP_IMM and rcx_is_stream:
                    subs.append(o[0].imm)
                pend = None
                rcx_is_stream = False             # rcx is clobbered by a call
        if not answer:
            answer = any(self.reads_stream(s, depth + 1, seen) for s in subs)
        self._reads[va] = answer
        return answer

    def fixed_loops(self, ins: list):
        """Loops whose trip count is a compile-time constant.

        Not every backward branch is a count-prefixed list. Some readers
        contain a fixed-length loop -- a vector of four, say -- and its
        consumption is fully determined:

            loop: mov r8d, 8 ; call [rax+8]      ; read 8
                  inc esi
                  cmp esi, 4                     ; trip count, an IMMEDIATE
                  jb  loop

        sub_1412656D0 is exactly that: two 4-byte reads, then four 8-byte
        reads. I first mis-read it as a plain 4-byte reader because my
        earlier scan stopped at the failure path's `ret`, and had I asserted
        that width it would have been simply wrong.

        Returns ``[(body_start, branch_addr, trip)]`` for the loops it can
        pin, or None if ANY backward branch cannot be pinned -- because a
        reader with one underivable loop is not derivable, and a partial
        answer here is a wrong width.

        Deliberately narrow: single counter, `cmp reg, imm` immediately
        governing the branch, unsigned/signed below. Anything else is
        refused rather than assumed.
        """
        from capstone import CS_OP_IMM, CS_OP_REG
        by_addr = {i.address: n for n, i in enumerate(ins)}
        loops = []
        for n, i in enumerate(ins):
            o = i.operands
            if not (i.mnemonic.startswith("j") and len(o) == 1
                    and o[0].type == CS_OP_IMM and o[0].imm < i.address):
                continue
            if i.mnemonic not in ("jb", "jl", "jbe", "jle", "jne"):
                return None
            target = o[0].imm
            if target not in by_addr:
                return None
            # the compare governing this branch: nearest preceding
            # `cmp <reg>, <imm>` within a few instructions
            trip = None
            for k in range(n - 1, max(-1, n - 5), -1):
                c = ins[k]
                co = c.operands
                if (c.mnemonic == "cmp" and len(co) == 2
                        and co[0].type == CS_OP_REG
                        and co[1].type == CS_OP_IMM):
                    trip = co[1].imm
                    if i.mnemonic in ("jbe", "jle"):
                        trip += 1          # inclusive bound
                    break
            if trip is None or not (1 <= trip <= 4096):
                return None
            loops.append((by_addr[target], n, trip))
        return loops

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
        ins = self.body(va)
        if not ins:
            return None
        reads: list[int] = []
        subs: list[int] = []
        pend = None
        var_read = False
        has_loop = False
        rcx_is_stream = True          # rcx holds the stream on entry
        for i in ins:
            o = i.operands
            _dst = i.op_str.split(",")[0].strip()
            if (i.mnemonic in ("mov", "lea") and len(o) == 2
                    and _dst == "rcx"):
                # See reads_stream: rcx loaded from memory or a global is
                # not our stream, so a call taking it cannot consume from
                # the stream.
                rcx_is_stream = (i.mnemonic == "mov"
                                 and o[1].type == CS_OP_REG)
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
                    if rcx_is_stream:
                        subs.append(o[0].imm)
                rcx_is_stream = False             # a call clobbers rcx
            if (i.mnemonic.startswith("j") and len(o) == 1
                    and o[0].type == CS_OP_IMM and va <= o[0].imm < i.address):
                has_loop = True
        # A loop is only undecidable when its trip count is unknown -- that
        # is a count-prefixed list, and stage 2 derives its element width.
        # A loop whose bound is an immediate is fully determined, and a loop
        # in a helper that reads nothing consumes nothing either way.
        if has_loop and (reads or var_read
                         or any(self.reads_stream(s) for s in subs)):
            loops = self.fixed_loops(ins)
            if not loops:
                return None
            # Re-attribute: a sized read inside a fixed loop counts `trip`
            # times, and once outside. Anything nested is refused above by
            # fixed_loops returning None for a branch it cannot pin.
            in_loop = {}
            for start, end, trip in loops:
                for idx in range(start, end + 1):
                    if idx in in_loop:
                        return None            # nested / overlapping
                    in_loop[idx] = trip
            total = 0
            pend2 = None
            for idx, i2 in enumerate(ins):
                o2 = i2.operands
                d2 = i2.op_str.split(",")[0].strip()
                if (i2.mnemonic in ("mov", "lea") and len(o2) == 2
                        and o2[0].type == CS_OP_REG and "r8" in d2):
                    pend2 = o2[1].imm if o2[1].type == CS_OP_IMM else None
                elif i2.mnemonic == "call":
                    if (len(o2) == 1 and o2[0].type == CS_OP_MEM
                            and pend2 is not None):
                        total += pend2 * in_loop.get(idx, 1)
                    elif (len(o2) == 1 and o2[0].type == CS_OP_REG
                            and pend2 is not None):
                        # `call r9` -- an indirect sized read inside a loop
                        total += pend2 * in_loop.get(idx, 1)
                    pend2 = None
            if not total:
                return None
            got = ("fixed", total)
            self._memo[va] = got
            return got
        if var_read and reads[:1] == [4]:
            self._memo[va] = ("str", 0)
            return self._memo[va]
        extra, kind = 0, "fixed"
        for s in subs:
            if not self.reads_stream(s):
                continue                            # consumes no stream bytes
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

    def _apply(self, body, p, end, spec):
        """Advance ``p`` past one reader described by ``spec``.

        ``spec`` is ``(kind, n)``. The families mirror the format's own
        grammar, documented in ``semantic/pabgb_types``:

          ('fixed', n)   n bytes                     -- primitive, [T;N]
          ('str',   n)   n bytes, then u32 len + len -- CString and
                                                       LocalizableString
                                                       (which is 13 + n)
          ('list',  n)   u32 count + count * n       -- CArray<fixed>
          ('slist', 0)   u32 count + count*(u32 len + len)
                                                     -- CArray<CString>
          ('opt',   n)   u8 flag + (n bytes if set)  -- COptional<fixed>
        """
        kind, n = spec
        if kind == "fixed":
            return p + n
        if kind == "str":
            p += n
            if p + 4 > end:
                return None
            ln = struct.unpack_from("<I", body, p)[0]
            p += 4
            if ln > 2_000_000 or p + ln > end:
                return None
            return p + ln
        if kind == "list":
            if p + 4 > end:
                return None
            cnt = struct.unpack_from("<I", body, p)[0]
            p += 4
            if cnt > 100_000 or p + cnt * n > end:
                return None
            return p + cnt * n
        if kind == "slist":
            if p + 4 > end:
                return None
            cnt = struct.unpack_from("<I", body, p)[0]
            p += 4
            if cnt > 100_000:
                return None
            for _ in range(cnt):
                if p + 4 > end:
                    return None
                ln = struct.unpack_from("<I", body, p)[0]
                p += 4
                if ln > 2_000_000 or p + ln > end:
                    return None
                p += ln
            return p
        if kind == "opt":
            if p + 1 > end:
                return None
            flag = body[p]
            p += 1
            if flag == 0:
                return p
            if flag != 1 or p + n > end:
                return None       # a real COptional flag is 0 or 1
            return p + n
        return None

    def _consume(self, body, p, end, fields, elem):
        """``elem`` maps an unresolved callee to a candidate ``(kind, n)``."""
        for name, kind, val in fields:
            if kind == "fixed":
                p += val
            elif kind == "call":
                m = self.model.get(val)
                if m is None:
                    m = elem.get(val)
                if m is None:
                    return None, name
                t, base = m
                # stage-1 models use 'strplus' for "base then a string"
                spec = ("str", base) if t in ("str", "strplus") else \
                       ("list", base) if t == "list" else ("fixed", base)
                p = self._apply(body, p, end, spec)
                if p is None:
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

    def candidates(self):
        """Every reader shape stage 2 will try, in one flat list.

        Widened from list-only. A reader stage 1 refused is not necessarily
        a count-prefixed list -- it may be a fixed width or a string whose
        derivation failed for an unrelated reason (an unaccounted subcall,
        say). Only offering the list form meant those could never solve no
        matter how well constrained they were by the data.

        Unique-or-nothing now applies across the WHOLE space: if a fixed
        width and a list width both tile, the data cannot tell them apart
        and the answer is unknown.
        """
        out = [("fixed", n) for n in range(MAX_ELEM + 1)]
        out += [("list", n) for n in range(1, MAX_ELEM + 1)]
        out += [("str", n) for n in range(33)]
        return out

    def search(self, body, ent, key_size, fields, unknown: list[int]):
        """Every shape assignment that tiles the WHOLE table.

        One or two unknowns. A spread of records prunes first and only
        survivors are verified in full -- sound, because a shape that tiles
        every record must also tile any subset, so a subset failure cannot
        be a false rejection.
        """
        n = len(ent)
        step = max(1, n // 24)
        probe = list(range(0, n, step))[:24] or [0]
        cands = self.candidates()

        if len(unknown) == 1:
            c = unknown[0]
            cheap = [x for x in cands
                     if self.tiles(body, ent, key_size, fields,
                                   {**self.elem, c: x}, probe)]
            return [(x,) for x in cheap
                    if self.tiles(body, ent, key_size, fields,
                                  {**self.elem, c: x})]

        a, b = unknown
        # Two unknowns over the full space would be ~300^2 tilings per
        # table. Prune each slot alone first: a shape that cannot tile the
        # probe for ANY partner cannot be part of a full solution.
        viable_a = [x for x in cands
                    if any(self.tiles(body, ent, key_size, fields,
                                      {**self.elem, a: x, b: y}, probe[:4])
                           for y in cands[::8])]
        viable_b = [y for y in cands
                    if any(self.tiles(body, ent, key_size, fields,
                                      {**self.elem, a: x, b: y}, probe[:4])
                           for x in viable_a[::8] or cands[::8])]
        cheap = [(x, y) for x in viable_a for y in viable_b
                 if self.tiles(body, ent, key_size, fields,
                               {**self.elem, a: x, b: y}, probe)]
        return [(x, y) for x, y in cheap
                if self.tiles(body, ent, key_size, fields,
                              {**self.elem, a: x, b: y})]


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
        d._memo.clear()
        got = d.solve_reader(c)
        if got is not None:
            d.model[c] = got
    auto = len(d.model)
    hand = 0
    for c, m in HAND_VERIFIED.items():
        if c in callees and c not in d.model:
            d.model[c] = m
            hand += 1
    print(f"sub-readers: {auto} solved statically, {hand} hand-verified "
          f"(of {len(callees)} used as field readers)")

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
                    shape = (f"4 + count*{e[1]}" if e[0] == "list"
                             else f"{e[1]} + str" if e[0] == "str"
                             else f"{e[1]} bytes")
                    print(f"  round {rnd}: sub_{c:X} = {shape}"
                          f"   (from {stem}, {len(ent)} records{note})")
            if len(pinned) == len(unknown):
                proven[stem] = len(ent)
            else:
                free = [c for c in unknown if c not in pinned]
                ambiguous[stem] = sorted({str(s[unknown.index(free[0])])
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
            print(f"   {s:<32} {len(hits)} shapes tile, e.g. {hits[:4]}")
    print("\nReminder: tiling proves STRUCTURE, not semantics. Gate every "
          "new field to _verified_fields after a value spot-check before "
          "anything writes to it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
