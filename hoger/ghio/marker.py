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

# Cosmetic default colours for injected groups (a, r, g, b). Chosen to be
# visually distinct in the GH canvas; /io does not care about this value.
_INPUT_COLOUR = (150, 255, 170, 100)
_OUTPUT_COLOUR = (150, 170, 200, 255)

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
    """
    plan = []
    seen_guids = set()

    for kind, marks in (("IN", input_marks), ("OUT", output_marks)):
        for m in marks:
            guid_str = m["guid"]
            name = m["name"]

            _validate_name(name)

            if guid_str not in known_guids:
                raise MarkError(
                    f"guid {guid_str!r} does not exist in this file's objects"
                )

            if guid_str in seen_guids:
                raise MarkError(
                    f"guid {guid_str!r} is marked more than once in the same call"
                )
            seen_guids.add(guid_str)

            plan.append({"guid": guid_str, "name": name, "kind": kind})

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


def _find_existing_mark_group(groups: list, member_guid: str):
    """Return the group dict whose ID list contains `member_guid` and whose
    NickName matches the HOGER marker pattern (^RH_(IN|OUT):), or None."""
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
                return g
    return None


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
    no duplicate guid across the whole call) *before* anything is written
    to disk. If any validation fails, MarkError is raised and the file is
    left byte-for-byte unmodified (no .bak is created either).

    Idempotency: if the target object is already a member of an existing
    HOGER-style marker group (NickName matches ^RH_(IN|OUT):), that group's
    NickName is renamed to the new value instead of adding a new group; such
    marks are reported in MarkResult.updated rather than marked_inputs/
    marked_outputs.

    Raises:
        MarkError: invalid name, unknown guid, or duplicate guid (raised
            before any write -- see above).
        hoger.ghio.loader.GhioUnavailable: GH_IO.dll not available.
        RuntimeError: the file was written but a post-write re-read/scan
            did not confirm every mark (backup_path, if any, is mentioned
            in the message so the caller can restore).
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
    known_guids = set()
    top_level = []
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
        top_level.append(inst_guid)

    # Build a case-preserving lookup so error messages/echoing use the
    # caller's original guid string, while comparisons are case-insensitive
    # (System.Guid string formatting is not guaranteed to match caller casing).
    known_guids_lower = {g.lower() for g in known_guids}

    plan = _validate_marks_and_build_plan(
        input_marks, output_marks, known_guids_lower
    )
    # _validate_marks_and_build_plan compares against known_guids_lower using
    # the raw (possibly mixed-case) guid strings from the caller -- normalize
    # here for the comparison to actually be case-insensitive.
    for item in plan:
        item["guid_lower"] = item["guid"].lower()
    missing = [item["guid"] for item in plan if item["guid_lower"] not in known_guids_lower]
    if missing:
        # Should already have been caught above; defensive re-check.
        raise MarkError(f"guid {missing[0]!r} does not exist in this file's objects")

    # ---- validation complete; now safe to touch disk ----

    backup_path = None
    if backup:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}.{timestamp}.bak")
        shutil.copy2(str(path), str(backup_path))

    groups = _top_level_group_objects(def_objects)

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

        existing_group = _find_existing_mark_group(groups, member_guid)
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

    if not archive.WriteToFile(str(path), True, False):
        raise RuntimeError(
            f"GH_IO WriteToFile failed for {path}"
            + (f" (backup available at {backup_path})" if backup_path else "")
        )

    # ---- post-write verification ----

    expected = {}
    for item in plan:
        nickname = f"RH_IN:{item['name']}" if item["kind"] == "IN" else f"RH_OUT:{item['name']}"
        expected[item["guid"].lower()] = nickname

    try:
        verify_scan = scan_gh(path)
    except Exception as exc:
        raise RuntimeError(
            f"post-write verification failed: could not re-scan {path}: {exc}"
            + (f" (backup available at {backup_path})" if backup_path else "")
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
        raise RuntimeError(
            "post-write verification failed: marks did not round-trip as "
            f"expected: {mismatches}"
            + (f" (backup available at {backup_path})" if backup_path else "")
        )

    return MarkResult(
        backup_path=str(backup_path) if backup_path else None,
        marked_inputs=marked_inputs,
        marked_outputs=marked_outputs,
        updated=updated,
    )
