# Unlocking more tables: extract field order from the game binary

The shipped `pabgb_complete_schema.json` lists each table's fields in **memory
order**, not on-disk **read order**, so ~82 of the game's 134 data tables can't
be decoded (see `GAME_DATA_UNLOCK_ROADMAP.md`). The game itself carries the read
order, in its reflection **error strings**:

> `<Class>의 _<field>를 읽어들이는데 실패했다`  — "Failed to read _<field> of <Class>"

`tools/extract_field_order.py` scans a binary for these, groups them per class in
file order, and runs the result through `cdumm.engine.schema_verify` — so the
output is only trusted if it reproduces the 7 tables CDUMM already knows byte-exact.

## Which binary, and which tool

| binary | tool | what you get |
|---|---|---|
| **macOS** `CrimsonDesert_Steam-*` | `extract_field_order.py` | strings are already in **read order** — a complete order per class |
| **Windows** `CrimsonDesert.exe` | `extract_field_order_win.py` | read order for the fields that **have** an error string |

Proven on 2026-07-11: the Windows exe yields correct field *membership* for **505
reflection classes**, but `extract_field_order.py`'s verification **fails all 7
known tables** on it — because the string-table order is not read order. That
failure is the tool working, not breaking. The macOS binary is specifically the one
whose strings are ordered (unstripped build; this is the method NattKh used to
decode `skill.pabgb`).

That is a limitation of the *string-table* method, not of the Windows exe.

## The Windows route

`extract_field_order_win.py` gets order from the same strings by looking at where
the **code** references them rather than where the linker put them. Each field read
is a guarded call whose failure branch loads that field's message:

```
    lea  rdx, [rsi + 0x70]                  ; destination in the struct
    mov  r8d, 8                             ; stream bytes (primitives only)
    call qword ptr [rax + 8]                ; the sized reader
    test al, al
    jne  ok
    lea  rax, [rip + "CharacterInfo의 _gender를 ..."]     <-- one xref per field
    jmp  fail
ok: ...
```

**Do not sort by the `lea`'s own address.** A conditionally read field has its error
block *outlined* — moved to the end of the function, with a forward branch left
behind — so the address sort drops every such field to the end of the table, and the
result still looks like a plausible order. On ItemInfo it relocates
`_itemUseInfoList`, `_cooltime` and `_maxChargedUseableCount` from indices 9, 67 and
70 to the end, and diverges from the verified order at index 9.

Sort by the **hot-path branch that reaches the error block** instead. It sits
immediately after the field's read, in true sequence, outlined or not. With that:

| table | shared with verified order | address sort | hot-path sort |
|---|---|---|---|
| ItemInfo | 101 | diverges @9 | **exact match** |
| RegionInfo | 19 | `_key` last | `_key` @0, one adjacent pair left |
| CharacterInfo | 7 | diverges @1 | diverges @1 |
| StageInfo | 30 | diverges @6 | diverges @6 |

### Named is not serialised — the trap

An error string proves a field exists **on the type** and is read by the
deserializer. It says nothing about whether that field occupies bytes **in the
record body**. The measured counterexample:

| field | named by exe | in the record body |
|---|---|---|
| `_stringKey` / `_key` | yes | **no** — the entry header consumes them; `_stringKey` *is* the entry name |

Splicing this tool's order into a verified one without pinning those two takes
RegionInfo's walker from a median of 21 fields to 2, on the live table.

Treat the output as an order over the fields that **are** serialised, not as a
list of what to read.

#### But do not over-apply this

A named field a walker skips is **far more often a real missing field** than a
memory-only one, and this very caveat was used to wave away a genuine bug.

`SkillInfo._isNoAlert` (index 25) is named here and was not read by
`_vendor/skillinfo_parser`, which took a run of seven consecutive `u8` flags for
six. **It is serialised**, and inserting it is the fix for
[#355](https://github.com/faisalkindi/CrimsonDesert-UltimateModsManager/issues/355)
— 2013/2013 records on CD 1.16, up from 1424.

An earlier revision of this page cited it as a counterexample, on the strength
of a measurement showing 449/2013. **That measurement was wrong.** It patched
the field *reader* without widening the matching flag-run skip in the boundary
probe, so the brute-force search then landed on wrong offsets. The field was
right; the test of it was incomplete.

So the lesson is narrower than "distrust the tool":

> Decide with an **aggregate decode over a whole table, on both an old and a new
> build**. Per-record signals cannot referee. The boundary search makes a wrong
> layout land exactly on the record end, and the record then round-trips
> byte-exact too — because it writes back whatever it read.

The aggregate is decisive where per-record evidence is blind:

```
layout      CD 1.13          CD 1.16
6 flags     1999/1999        1424/2013
7 flags     1576/1999        2013/2013
```

### Two things it does not give you

**It is not a complete order.** Only fields with an error string are named — 12 of
ItemInfo's 113 and 9 of CharacterInfo's 164 are never named — so something else still
has to place the rest. Verification runs through
`schema_verify.verify_order_source_relative`, which compares on shared names and
reports the unplaced ones instead of pretending they do not exist. A source that
matches but is incomplete **corroborates** an order; it does not become
`_ordered_fields`.

**It does not settle a disagreement by itself.** Where the hot-path order still
disagrees with a verified order, the byte oracle usually cannot referee: `decode_score`
counts bytes consumed, so any permutation that preserves the *total* width over a span
decodes identically. Splicing the extracted CharacterInfo order into the verified one
leaves both at 14/14 fields on 100% of records — the same score, so the fixture cannot
choose. Settling those needs `value_agreement`, not a deeper disassembly.

### Where it corroborates something independently

`ORDER_VARIANTS['ItemInfo']` drops four fields for CD 1.16 and its comment splits them
two ways by hand: `_inventoryInfo` and `_gimmickVisualPrefabDataList` are *absent from
the binary*, while `_repairDataList` and `_prefabDataList` still exist and are dropped
only because a field-name list cannot express the opaque run 1.16 wrapped them in. The
extractor reproduces that split exactly — the first two are not named by the exe, the
second two are. `tests/test_extract_field_order_win.py` pins it.

### Running it

```
python -m pip install capstone pefile        # analysis-only, not app deps
python tools/extract_field_order_win.py "<game>/bin64/CrimsonDesert.exe"
```

`capstone` and `pefile` are deliberately **not** runtime dependencies — nothing that
ships imports them, and the tests skip without them. The exe is opened read-only and
mmapped; it is never copied, patched or executed.

## The macOS route (a complete order, if you can get the binary)

## Getting the macOS executable

You do **not** need the whole macOS install — only the executable
(`CrimsonDesert_Steam-*`, a few hundred MB).

**Option A — you have a Mac:** install Crimson Desert via Steam, then copy the
executable out of the app bundle
(`.../Crimson Desert.app/Contents/MacOS/CrimsonDesert_Steam-*`).

**Option B — SteamCMD on this PC (no Mac needed):**

```
steamcmd +@sSteamCmdForcePlatformType macos +login <your_steam_account> \
         +app_update 3321460 validate +quit
```

App ID is **3321460**. Forcing the macOS platform makes SteamCMD fetch the macOS
build; once the `CrimsonDesert_Steam-*` binary has downloaded you can stop it — the
bulk `.paz` assets aren't needed. (If you'd rather grab just the binary's depot,
look up the macOS depot + manifest on SteamDB for 3321460 and use
`download_depot 3321460 <depot> <manifest>`.)

## Running it

```
python tools/extract_field_order.py  /path/to/CrimsonDesert_Steam-macos
```

- **✅ VERIFIED** → the extraction reproduced all 7 known tables. The orders for the
  other classes can be trusted enough to add to `pabgb_type_overrides.json`
  (`_ordered_fields`), each still gated to `verified_fields` after a quick in-game
  value spot-check.
- **❌ NOT VERIFIED** → it prints the first divergence per failing table and stops.
  Do not use its output; the order isn't right (wrong binary, or the format shifted).

The script never writes a schema itself — it only proposes and verifies. Turning a
verified extraction into `_ordered_fields` entries is the deliberate next step.
