"""
hoger.ghio.marker — programmatically inject RH_IN:/RH_OUT: marker groups into
a .gh file (with backup and idempotency).

Ground truth for the GH_Group chunk structure and the /io marker-detection
rule is documented in
docs/superpowers/plans/2026-07-04-hoger-v2-auto-convert.md section 0.3/0.4,
and was validated by the prototypes in scratch/spike_v2/ (v3/v3b/v4 series)
against a real Rhino.Compute instance.

Key facts this module relies on:

- A "marker group" is a GH_Group Object chunk (component-class GUID
  ``c552a431-af5b-46a9-a8a4-0fcbc27ef596``) appended as a top-level child of
  the DefinitionObjects chunk. Its Container carries: Border(int 1),
  Colour (gh_drawing_color, optional/cosmetic), Description(str),
  ID (indexed guid, one per member, count in ID_Count), InstanceGuid (new
  guid), Name="Group", NickName (this is where "RH_IN:xxx"/"RH_OUT:xxx"
  lives -- /io matches on the case-sensitive substring "RH_IN"/"RH_OUT"
  anywhere in NickName, but HOGER always writes the strict
  "RH_IN:<name>"/"RH_OUT:<name>" form), and an empty Attributes sub-chunk.
- DefinitionObjects carries its own child count in an "ObjectCount" int
  item. GH_IO's SetInt32 does not overwrite an existing item of the same
  name -- it appends a duplicate -- so updating the count requires
  RemoveItem("ObjectCount") before SetInt32.
- Colour is a "gh_drawing_color" typed item, written via
  GH_Chunk.SetDrawingColor(name, System.Drawing.Color). It is cosmetic only
  (GH assigns a default group colour if omitted) -- ghio_helpers.set_drawing_color
  wraps the reflection call so we still write a real value rather than
  relying on omission.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hoger.ghio import ghio_helpers as gh
from hoger.ghio.loader import get_archive_class
from hoger.ghio.scanner import scan_gh

GH_GROUP_TYPE_GUID = "c552a431-af5b-46a9-a8a4-0fcbc27ef596"
_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_MARK_GROUP_RE = re.compile(r"^RH_(IN|OUT):")

# Cosmetic default colours for injected groups, as (alpha, red, green, blue)
# tuples -- the exact argument order of System.Drawing.Color.FromArgb, which
# ghio_helpers.set_drawing_color unpacks positionally. /io ignores the colour;
# it only affects how the group renders in the Grasshopper canvas.
_INPUT_COLOUR = (150, 255, 170, 100)  # semi-transparent orange: input marks
_OUTPUT_COLOUR = (150, 170, 200, 255)  # semi-transparent blue: output marks

_INPUT_DESCRIPTION = "HOGER auto-generated input mark"
_OUTPUT_DESCRIPTION = "HOGER auto-generated output mark"


class MarkError(ValueError):
    """User error: invalid name format, unknown guid, duplicate guid, etc."""


@dataclass
class MarkResult:
    backup_path: str | None
    marked_inputs: list
    marked_outputs: list
    updated: list


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise MarkError(
            f"invalid mark name {name!r}: must match ^[A-Za-z0-9_]+$ "
            "(no spaces, no unicode, non-empty)"
        )
    if "RH_IN" in name or "RH_OUT" in name:
        raise MarkError(
            f"invalid mark name {name!r}: must not contain 'RH_IN' or 'RH_OUT' "
            "(would collide with the group NickName prefix)"
        )


def _validate_marks_and_build_plan(
    input_marks: list, output_marks: list, known_guids: set
) -> list:
    """Validate every requested mark up front (name format, guid existence,
    no duplicate guid within the call) and return a flat ordered plan list
    of {"guid", "name", "kind"} dicts ("kind" is "IN" or "OUT"). Raises
    MarkError on the first problem found -- nothing is written to disk by
    this function or by the caller before it returns successfully.

    Guid comparison is case-insensitive: `known_guids` must be a set of
    lowercase guid strings, and each plan entry's "guid" is the normalized
    (lowercase) form so all downstream use (group ID item, existing-group
    lookup, post-write verification) operates on one canonical casing.
    Error messages echo the caller's original string.
    """
    plan = []
    seen_guids = set()

    for kind, marks in (("IN", input_marks), ("OUT", output_marks)):
        for m in marks:
            guid_str = m["guid"]
            name = m["name"]
            guid_norm = guid_str.lower()

            _validate_name(name)

            if guid_norm not in known_guids:
                raise MarkError(
                    f"guid {guid_str!r} does not exist in this file's objects"
                )

            if guid_norm in seen_guids:
                raise MarkError(
                    f"guid {guid_str!r} is marked more than once in the same call"
                )
            seen_guids.add(guid_norm)

            plan.append({"guid": guid_norm, "name": name, "kind": kind})

    return plan


def _top_level_group_objects(def_objects) -> list:
    """Return [{"index", "chunk", "container", "nickname"}] for every
    top-level Object chunk whose Name == "Group"."""
    groups = []
    for i, ch in enumerate(gh.chunks_of(def_objects)):
        if not gh.item_exists(ch, "Name"):
            continue
        try:
            type_name = gh.get_string(ch, "Name")
        except Exception:
            continue
        if type_name != "Group":
            continue
        container = gh.find_chunk(ch, "Container")
        if container is None:
            continue
        nickname = None
        if gh.item_exists(container, "NickName"):
            try:
                nickname = gh.get_string(container, "NickName")
            except Exception:
                nickname = None
        groups.append(
            {"index": i, "chunk": ch, "container": container, "nickname": nickname}
        )
    return groups


def _mark_groups_containing(groups: list, member_guid: str) -> list:
    """Return every group dict whose ID list contains `member_guid` and whose
    NickName matches the HOGER marker pattern (^RH_(IN|OUT):).

    Returning *all* matches (not just the first) matters: if an object
    belongs to two or more marker groups at once, renaming just one of them
    is ambiguous (the scanner reports the last group's NickName as
    existing_mark, so renaming the first would "succeed" and then fail
    post-write verification). apply_marks rejects that state up front.
    """
    matches = []
    for g in groups:
        nickname = g["nickname"]
        if not nickname or not _MARK_GROUP_RE.match(nickname):
            continue
        container = g["container"]
        id_count = 0
        if gh.item_exists(container, "ID_Count"):
            try:
                id_count = gh.get_int32(container, "ID_Count")
            except Exception:
                id_count = 0
        for k in range(id_count):
            if not gh.item_exists(container, "ID", k):
                continue
            try:
                member = str(gh.get_guid(container, "ID", k))
            except Exception:
                continue
            if member.lower() == member_guid.lower():
                matches.append(g)
                break
    return matches


def _discard(tmp_path: Path) -> None:
    """Best-effort removal of a temp file on a failure path."""
    try:
        tmp_path.unlink()
    except OSError:
        pass


def _write_new_group(def_objects, index: int, member_guid: str, nickname: str, kind: str) -> None:
    obj_chunk = gh.create_chunk(def_objects, "Object", index)
    gh.set_guid(obj_chunk, "GUID", GH_GROUP_TYPE_GUID)
    gh.set_string(obj_chunk, "Name", "Group")

    container = gh.create_chunk(obj_chunk, "Container")
    gh.set_int32(container, "Border", 1)
    colour = _INPUT_COLOUR if kind == "IN" else _OUTPUT_COLOUR
    gh.set_drawing_color(container, "Colour", colour)
    description = _INPUT_DESCRIPTION if kind == "IN" else _OUTPUT_DESCRIPTION
    gh.set_string(container, "Description", description)
    gh.set_guid(container, "ID", member_guid, 0)
    gh.set_int32(container, "ID_Count", 1)
    gh.set_guid(container, "InstanceGuid", str(uuid.uuid4()))
    gh.set_string(container, "Name", "Group")
    gh.set_string(container, "NickName", nickname)
    gh.create_chunk(container, "Attributes")


def apply_marks(
    path,
    input_marks: list,
    output_marks: list,
    backup: bool = True,
) -> MarkResult:
    """Inject RH_IN:/RH_OUT: marker groups into the .gh file at `path`.

    input_marks / output_marks: [{"guid": "<instance guid>", "name": "<param name>"}]

    All-or-nothing: every mark is validated (name format, guid existence,
    no duplicate guid across the whole call, no ambiguous multi-group
    membership) *before* anything is written to disk. If any validation
    fails, MarkError is raised and the file is left byte-for-byte
    unmodified (no .bak is created either).

    Atomic write: the modified archive is written to a sibling temp file
    (same directory, therefore same volume) and post-write verification
    runs against that temp file; only after verification passes is the
    temp os.replace()'d over the original. Invariant: **if apply_marks
    raises, the user's file is byte-for-byte identical to before the
    call** -- on every failure path (validation error, WriteToFile
    returning False or raising, verification mismatch) the original has
    never been touched, with or without backup. A crash mid-write can at
    worst leave a stray .gh.tmp sibling, never a corrupted original.

    Idempotency: if the target object is already a member of exactly one
    existing HOGER-style marker group (NickName matches ^RH_(IN|OUT):),
    that group's NickName is renamed to the new value instead of adding a
    new group; such marks are reported in MarkResult.updated rather than
    marked_inputs/marked_outputs. Membership in two or more marker groups
    at once is ambiguous and rejected with MarkError (clean it up manually
    in Grasshopper first).

    Raises:
        MarkError: invalid name, unknown guid, duplicate guid, or
            ambiguous multi-group membership (raised before any write).
        hoger.ghio.loader.GhioUnavailable: GH_IO.dll not available.
        RuntimeError: writing or post-write verification failed; the
            original file is unmodified (the message says so and mentions
            backup_path when one was made).
    """
    path = Path(path)

    archive_cls = get_archive_class()
    archive = archive_cls()
    if not archive.ReadFromFile(str(path)):
        raise MarkError(f"could not read GH archive: {path}")

    root = archive.get_GetRootNode()
    definition = gh.find_chunk(root, "Definition")
    if definition is None:
        raise MarkError(f"not a valid GH archive (no Definition chunk): {path}")
    def_objects = gh.find_chunk(definition, "DefinitionObjects")
    if def_objects is None:
        raise MarkError(
            f"not a valid GH archive (no DefinitionObjects chunk): {path}"
        )

    # Collect every InstanceGuid present anywhere at the top level (for
    # guid-existence validation) -- top-level objects only, matching the
    # scanner's notion of "object" (guids nested inside components are not
    # valid mark targets since the group must wrap a top-level Object chunk).
    # Guid comparison is case-insensitive throughout: System.Guid's string
    # formatting (lowercase) is not guaranteed to match the caller's casing,
    # so the known set is normalized to lowercase and
    # _validate_marks_and_build_plan normalizes each requested guid the same
    # way (plan entries carry the normalized form; error messages echo the
    # caller's original string).
    known_guids = set()
    for ch in gh.chunks_of(def_objects):
        container = gh.find_chunk(ch, "Container")
        if container is None:
            continue
        if not gh.item_exists(container, "InstanceGuid"):
            continue
        try:
            inst_guid = str(gh.get_guid(container, "InstanceGuid"))
        except Exception:
            continue
        known_guids.add(inst_guid.lower())

    plan = _validate_marks_and_build_plan(input_marks, output_marks, known_guids)

    # Multi-group membership check (still validation -- nothing written yet):
    # an object sitting in >= 2 marker groups cannot be renamed unambiguously
    # (which group is "the" mark?), and the scanner would report a different
    # group than the one we renamed. Reject the whole call before touching
    # disk; the user must clean up the duplicate groups in Grasshopper first.
    groups = _top_level_group_objects(def_objects)
    for item in plan:
        item["existing_groups"] = _mark_groups_containing(groups, item["guid"])
        if len(item["existing_groups"]) >= 2:
            raise MarkError(
                f"object {item['guid']!r} already belongs to "
                f"{len(item['existing_groups'])} marker groups at once; the "
                "existing mark state is ambiguous -- please clean up the "
                "groups manually in Grasshopper first"
            )

    # ---- validation complete; now safe to touch disk ----

    backup_path = None
    if backup:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}.{timestamp}.bak")
        shutil.copy2(str(path), str(backup_path))

    marked_inputs = []
    marked_outputs = []
    updated = []

    obj_count = gh.get_int32(def_objects, "ObjectCount")
    next_index = obj_count

    for item in plan:
        member_guid = item["guid"]
        name = item["name"]
        kind = item["kind"]
        nickname = f"RH_IN:{name}" if kind == "IN" else f"RH_OUT:{name}"

        existing_group = item["existing_groups"][0] if item["existing_groups"] else None
        if existing_group is not None:
            gh.remove_item(existing_group["container"], "NickName")
            gh.set_string(existing_group["container"], "NickName", nickname)
            existing_group["nickname"] = nickname
            updated.append(nickname)
            continue

        _write_new_group(def_objects, next_index, member_guid, nickname, kind)
        next_index += 1

        if kind == "IN":
            marked_inputs.append(nickname)
        else:
            marked_outputs.append(nickname)

    new_obj_count = obj_count + (len(marked_inputs) + len(marked_outputs))
    if new_obj_count != obj_count:
        gh.remove_item(def_objects, "ObjectCount")
        gh.set_int32(def_objects, "ObjectCount", new_obj_count)

    # ---- atomic write: temp sibling -> verify -> os.replace ----
    #
    # Writing directly over the original would leave a truncated, corrupt
    # file if the process died mid-write (disk full, power loss, ...). The
    # temp lives in the same directory (same volume), so os.replace() is an
    # atomic rename; the original is only ever swapped for a fully written,
    # fully *verified* file. On every failure below, the original has not
    # been touched at all.
    #
    # The temp name must end in ".gh": GH_Archive.WriteToFile dispatches its
    # serialization format on the file extension and rejects unrecognized
    # ones outright ("file_name is not of a recognized type"), so a plain
    # ".tmp" suffix cannot be written at all (verified empirically).

    intact_note = "; the original file has not been modified" + (
        f" (backup at {backup_path})"
        if backup_path
        else " (no backup was made: backup=False)"
    )

    tmp_path = path.with_name(path.stem + ".tmp.gh")
    try:
        write_ok = archive.WriteToFile(str(tmp_path), True, False)
    except Exception as exc:
        _discard(tmp_path)
        raise RuntimeError(
            f"GH_IO WriteToFile failed for {path}: {exc}{intact_note}"
        ) from exc
    if not write_ok:
        _discard(tmp_path)
        raise RuntimeError(
            f"GH_IO WriteToFile returned False for {path}{intact_note}"
        )

    # ---- post-write verification (against the temp file, BEFORE it
    # replaces the original) ----

    expected = {}
    for item in plan:
        nickname = f"RH_IN:{item['name']}" if item["kind"] == "IN" else f"RH_OUT:{item['name']}"
        expected[item["guid"].lower()] = nickname

    try:
        verify_scan = scan_gh(tmp_path)
    except Exception as exc:
        _discard(tmp_path)
        raise RuntimeError(
            f"post-write verification failed: could not re-scan the written "
            f"archive for {path}: {exc}{intact_note}"
        ) from exc

    actual = {}
    for cand in list(verify_scan.inputs) + list(verify_scan.outputs):
        actual[cand.instance_guid.lower()] = cand.existing_mark

    mismatches = []
    for guid_lower, expected_nick in expected.items():
        actual_nick = actual.get(guid_lower)
        if actual_nick != expected_nick:
            mismatches.append((guid_lower, expected_nick, actual_nick))

    if mismatches:
        _discard(tmp_path)
        raise RuntimeError(
            "post-write verification failed: marks did not round-trip as "
            f"expected: {mismatches}{intact_note}"
        )

    try:
        os.replace(str(tmp_path), str(path))
    except OSError:
        _discard(tmp_path)
        raise

    return MarkResult(
        backup_path=str(backup_path) if backup_path else None,
        marked_inputs=marked_inputs,
        marked_outputs=marked_outputs,
        updated=updated,
    )
