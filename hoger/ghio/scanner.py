"""
hoger.ghio.scanner — recursively scan a .gh file for input/output candidates.

This is a formalization of the prototype in
``scratch/spike_v2/v1_enumerate_graph.py`` (validated against a real
36-object .gh file); see docs/superpowers/plans/2026-07-04-hoger-v2-auto-convert.md
section 0 for the ground-truth facts this module relies on.

Candidate rules (plan section 2):
- Input candidates: Number Slider (current_value/minimum/maximum), Boolean
  Toggle (current_value), Panel (only if it has at least one downstream
  consumer), Value List, and top-level "dangling" params (objects with a
  downstream consumer but no upstream Source — object's own Name is used as
  object_type since we don't know its concrete param kind from chunk data
  alone).
- Output candidates: Panels/params with no downstream consumer at all
  (fed_by records the upstream component(s) feeding them).
- Wiring (feeds / fed_by) requires a full recursive scan of the
  DefinitionObjects chunk tree, because Source[] lists for params nested
  inside components (e.g. a component's own input params) are buried in
  sub-chunks at varying depths.
- existing_mark: an object is "already marked" if its InstanceGuid appears
  in a Group object's `ID` list AND that Group's NickName contains the
  (case-sensitive) substring "RH_IN" or "RH_OUT" anywhere. The *entire*
  NickName (not just the marker substring) is reported as existing_mark.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hoger.ghio import ghio_helpers as gh
from hoger.ghio.loader import get_archive_class

CANDIDATE_INPUT_TYPE_NAMES = {"Number Slider", "Boolean Toggle", "Panel", "Value List"}
GROUP_TYPE_NAME = "Group"

# Slider sub-chunk that carries Min/Max/Value.
_SLIDER_SUBCHUNK = "Slider"


@dataclass
class InputCandidate:
    instance_guid: str
    object_type: str  # "Number Slider" | "Boolean Toggle" | "Panel" | "Value List" | <param Name>
    nickname: str | None
    current_value: str | None
    minimum: float | None
    maximum: float | None
    feeds: list = field(default_factory=list)  # [{"component": "...", "input"/"output": "..."}]
    existing_mark: str | None = None


@dataclass
class OutputCandidate:
    instance_guid: str
    object_type: str
    nickname: str | None
    fed_by: list = field(default_factory=list)
    existing_mark: str | None = None


@dataclass
class ScanResult:
    inputs: list
    outputs: list
    already_marked_count: int
    object_count: int


def _item_present(chunk, name, index=-1) -> bool:
    """Check item existence via ItemExists *before* attempting a typed getter.

    This matters beyond efficiency: GH_IO's GetXxx() methods raise a native
    NullReferenceException (surfaced to Python as
    System.Reflection.TargetInvocationException) when the item is absent.
    That exception is perfectly catchable with a plain `except Exception`,
    but pytest's faulthandler monitor mistakes the underlying CLR/SEH
    exception machinery for a fatal native crash and prints a spurious
    "Windows fatal exception: access violation" for every occurrence (the
    test run still completes and passes correctly, but the output is
    extremely noisy). Checking ItemExists first avoids throwing at all in
    the common "field absent" case, which is expected to happen often here
    (e.g. `SourceCount` is only present on chunks that carry a Source list).
    """
    try:
        return gh.item_exists(chunk, name, index)
    except Exception:
        return False


def _safe_string(chunk, name, index=-1):
    if not _item_present(chunk, name, index):
        return None
    try:
        return gh.get_string(chunk, name, index)
    except Exception:
        return None


def _safe_guid(chunk, name, index=-1):
    if not _item_present(chunk, name, index):
        return None
    try:
        return str(gh.get_guid(chunk, name, index))
    except Exception:
        return None


def _safe_int(chunk, name, index=-1):
    if not _item_present(chunk, name, index):
        return None
    try:
        return gh.get_int32(chunk, name, index)
    except Exception:
        return None


def _safe_double(chunk, name, index=-1):
    if not _item_present(chunk, name, index):
        return None
    try:
        return gh.get_double(chunk, name, index)
    except Exception:
        return None


def _safe_bool(chunk, name, index=-1):
    if not _item_present(chunk, name, index):
        return None
    try:
        return gh.get_boolean(chunk, name, index)
    except Exception:
        return None


def _find_all_param_records(chunk, depth=0, max_depth=12):
    """Recursively scan `chunk`'s whole subtree for (InstanceGuid, NickName,
    Source[]) records. Grasshopper nests per-param Source lists at varying
    depths: standalone top-level params (Slider/Panel/Toggle) carry Source
    directly on their own Container; params living inside a component are
    nested under component-specific sub-chunk names. A recursive scan
    sidesteps needing to know every component's internal naming convention.
    """
    results = []
    if depth > max_depth:
        return results

    has_source = False
    try:
        has_source = gh.item_exists(chunk, "SourceCount")
    except Exception:
        has_source = False

    if has_source:
        try:
            src_count = gh.get_int32(chunk, "SourceCount")
            sources = [str(gh.get_guid(chunk, "Source", k)) for k in range(src_count)]
            inst_guid = _safe_guid(chunk, "InstanceGuid")
            nickname = _safe_string(chunk, "NickName")
            name = _safe_string(chunk, "Name")
            results.append(
                {
                    "instance_guid": inst_guid,
                    "nickname": nickname,
                    "name": name,
                    "sources": sources,
                }
            )
        except Exception:
            pass

    try:
        for sub in gh.chunks_of(chunk):
            results.extend(_find_all_param_records(sub, depth + 1, max_depth))
    except Exception:
        pass

    return results


def _top_level_objects(def_objects):
    """Return list of dicts describing each direct child Object chunk of
    DefinitionObjects (type_name, instance_guid, nickname, sources, chunk)."""
    objects = []
    for i, ch in enumerate(gh.chunks_of(def_objects)):
        type_name = _safe_string(ch, "Name")
        container = gh.find_chunk(ch, "Container")
        if container is None:
            continue
        instance_guid = _safe_guid(container, "InstanceGuid")
        nickname = _safe_string(container, "NickName")
        sources = []
        src_count = _safe_int(container, "SourceCount")
        if src_count:
            for k in range(src_count):
                src = _safe_guid(container, "Source", k)
                if src:
                    sources.append(src)
        objects.append(
            {
                "index": i,
                "type_name": type_name,
                "instance_guid": instance_guid,
                "nickname": nickname,
                "sources": sources,
                "chunk": ch,
                "container": container,
            }
        )
    return objects


def _group_marks(objects):
    """Return dict: member_instance_guid -> full Group NickName, for every
    Group object whose NickName contains "RH_IN" or "RH_OUT" (case-sensitive
    substring, per compute's actual matching rule). Also returns the count
    of such marker groups found.
    """
    marks = {}
    marker_group_count = 0
    for o in objects:
        if o["type_name"] != GROUP_TYPE_NAME:
            continue
        container = o["container"]
        nickname = _safe_string(container, "NickName")
        if not nickname:
            continue
        if "RH_IN" not in nickname and "RH_OUT" not in nickname:
            continue
        marker_group_count += 1
        id_count = _safe_int(container, "ID_Count") or 0
        for k in range(id_count):
            member_guid = _safe_guid(container, "ID", k)
            if member_guid:
                marks[member_guid] = nickname
    return marks, marker_group_count


def _build_adjacency(all_param_records):
    """source_guid -> list of {"component": nickname_or_name, "input"/"output": nickname_or_name}
    Returned as raw consumer records keyed by source guid; caller decides
    input/output key label based on context (kept generic here, resolved by
    callers of _consumers_of()).
    """
    consumers = {}
    for rec in all_param_records:
        for src in rec["sources"]:
            consumers.setdefault(src, []).append(rec)
    return consumers


def _consumer_label(rec):
    """Best-effort display name for a consuming param: prefer NickName, fall
    back to Name."""
    return rec.get("nickname") or rec.get("name") or "?"


def scan_gh(path) -> ScanResult:
    """Scan a .gh file at `path` and return candidate inputs/outputs.

    Raises:
        hoger.ghio.loader.GhioUnavailable: if GH_IO.dll is not available.
        Exception: if the file cannot be read as a GH archive (caller should
            treat this as a scan failure, e.g. HTTP 422).
    """
    archive_cls = get_archive_class()
    archive = archive_cls()
    if not archive.ReadFromFile(str(path)):
        raise ValueError(f"Could not read GH archive: {path}")

    root = archive.get_GetRootNode()
    definition = gh.find_chunk(root, "Definition")
    def_objects = gh.find_chunk(definition, "DefinitionObjects")

    objects = _top_level_objects(def_objects)
    object_count = len(objects)

    all_param_records = []
    for o in objects:
        all_param_records.extend(_find_all_param_records(o["chunk"]))

    consumers = _build_adjacency(all_param_records)
    marks, marker_group_count = _group_marks(objects)

    inputs = []
    outputs = []

    for o in objects:
        type_name = o["type_name"]
        if type_name == GROUP_TYPE_NAME:
            continue

        instance_guid = o["instance_guid"]
        if not instance_guid:
            continue

        container = o["container"]
        nickname = o["nickname"]
        has_upstream = len(o["sources"]) > 0
        downstream = consumers.get(instance_guid, [])
        has_downstream = len(downstream) > 0
        existing_mark = marks.get(instance_guid)

        feeds = [
            {"component": _consumer_label(rec), "input": _consumer_label(rec)}
            for rec in downstream
        ]

        if type_name in CANDIDATE_INPUT_TYPE_NAMES:
            if type_name == "Panel" and not has_downstream:
                # Panel with no downstream consumer -> output candidate, not input.
                fed_by = _fed_by_for(o, all_param_records)
                outputs.append(
                    OutputCandidate(
                        instance_guid=instance_guid,
                        object_type=type_name,
                        nickname=nickname,
                        fed_by=fed_by,
                        existing_mark=existing_mark,
                    )
                )
                continue

            current_value, minimum, maximum = _extract_values(type_name, container)
            inputs.append(
                InputCandidate(
                    instance_guid=instance_guid,
                    object_type=type_name,
                    nickname=nickname,
                    current_value=current_value,
                    minimum=minimum,
                    maximum=maximum,
                    feeds=feeds,
                    existing_mark=existing_mark,
                )
            )
        else:
            # Non-slider/toggle/panel/valuelist top-level object (a component,
            # or a dangling param). Only relevant as a scan candidate if it
            # looks like a dangling input (has downstream, no upstream) or a
            # dangling output (has upstream feeding it from elsewhere, no
            # downstream) at the TOP level -- most top-level "objects" here
            # are actual components (with their own nested input params
            # already captured via the recursive scan), so we only treat
            # true leaf params (no nested Object/Container-less shape) as
            # candidates. We detect "leaf param-like" by requiring the
            # object to expose SourceCount directly on its own container
            # (already true for all objects here) AND have no further
            # nested "Object" sub-chunks (i.e. it isn't a compound component).
            if _looks_like_component(o):
                continue
            if has_downstream and not has_upstream:
                current_value, minimum, maximum = _extract_values(type_name, container)
                inputs.append(
                    InputCandidate(
                        instance_guid=instance_guid,
                        object_type=type_name,
                        nickname=nickname,
                        current_value=current_value,
                        minimum=minimum,
                        maximum=maximum,
                        feeds=feeds,
                        existing_mark=existing_mark,
                    )
                )
            elif has_upstream and not has_downstream:
                fed_by = _fed_by_for(o, all_param_records)
                outputs.append(
                    OutputCandidate(
                        instance_guid=instance_guid,
                        object_type=type_name,
                        nickname=nickname,
                        fed_by=fed_by,
                        existing_mark=existing_mark,
                    )
                )

    return ScanResult(
        inputs=inputs,
        outputs=outputs,
        already_marked_count=marker_group_count,
        object_count=object_count,
    )


def _looks_like_component(o) -> bool:
    """Heuristic: an object is a "component" (as opposed to a bare param)
    if its Object chunk contains nested chunks other than Container, i.e.
    it isn't just {GUID, Name, Container{...}}. Bare params (Slider/Toggle/
    Panel/ValueList) only ever have a single "Container" child under Object.
    """
    children = gh.chunks_of(o["chunk"])
    child_names = {c.Name for c in children}
    return len(child_names - {"Container"}) > 0


def _fed_by_for(o, all_param_records):
    """For an object with upstream Source(s), resolve the upstream component
    display names by finding, among all_param_records, any record whose
    InstanceGuid equals one of this object's sources -- no, actually we need
    the upstream side's own identity, not its consumers. Since Source[] on
    `o` already gives the upstream InstanceGuids directly, we look up display
    names by matching those guids against all_param_records' own identity
    (a record whose instance_guid equals the source), falling back to the
    guid itself if not found.
    """
    by_guid = {r["instance_guid"]: r for r in all_param_records if r["instance_guid"]}
    fed_by = []
    for src_guid in o["sources"]:
        rec = by_guid.get(src_guid)
        label = _consumer_label(rec) if rec else src_guid
        fed_by.append({"component": label, "output": label})
    return fed_by


def _extract_values(type_name, container):
    """Return (current_value, minimum, maximum) for a candidate input object."""
    if type_name == "Number Slider":
        slider_chunk = gh.find_chunk(container, _SLIDER_SUBCHUNK)
        if slider_chunk is not None:
            value = _safe_double(slider_chunk, "Value")
            minimum = _safe_double(slider_chunk, "Min")
            maximum = _safe_double(slider_chunk, "Max")
            return (
                str(value) if value is not None else None,
                minimum,
                maximum,
            )
        return None, None, None

    if type_name == "Boolean Toggle":
        value = _safe_bool(container, "ToggleValue")
        return (str(value) if value is not None else None, None, None)

    if type_name == "Panel":
        value = _safe_string(container, "UserText")
        return value, None, None

    if type_name == "Value List":
        # Value List internal layout not yet confirmed against a real file
        # (plan section 0, item 8) -- best-effort only.
        value = _safe_string(container, "UserText")
        return value, None, None

    return None, None, None
