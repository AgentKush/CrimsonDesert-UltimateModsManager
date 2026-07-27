"""interactioninfo.pabgb interaction-range writer (Fast Pickup mods).

"Fast Pickup - Increase Range" (Nexus) sets
``interaction_pivot_list[0].raw_a`` and ``.raw_b`` on five interaction
records so gathering, skinning and insect-catching reach further. All
ten intents applied zero bytes: ``_interactionPivotList`` is field #26
of ``InteractionInfo`` with type ``None`` in the PABGB schema -- no
descriptor -- so the generic walker cannot reach it. ``parse_records``
returns 0 records for this table entirely.

What the fields are
-------------------
The values decode the field. The mod writes ``1084227584`` and
``1077936128``, which are ``0x40A00000`` and ``0x40400000`` -- the raw
bit patterns of f32 ``5.0`` and ``3.0``. DMM calls them "raw" because it
carries the float's bits as an integer.

The schema names them. ``InteractionPivotOverrideData`` ends::

    _interactionUpperHeight   f32    4
    _targetGotoOffset         12B    (a 3-float vector)
    _interactionDistance      f32    4     <- raw_a
    _interactionLowerHeight   f32    4     <- raw_b

So when the upper height is 0.0 and the goto offset is (0, 0, 0) -- the
common case -- the pair is preceded by exactly **16 zero bytes**. That is
a typed prediction from the schema, not a pattern noticed in a hex dump,
and it is what this writer looks for.

Locating it
-----------
Scan the record for a position where the preceding 16 bytes are zero and
the next two f32 are in ``[0.01, 100.0]`` -- a sane interaction range.
**Require exactly one such position.** On the live 1.15 table:

* all five records the mod targets resolve to exactly one match, and it
  is the right one (``Gimmick_PickUp`` 2.5/2.5, ``SmallAnimal_Skin``
  1.5/2.0, ``Animal_Skin`` 1.7/2.0, ``Gimmick_Collect`` 2.5/3.0,
  ``Insect_Catch`` 2.5/2.6);
* 291 of 393 records resolve uniquely, 8 have no match and 94 have more
  than one.

Records in that last group are **refused**, not guessed at. The
16-zero frame only holds when upper height and goto offset are zero; on
a record where they aren't, the scan would find some other pair of
floats, and writing there would corrupt an unrelated field. Ambiguity is
a refusal.

Three earlier locators were tried and thrown away, recorded here so they
aren't retried: a fixed offset from the record's inner id-string (73 on
four records, 89 on ``Gimmick_Collect``); a fixed offset from the name
(varies); and the maximal zero-run end, which is contaminated because
``2.5`` is ``00 00 20 40`` and the run swallows the value's own leading
zero bytes.

Writes are 4 bytes at the same width, so the companion ``.pabgh`` needs
no rebuild.
"""
from __future__ import annotations

import logging
import math
import struct

logger = logging.getLogger(__name__)

#: The zero frame: _interactionUpperHeight (4) + _targetGotoOffset (12).
_FRAME = b"\x00" * 16

#: A plausible interaction range in metres. Used both to recognise the
#: pair when locating it and to sanity-check a value before writing.
_MIN_RANGE = 0.01
_MAX_RANGE = 100.0

#: Field spelling -> byte offset within the located pair.
_PAIR_FIELDS = {
    "interaction_pivot_list[0].raw_a": 0,   # _interactionDistance
    "interaction_pivot_list[0].raw_b": 4,   # _interactionLowerHeight
}


def _record_bounds(header: bytes, body: bytes) -> dict[int, tuple[int, int]]:
    from cdumm.semantic.parser import parse_pabgh_index
    _keys, offs = parse_pabgh_index(header, "interactioninfo")
    ordered = sorted(offs.items(), key=lambda kv: kv[1])
    return {
        k: (o, ordered[i + 1][1] if i + 1 < len(ordered) else len(body))
        for i, (k, o) in enumerate(ordered)
    }


def locate_pivot_pair(body: bytes, lo: int, hi: int) -> int | None:
    """Absolute offset of ``raw_a``, or None when it isn't unambiguous.

    None means "refuse": either no position carries the 16-zero frame
    followed by a sane range pair, or several do and picking one would
    be a guess.
    """
    if hi - lo < 12:
        return None
    name_len = struct.unpack_from("<I", body, lo + 4)[0]
    if not 0 < name_len < 250:
        return None
    start = lo + 8 + name_len
    if start >= hi:
        return None

    found: list[int] = []
    i = start
    while i < hi:
        if body[i] != 0:
            i += 1
            continue
        run_end = i
        while run_end < hi and body[run_end] == 0:
            run_end += 1
        if run_end - i >= 16:
            # Candidates sit on the run's OWN 4-byte grid. Fields are
            # 4-aligned and the frame is four u32s, so a real pair is at
            # run_start + 16, +20, +24 ... Scanning every byte instead
            # would accept a window shifted one byte into the value: with
            # the pair set to 3.0 (00 00 40 40) twice, a read one byte
            # early yields 00 00 00 40 = 2.0, which passes the range
            # check and makes the record look ambiguous. That is exactly
            # what happens once this mod has been applied, so an unaligned
            # scan refuses on the second apply. Uniqueness that depends on
            # the stored values is not uniqueness.
            p = i + 16
            while p + 8 <= hi and p <= run_end:
                a = struct.unpack_from("<f", body, p)[0]
                b = struct.unpack_from("<f", body, p + 4)[0]
                if (_MIN_RANGE <= a <= _MAX_RANGE
                        and _MIN_RANGE <= b <= _MAX_RANGE):
                    found.append(p)
                    if len(found) > 1:
                        return None       # ambiguous -> refuse
                p += 4
        i = run_end
    return found[0] if len(found) == 1 else None


def build_interactioninfo_changes(
    vanilla_body: bytes, vanilla_header: bytes, intents: list
) -> tuple[list[dict], list[tuple[object, str]]]:
    """Apply ``interaction_pivot_list[0].raw_a/.raw_b`` set intents.

    Returns ``(changes, dropped)``; each change is a 4-byte
    ``{offset, original, patched}``. No .pabgh companion.
    """
    dropped: list[tuple[object, str]] = []
    bounds = _record_bounds(vanilla_header, vanilla_body)
    writes: dict[int, int] = {}

    for it in intents:
        field = getattr(it, "field", "") or ""
        delta = _PAIR_FIELDS.get(field)
        if delta is None:
            dropped.append((it, (
                f"field {field!r} is not a supported interactioninfo field "
                f"(only interaction_pivot_list[0].raw_a / .raw_b)")))
            continue
        if (getattr(it, "op", "set") or "set") != "set":
            dropped.append((it, (
                f"op {getattr(it, 'op', None)!r} not supported for "
                f"interactioninfo (only 'set')")))
            continue
        raw = getattr(it, "new", None)
        if isinstance(raw, bool) or not isinstance(raw, int) \
                or not 0 <= raw <= 0xFFFFFFFF:
            dropped.append((it, (
                f"value {raw!r} is not a u32 float bit pattern")))
            continue
        val = struct.unpack("<f", struct.pack("<I", raw))[0]
        if not math.isfinite(val) or not _MIN_RANGE <= val <= _MAX_RANGE:
            dropped.append((it, (
                f"value {raw} decodes to {val!r} as f32, outside the "
                f"plausible interaction range "
                f"{_MIN_RANGE}..{_MAX_RANGE}")))
            continue
        key = getattr(it, "key", None)
        if not isinstance(key, int) or key not in bounds:
            dropped.append((it, f"no interactioninfo record with key {key!r}"))
            continue
        lo, hi = bounds[key]
        pos = locate_pivot_pair(vanilla_body, lo, hi)
        if pos is None:
            dropped.append((it, (
                f"interactioninfo record key={key}: the pivot range pair "
                f"could not be located unambiguously (its upper height / "
                f"goto offset are probably non-zero); refusing to write")))
            continue
        entry = getattr(it, "entry", "") or ""
        if entry:
            name_len = struct.unpack_from("<I", vanilla_body, lo + 4)[0]
            name = vanilla_body[lo + 8:lo + 8 + name_len].decode(
                "utf-8", "replace")
            if name != entry:
                dropped.append((it, (
                    f"key {key} is {name!r}, but the intent names {entry!r}")))
                continue
        writes[pos + delta] = raw

    changes = []
    for pos, raw in sorted(writes.items()):
        original = vanilla_body[pos:pos + 4]
        patched = struct.pack("<I", raw)
        if original == patched:
            continue                      # already at the requested value
        changes.append({
            "offset": pos,
            "original": original.hex(),
            "patched": patched.hex(),
        })
    return changes, dropped
