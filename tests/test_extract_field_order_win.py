"""Windows-exe field-order extraction: the cold-block fix, pinned.

The bug this guards against is subtle and silent. Field order is read off
the reflection error strings, one `lea` per field, and those `lea`s appear
in field order *only when they are laid out where they were emitted*. A
conditionally read field gets its error block outlined to the end of the
function, so ordering by the `lea`'s own address drops it to the end of
the table — and the result still looks like a plausible field order.

Measured on the real binary, naive ordering puts three ItemInfo fields
(`_itemUseInfoList`, `_cooltime`, `_maxChargedUseableCount`) at the end
instead of indices 9, 67 and 70. Ordering by the hot-path branch that
*reaches* the error block fixes all three.

The core test below needs no game binary: it assembles a three-field
function whose error blocks are outlined in reverse order, which is the
worst case for the naive rule, and pins that the hot-path rule recovers
the true sequence. The exe-gated test at the bottom checks the same
property against the real ItemInfo deserializer and skips without a game
install.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

capstone = pytest.importorskip(
    "capstone", reason="analysis-only dependency: pip install capstone pefile")

from extract_field_order_win import sweep_bytes

_BASE = 0x1000
_STRINGS = (0x2000, 0x2010, 0x2020)


def _field_block(width: int) -> bytes:
    """One guarded field read whose failure path is a forward jump.

        mov  r8d, <width>          41 B8 imm32
        call qword ptr [rax + 8]   FF 50 08
        test al, al                84 C0
        jne  ok                    75 05      (over the 5-byte jmp)
        jmp  <cold block>          E9 rel32   (patched by the caller)
      ok:
    """
    return (b"\x41\xb8" + struct.pack("<I", width)
            + b"\xff\x50\x08"
            + b"\x84\xc0"
            + b"\x75\x05"
            + b"\xe9\x00\x00\x00\x00")


_BLOCK_LEN = len(_field_block(0))
assert _BLOCK_LEN == 18
_JMP_AT = 13                      # offset of the E9 within a field block


def _build_outlined_function(n: int = 3, reverse_cold: bool = True):
    """Assemble a function with ``n`` fields and outlined error blocks.

    Returns ``(blob, base_va, lea_addrs)`` where ``lea_addrs[i]`` is the
    address of field ``i``'s error-``lea``.

    With ``reverse_cold`` the cold blocks are emitted in reverse field
    order — so sorting by ``lea`` address yields exactly the reverse of
    the truth. That is the strongest available statement of the bug: not
    "slightly off", but maximally wrong.
    """
    hot = bytearray()
    for i in range(n):
        hot += _field_block(4 + i)
    hot += b"\xc3"                                    # ret on the ok path

    cold_base = _BASE + len(hot)
    order = list(reversed(range(n))) if reverse_cold else list(range(n))

    # lea rdx, [rip + disp32]  (7 bytes) + ret (1)
    cold = bytearray()
    lea_addrs: dict[int, int] = {}
    for slot, fld in enumerate(order):
        at = cold_base + slot * 8
        lea_addrs[fld] = at
        disp = _STRINGS[fld % len(_STRINGS)] - (at + 7)
        cold += b"\x48\x8d\x15" + struct.pack("<i", disp) + b"\xc3"

    # Patch each field's jmp to its own cold block.
    for i in range(n):
        at = _BASE + i * _BLOCK_LEN + _JMP_AT
        rel = lea_addrs[i] - (at + 5)
        struct.pack_into("<i", hot, i * _BLOCK_LEN + _JMP_AT + 1, rel)

    return bytes(hot + cold), _BASE, [lea_addrs[i] for i in range(n)]


def test_naive_lea_order_is_wrong_when_error_blocks_are_outlined():
    """The bug, stated as a fact about the input rather than the fix.

    If this ever stops failing to reproduce the reversal, the synthetic
    function has stopped modelling the compiler output it stands in for
    and the fix test below is no longer proving anything.
    """
    blob, base, leas = _build_outlined_function(3)
    func = sweep_bytes(blob, base)

    naive = sorted(range(3), key=lambda i: leas[i])
    assert naive == [2, 1, 0], (
        "cold blocks were emitted in reverse, so lea-address order must be "
        f"the reverse of the true order; got {naive}")
    # And the sweep really did see three outlined blocks.
    assert all(leas[i] in func.inbound for i in range(3)), (
        "each error block must be a branch target, or hot_key has nothing "
        "to work with")


def test_hot_path_order_recovers_the_true_field_order():
    blob, base, leas = _build_outlined_function(3)
    func = sweep_bytes(blob, base)

    got = sorted(range(3), key=lambda i: func.hot_key(leas[i]))
    assert got == [0, 1, 2]

    # The key is the branch that reaches the block, and those branches are
    # in the hot path — strictly increasing, and all below the cold region.
    keys = [func.hot_key(leas[i])[0] for i in range(3)]
    assert keys == sorted(keys)
    assert max(keys) < min(leas)


def test_hot_path_order_is_stable_when_nothing_is_outlined():
    """The fix must not disturb the case the naive rule already got right."""
    blob, base, leas = _build_outlined_function(3, reverse_cold=False)
    func = sweep_bytes(blob, base)
    assert sorted(range(3), key=lambda i: leas[i]) == [0, 1, 2]
    assert sorted(range(3), key=lambda i: func.hot_key(leas[i])) == [0, 1, 2]


def test_hot_key_falls_back_to_the_lea_for_a_fall_through_block():
    """A block nothing branches into keeps its own address as the key.

    Ordering by an absent branch would otherwise collapse those fields
    onto one key and lose their sequence.
    """
    blob, base, _leas = _build_outlined_function(1)
    func = sweep_bytes(blob, base)
    # An address inside the entry block: no inbound branch exists.
    inside = base + 6
    assert func.block_start_of(inside) == base
    assert base not in func.inbound
    assert func.hot_key(inside) == (inside, inside)


def test_hot_key_is_a_total_order_within_one_cold_block():
    """Two fields sharing a cold block must still be separable.

    They would tie on the branch address, so the lea address is the
    tiebreak. Without it `sorted` would keep them in input order, which is
    the string-table order the whole method exists to avoid.
    """
    blob, base, _ = _build_outlined_function(2)
    func = sweep_bytes(blob, base)
    blk = min(func.inbound)
    a, b = blk, blk + 1
    assert func.block_start_of(a) == func.block_start_of(b) == blk
    assert func.hot_key(a) < func.hot_key(b)


# ── against the real binary (skips without a game install) ────────────────

def _game_exe() -> Path | None:
    env = os.environ.get("CDUMM_GAME_EXE")
    if env and Path(env).is_file():
        return Path(env)
    for root in ("C:", "D:", "E:", "F:"):
        for lib in ("SteamLibrary", "Steam"):
            p = Path(f"{root}/{lib}/steamapps/common/Crimson Desert/"
                     "bin64/CrimsonDesert.exe")
            if p.is_file():
                return p
    return None


@pytest.mark.slow
def test_real_exe_hot_path_beats_naive_on_iteminfo():
    """ItemInfo is the real oracle: 101 fields shared with the verified order.

    Naive ordering must FAIL it and hot-path ordering must PASS it. Pinning
    both directions is what stops a future refactor from quietly reverting
    to the address sort and still looking green.
    """
    pytest.importorskip("pefile", reason="analysis-only dependency")
    exe = _game_exe()
    if exe is None:
        pytest.skip("no Crimson Desert install found (set CDUMM_GAME_EXE)")

    from extract_field_order_win import extract_orders

    from cdumm.engine.schema_verify import relative_order_matches

    hot, stats = extract_orders(exe)
    naive, _ = extract_orders(exe, naive=True)
    assert stats["classes"] > 100, "extraction found implausibly few classes"

    r_hot = relative_order_matches("ItemInfo", hot["ItemInfo"])
    r_naive = relative_order_matches("ItemInfo", naive["ItemInfo"])

    assert len(r_hot.shared) >= 90, (
        f"only {len(r_hot.shared)} ItemInfo fields shared with the verified "
        f"order — the oracle has weakened, re-check before trusting this")
    assert r_hot.matches, (
        f"hot-path order diverges at shared index {r_hot.first_divergence}: "
        f"verified {r_hot.shared[r_hot.first_divergence:][:3]} vs extracted "
        f"{r_hot.candidate_sequence[r_hot.first_divergence:][:3]}")
    assert not r_naive.matches, (
        "naive lea-address order now MATCHES on ItemInfo. Either the "
        "compiler stopped outlining error blocks, or the fix has been "
        "reverted and this test is no longer discriminating.")


@pytest.mark.slow
def test_real_exe_corroborates_the_cd116_field_removals():
    """The binary agrees with ORDER_VARIANTS, four ways.

    ``ORDER_VARIANTS['ItemInfo']`` drops four fields for CD 1.16, and its
    comment distinguishes two reasons: `_inventoryInfo` and
    `_gimmickVisualPrefabDataList` are *absent from the binary*, while
    `_repairDataList` and `_prefabDataList` still exist and are dropped
    only because a field-name list cannot express the opaque run 1.16
    wrapped them in.

    That hand-made distinction is independently checkable: the two absent
    ones must not be named by the exe, and the two surviving ones must be.
    """
    pytest.importorskip("pefile", reason="analysis-only dependency")
    exe = _game_exe()
    if exe is None:
        pytest.skip("no Crimson Desert install found (set CDUMM_GAME_EXE)")

    from extract_field_order_win import extract_orders

    from cdumm.engine.schema_verify import ORDER_VARIANTS

    orders, _ = extract_orders(exe)
    named = set(orders["ItemInfo"])
    removed = set(ORDER_VARIANTS["ItemInfo"][0][1])

    assert {"_inventoryInfo", "_gimmickVisualPrefabDataList"} <= removed
    for absent in ("_inventoryInfo", "_gimmickVisualPrefabDataList"):
        assert absent not in named, (
            f"{absent} is named by this build's binary, so the cd116 "
            f"variant's reason for dropping it no longer holds")
    for present in ("_repairDataList", "_prefabDataList"):
        assert present in named, (
            f"{present} is NOT named by this build's binary — the cd116 "
            f"variant says it still exists and is dropped for another "
            f"reason, which is no longer true")
