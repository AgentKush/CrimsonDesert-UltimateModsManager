"""characterinfo.pabgb field writer for Format 3 mods (GitHub #150).

Female Animations (and similar character-swap mods) ship Format 3
intents targeting characterinfo.pabgb with five fields:

  upper_chart.group_lookup   u32  the upper action-chart package hash
  lower_chart.group_lookup   u32  the lower action-chart package hash
  skeleton_name              u32  the skeleton package hash
  lookup_25                  u32  the skeleton-variation package hash
  flag_c                     u8   a 0/1/2 enum in the post-block run

CDUMM's characterinfo PABGB schema is a positional, name-less
decompiled structure, so the generic Format 3 writer cannot resolve a
write position from a field name. All five fields sit at fixed
offsets inside (or just past) the action-chart / skeleton block, and
that block is located per record by the characterinfo parser walk.

The field-to-slot mapping was verified against vanilla 1.07.00, not
guessed: the Damian record holds the exact four u32 hashes the mod
copies onto Kliff, one per slot, and the flag_c slot holds only 0/1/2
across all 7027 records with Damian holding 2 (the value the mod
sets). See GitHub #150.

Every field is a fixed-size primitive, so each intent becomes one
absolute-offset replace; no record ever changes size and the
companion .pabgh never needs rebuilding.
"""
from __future__ import annotations

import logging
import struct

from cdumm.archive.format_parsers.characterinfo_full_parser import (
    parse_entry,
    parse_pabgh_index,
)

logger = logging.getLogger(__name__)

# Mod field name -> (parse_entry offset key, struct format, byte width).
_FIELD_MAP: dict[str, tuple[str, str, int]] = {
    "upper_chart.group_lookup":
        ("_upperActionChartPackageGroupName_offset", "<I", 4),
    "lower_chart.group_lookup":
        ("_lowerActionChartPackageGroupName_offset", "<I", 4),
    # GitHub #192 (Yorivel): mesh / visual-swap mods set the appearance
    # hash and the model-path hash. Both are plain u32 name-hash slots
    # in the same action-chart block (block+12 / block+16), located by
    # the same parser walk as the four #150 u32 fields.
    "lookup_22": ("_appearanceName_stream_offset", "<I", 4),
    "lookup_24": ("_characterPrefabPath_stream_offset", "<I", 4),
    "skeleton_name": ("_skeletonName_offset", "<I", 4),
    "lookup_25": ("_skeletonVariationName_offset", "<I", 4),
    "flag_c": ("_flagC_offset", "<B", 1),
}

# Mount / vehicle scalar fields. Unlike the appearance fields above, these are
# NOT located by parse_entry -- that parser fails on the real mount records (it
# only parses the ~6300 all-zero non-mount records). They sit past two
# variable-length LocalizableStrings + a CString, so their offset is record-
# dependent and is resolved by the schema `_ordered_fields` walk
# (cdumm.semantic.parser.field_offsets_in_record, the same walk the Game Data
# grid displays them with). Each is a fixed-width primitive => a `set` is one
# absolute-offset replace, no size change. Verified byte-exact on the live 1.13
# install (18/18 real mounts) + a 33/33 vehicleinfo foreign-key cross-check.
_MOUNT_FIELD_WIDTHS: dict[str, tuple[str, int]] = {
    "_vehicleInfo": ("<H", 2),
    "_callMercenaryCoolTime": ("<Q", 8),
    "_callMercenarySpawnDuration": ("<Q", 8),
    "_mercenaryCoolTimeType": ("<B", 1),
}

SUPPORTED_FIELDS = frozenset(_FIELD_MAP) | frozenset(_MOUNT_FIELD_WIDTHS)

_ci_key_size_cache: dict[str, int] = {}


def _mount_change(
    body: bytes,
    header: bytes,
    idx: dict[int, int],
    order: list[tuple[int, int]],
    name_to_key: dict[str, int],
    entry_name: str,
    raw_key: int,
    field: str,
    new_value: object,
) -> dict | None:
    """Resolve one mount-field Format 3 intent to an absolute-offset replace.

    Locates the record by key (the maker always supplies it; falls back to the
    parse_entry name map for the non-mount records that carries), walks the
    schema `_ordered_fields` to the field's record-dependent offset, and
    returns a v2 change dict, or None (logged) if anything doesn't resolve.
    """
    fmt, width = _MOUNT_FIELD_WIDTHS[field]
    if isinstance(new_value, bool) or not isinstance(new_value, int):
        logger.warning("characterinfo: mount intent %s on %r has non-integer "
                       "value %r, skipping", field, entry_name, new_value)
        return None
    key = name_to_key.get(entry_name)
    if key is None and raw_key:
        key = raw_key
    start = idx.get(key) if key is not None else None
    if start is None:
        logger.warning("characterinfo: mount entry %r (key=%r) not found, "
                       "skipping intent on %s", entry_name, raw_key, field)
        return None
    ranks = {k: i for i, (k, _) in enumerate(order)}
    rank = ranks.get(key)
    end = (order[rank + 1][1]
           if rank is not None and rank + 1 < len(order) else len(body))
    from cdumm.semantic import parser as _sem
    _sem.init_schemas()
    schema = _sem.get_schema("characterinfo")
    if schema is None:
        return None
    key_size = _ci_key_size_cache.get("v")
    if key_size is None:
        key_size, _ = _sem.parse_pabgh_index(header, "characterinfo")
        _ci_key_size_cache["v"] = key_size
    rel = _sem.field_offsets_in_record(
        body[start:end], schema, key_size).get(field)
    if rel is None:
        logger.warning("characterinfo: could not walk to mount field %r for "
                       "%r, skipping", field, entry_name)
        return None
    abs_off = start + rel
    if abs_off + width > len(body):
        return None
    try:
        patched = struct.pack(fmt, new_value)
    except struct.error:
        logger.warning("characterinfo: value %r out of range for mount field "
                       "%r (%d-byte), skipping", new_value, field, width)
        return None
    return {
        "offset": abs_off,
        "original": bytes(body[abs_off:abs_off + width]).hex(),
        "patched": patched.hex(),
        "label": f"{entry_name}.{field}",
    }


def build_characterinfo_changes(
    vanilla_body: bytes,
    vanilla_header: bytes,
    intents: list[tuple[str, int, str, object]],
) -> list[dict]:
    """Resolve Format 3 characterinfo intents into v2 change dicts.

    ``intents`` is a list of (entry_name, key, field, new_value):
      * entry_name - the record's name (Format 3 mods locate by name).
      * key        - the numeric record key, or 0 when the mod omits it.
      * field      - one of SUPPORTED_FIELDS.
      * new_value  - the integer value to set.

    Returns one absolute-offset replace change per resolved intent.
    Intents whose field is unsupported, whose record cannot be found or
    parsed, or whose value does not fit the field width are dropped
    with a logged warning, never raising.
    """
    idx = parse_pabgh_index(vanilla_header)  # {key: record offset}
    order = sorted(idx.items(), key=lambda kv: kv[1])

    parsed: dict[int, dict] = {}
    name_to_key: dict[str, int] = {}
    for rank, (key, start) in enumerate(order):
        end = (order[rank + 1][1]
               if rank + 1 < len(order) else len(vanilla_body))
        rec = parse_entry(vanilla_body, start, end)
        if rec is None:
            continue
        parsed[key] = rec
        name = rec.get("name")
        if name:
            name_to_key.setdefault(name, key)

    changes: list[dict] = []
    for entry_name, raw_key, field, new_value in intents:
        if field in _MOUNT_FIELD_WIDTHS:
            ch = _mount_change(vanilla_body, vanilla_header, idx, order,
                               name_to_key, entry_name, raw_key, field,
                               new_value)
            if ch is not None:
                changes.append(ch)
            continue
        spec = _FIELD_MAP.get(field)
        if spec is None:
            logger.warning(
                "characterinfo: field %r is not supported, skipping",
                field)
            continue
        if isinstance(new_value, bool) or not isinstance(new_value, int):
            logger.warning(
                "characterinfo: intent %s on %r has non-integer value "
                "%r, skipping", field, entry_name, new_value)
            continue
        key = name_to_key.get(entry_name)
        if key is None and raw_key:
            key = raw_key
        rec = parsed.get(key) if key is not None else None
        if rec is None:
            logger.warning(
                "characterinfo: entry %r (key=%r) not found or not "
                "parsable, skipping intent on %s",
                entry_name, raw_key, field)
            continue
        off_key, fmt, width = spec
        abs_off = rec.get(off_key)
        if abs_off is None:
            logger.warning(
                "characterinfo: could not locate field %r for entry "
                "%r (record parsed only partially), skipping",
                field, entry_name)
            continue
        if abs_off + width > len(vanilla_body):
            continue
        try:
            patched = struct.pack(fmt, new_value)
        except struct.error:
            logger.warning(
                "characterinfo: value %r is out of range for field "
                "%r (%d-byte), skipping", new_value, field, width)
            continue
        original = bytes(vanilla_body[abs_off:abs_off + width])
        changes.append({
            "offset": abs_off,
            "original": original.hex(),
            "patched": patched.hex(),
            "label": f"{entry_name}.{field}",
        })
    return changes
