"""
hoger.ghio.scanner — recursively scan a .gh file for input/output candidates.

This is a formalization of the prototype in
``scratch/spike_v2/v1_enumerate_graph.py`` (validated against a real
36-object .gh file); see docs/superpowers/plans/2026-07-04-hoger-v2-auto-convert.md
section 0 for the ground-truth facts this module relies on.

Candidate rules (plan section 2, widened by task v2-G — see
scratch/spike_v2g/ for the Part 0 evidence this widening is based on):
- Input candidates: Number Slider (current_value/minimum/maximum), Boolean
  Toggle (current_value), Panel (only if it has at least one downstream
  consumer), Value List, and top-level "dangling" data objects (objects with
  a downstream consumer but no upstream Source — object's own Name is used
  as object_type since we don't know its concrete param kind from chunk data
  alone).
- Output candidates: ANY non-component top-level data object with no
  downstream consumer at all — Panel, or any bare parameter (Curve, Surface,
  Brep, Mesh, Point, Geometry, Text, ...); fed_by records the upstream
  component(s) feeding them. Number Slider / Boolean Toggle / Value List are
  excluded even when they have no downstream (a dangling slider as "output"
  is meaningless — see the comment above PARAM_TYPE_GUIDS).
- Wiring (feeds / fed_by) requires a full recursive scan of the
  DefinitionObjects chunk tree, because Source[] lists for params nested
  inside components (e.g. a component's own input params) are buried in
  sub-chunks at varying depths.
- existing_mark: an object is "already marked" if its InstanceGuid appears
  in a Group object's `ID` list AND that Group's NickName contains the
  (case-sensitive) substring "RH_IN" or "RH_OUT" anywhere. The *entire*
  NickName (not just the marker substring) is reported as existing_mark.

--- Part 0 finding (task v2-G): the "component vs data object" structural
hypothesis is FALSIFIED ---

The original hypothesis (see git history) was that components (objects with
input/output plugs, e.g. Division, LB Outdoor Solar MRT) could be told apart
from bare data-carrying params (Param_Brep, Param_Point, ...) by a
structural fingerprint: components' Container chunk has "param_input"/
"param_output" sub-chunks, bare params don't.

This was tested against 9 real .gh files (see scratch/spike_v2g/, scripts
classify.py / final_table.py / collect_guids.py; source files copied to
scratch/spike_v2g/src/ with md5 verified unchanged before and after) totaling
144 distinct top-level-object type GUIDs. Result: FALSIFIED in both
directions —
  * Many genuine components have NO param_input/param_output chunks at all:
    Relay, Multiplication/Addition/Subtraction, all "LB *"/"HB *" (Ladybug/
    Honeybee, which are GhPython-Script-derived and store I/O as
    "ParameterData" instead), Hops "Get Point"/"Get Number"/"Get Geometry"/
    "Get Integer"/"Get File Path", Cluster, GhPython/Python 3 Script, Path
    Mapper, Loop Start/End, Explode Tree, List Item, Merge, Stream Filter,
    Entwine, Expression, Format, Button, Colour Picker/Swatch, Fish, Text
    Entity, Tunny. All of these have a Container sub-chunk shape
    indistinguishable from a bare param (just "Attributes", sometimes plus
    "ParameterData" or "ListItem" — shapes ALSO seen on genuine bare params).
  * One GUID (874eebe7-835b-4f4f-9811-97e031c41597, a "Group"-named cluster
    I/O proxy object, distinct from the normal Group GUID
    c552a431-af5b-46a9-a8a4-0fcbc27ef596) DOES carry param_input/
    param_output despite not being a normal top-level data/component object.

Conclusion: chunk shape alone cannot discriminate; the only reliable
per-session-verified signal remains the object's *type* GUID (the Object
chunk's own "GUID" item). This module therefore keeps (and widens) the GUID
allowlist approach rather than replacing it with a structural check. Most
GUIDs in PARAM_TYPE_GUIDS below were confirmed, in at least one real file, to
be a bare Grasshopper parameter (no SolveInstance logic, no input/output
plugs) via manual cross-reference of scratch/spike_v2g output against the
known GH component catalog; a later batch (task v2-H — Mesh, Line, Plane,
String, Circle, Box, Boolean) was instead sourced from a trusted third-party
Grasshopper parameter reference and cross-validated against the file-confirmed
entries (see the comment above those entries in PARAM_TYPE_GUIDS for detail)
since no sample file available at the time contained a bare instance of them.

As of task v2-H, PARAM_TYPE_GUIDS covers: Brep, Point, Geometry, Curve,
Surface, Data (GenericObject), Number, Integer, Vector, Rectangle, Mesh,
Line, Plane, String, Circle, Box, and Boolean. Types not yet covered
(Circular Arc, Colour, Time, Complex, and any other standard Param_* not
listed here) can be added the same way when encountered in a real file or
independently cross-validated against a trusted source, per the extension
recipe documented above PARAM_TYPE_GUIDS.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hoger.ghio import ghio_helpers as gh
from hoger.ghio.loader import get_archive_class

CANDIDATE_INPUT_TYPE_NAMES = {"Number Slider", "Boolean Toggle", "Panel", "Value List"}
GROUP_TYPE_NAME = "Group"

# Number Slider / Boolean Toggle / Value List are terminal input widgets: a
# dangling one (no downstream) is not a meaningful *output* candidate (there
# is nothing being "produced" — it's just an unused control). They never
# reach the output-candidate branch below because CANDIDATE_INPUT_TYPE_NAMES
# routes them straight to the input branch regardless of wiring (see the
# main scan loop) — only "Panel" among CANDIDATE_INPUT_TYPE_NAMES falls
# through to output when it has no downstream. This comment documents that
# exclusion explicitly since task v2-G asked for it by name.

# Allowlist of *type* GUIDs (the Object chunk's own "GUID" item, not the
# per-instance InstanceGuid) for bare Grasshopper parameter classes — objects
# that carry/display data but have no SolveInstance computation and no
# input/output plugs of their own (Param_Brep, Param_Curve, Param_Mesh, ...).
#
# Per the Part 0 finding above, this is the ONLY reliable way (short of
# loading the full Grasshopper.dll + RhinoCommon.dll + Rhino UI runtime,
# which this headless GH_IO-only scanner deliberately does not depend on) to
# tell a bare param apart from a component that happens to have a similarly
# minimal Container chunk shape (e.g. Relay, Get Point, Button, Fish).
#
# Any top-level object whose Name is not in CANDIDATE_INPUT_TYPE_NAMES and
# whose type GUID is NOT in this set is treated as a component and skipped
# as a standalone candidate (its own nested input params are still captured
# via the recursive Source scan, so wiring information is not lost) — dangling
# input/output candidates deliberately favor missing an object over
# mislabeling a component as one.
#
# GUIDs verified (file-confirmed) against real files during task v2-G
# (scratch/spike_v2g/src/*.gh — comfort_in_a_street_canyon_study.gh,
# comfort_in_a_street_canyon_study_hops.gh, hops test.gh, MOO Tool for
# MFRB.gh, v2-7 離群值+共線性+相關性.gh, 樓梯教學.gh, 量體長柱、女兒牆、樓板 V2.gh):
PARAM_TYPE_GUIDS = {
    "919e146f-30ae-4aae-be34-4d72f555e7da",  # Param_Brep ("Brep")
    "fbac3e32-f100-4292-8692-77240a42fd1a",  # Param_Point ("Point")
    "ac2bc2cb-70fb-4dd5-9c78-7e1ea97fe278",  # Param_Geometry ("Geometry")
    "d5967b9f-e8ee-436b-a8ad-29fdcecf32d5",  # Param_Curve ("Curve")
    "deaf8653-5528-4286-807c-3de8b8dad781",  # Param_Surface ("Surface")
    "8ec86459-bf01-4409-baee-174d0d2b13d0",  # Param_GenericObject ("Data")
    "3e8ca6be-fda8-4aaf-b5c0-3c54c8bb7312",  # Param_Number ("Number")
    "2e3ab970-8545-46bb-836c-1c11e5610bce",  # Param_Integer ("Integer")
    "16ef3e75-e315-4899-b531-d3166b42dac9",  # Param_Vector ("Vector")
    # NOTE: "Rectangle" appears under TWO different type GUIDs across the
    # sample files (abf9c670-... and d93100b6-...) — only one of them
    # (abf9c670) was confirmed to be the bare Param_Rectangle (no
    # param_input/output, PersistentData present); d93100b6 was confirmed to
    # be the "Rectangle" *component* (has param_input/output) and is
    # correctly excluded. Same ambiguity exists for "Area" (component GUID
    # 2e205f24 has param_input/output and is excluded; a second GUID
    # 86b28a7e also named "Area" has no param_input/output but DOES have a
    # "ParameterData" sub-chunk — the same component-side marker seen on
    # Multiplication/Addition/Subtraction — so it too is a component, not a
    # bare param, and is correctly left OUT of this allowlist).
    "abf9c670-5462-4cd8-acb3-f1ab0256dbf3",  # Param_Rectangle ("Rectangle")
    # --- Added task v2-H: web-sourced, cross-validated against Brep/Point/
    # Geometry (see below) --- no sample .gh file in this session contained a
    # bare instance of these, so they could not be file-confirmed directly;
    # instead they were sourced from https://rhino-help.com/help/GrashopperHELP/
    # (a mirrored/localized copy of GrasshopperDocs' per-parameter help pages,
    # URL pattern "Params.<Category>.<GUID>.htm"). This source was trusted
    # only after it reproduced, byte-for-byte, EVERY one of the 8 checkable
    # pre-existing entries above via live page fetch: Brep
    # (919e146f-30ae-4aae-be34-4d72f555e7da), Point
    # (fbac3e32-f100-4292-8692-77240a42fd1a), Geometry
    # (ac2bc2cb-70fb-4dd5-9c78-7e1ea97fe278), Curve, Surface, Vector,
    # Rectangle (the correct abf9c670 param GUID, not the d93100b6 component
    # GUID), Integer, and Number. Each new GUID below was then independently
    # fetched and its page title confirmed to match the intended type name.
    "1e936df3-0eea-4246-8549-514cb8862b7a",  # Param_Mesh ("Mesh") - web-sourced, cross-validated
    "8529dbdf-9b6f-42e9-8e1f-c7a2bde56a70",  # Param_Line ("Line") - web-sourced, cross-validated
    "4f8984c4-7c7a-4d69-b0a2-183cbb330d20",  # Param_Plane ("Plane") - web-sourced, cross-validated
    "3ede854e-c753-40eb-84cb-b48008f14fd4",  # Param_String ("String"/text) - web-sourced, cross-validated
    "d1028c72-ff86-4057-9eb0-36c687a4d98c",  # Param_Circle ("Circle") - web-sourced, cross-validated
    "c9482db6-bea9-448d-98ff-fed6d69a8efc",  # Param_Box ("Box") - web-sourced, cross-validated
    "cb95db89-6165-43b6-9c41-5702bc5bf137",  # Param_Boolean ("Boolean") - web-sourced, cross-validated
}
#
# Not added despite being on the initial web-sourced candidate list: Param_Arc
# ("Circular Arc"), Param_Colour ("Colour"), Param_Time ("Time"), and
# Param_Complex ("Complex"). Candidate GUIDs for these were found in the same
# rhino-help.com index page, but the source became unreachable (connection
# refused) mid-session before their individual pages could be independently
# fetched and title-confirmed, so per this task's "any type that can't be
# verified is left out" rule they were not added. Extend PARAM_TYPE_GUIDS the
# same way once confirmed — either against a real file (open a .gh with the
# param, read its Object chunk's "GUID" item, verify no param_input/
# param_output sub-chunk on Container) or against a trusted source
# cross-validated the same way as above.
#
# Untried/uncovered types beyond that list can be added the same way when
# encountered in a real file: read the Object chunk's "GUID" item, verify no
# param_input/param_output sub-chunk on Container, add the GUID here with the
# verifying filename (or verifying source) in the comment.
#
# Used both for input-side dangling-param detection and output-side
# non-Panel data-object detection (see the main scan loop in scan_gh()).

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
    # 頂層物件中「非候選、非已知資料物件」的 Name -> 出現次數，即被主掃描迴圈
    # 判定為元件（component）而跳過候選判定的那些物件（見 scan_gh 主迴圈的
    # PARAM_TYPE_GUIDS 分支）。這是 hoger.core.describe 產生工具描述的原料
    # ——元件名稱前綴（"LB ", "HB ", "Karamba"...）揭示這個工具用了哪些已知
    # Grasshopper 生態系，藉此推斷用途類別。task v3-A 新增欄位；既有消費端
    # （/api/scan 回應）用 dataclasses.asdict() 印出全部欄位，多一個 key
    # 不影響既有讀取邏輯（向後相容）。
    component_inventory: dict = field(default_factory=dict)


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
        type_guid = _safe_guid(ch, "GUID")  # component *class* GUID
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
                "type_guid": type_guid,
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
    if definition is None:
        raise ValueError(f"Not a valid GH archive (no Definition chunk): {path}")
    def_objects = gh.find_chunk(definition, "DefinitionObjects")
    if def_objects is None:
        raise ValueError(
            f"Not a valid GH archive (no DefinitionObjects chunk): {path}"
        )

    objects = _top_level_objects(def_objects)
    object_count = len(objects)

    all_param_records = []
    for o in objects:
        all_param_records.extend(_find_all_param_records(o["chunk"]))

    consumers = _build_adjacency(all_param_records)
    marks, marker_group_count = _group_marks(objects)

    inputs = []
    outputs = []
    component_inventory: dict = {}

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
            # Non-slider/toggle/panel/valuelist top-level object. Could be a
            # bare param (Param_Brep, Param_Curve, Param_Point, ...) or a
            # full component (Division, LB Outdoor Solar MRT, Relay, ...).
            # Only objects whose *type* GUID is in the verified allowlist are
            # considered candidates; everything else is treated as a
            # component and skipped (its own nested input params are still
            # captured by the recursive Source scan, so wiring info isn't
            # lost). See PARAM_TYPE_GUIDS docstring for the Part 0 evidence
            # behind this (chunk-shape structural checks were tried and
            # falsified — GUID allowlist is the only reliable signal found).
            if o["type_guid"] not in PARAM_TYPE_GUIDS:
                # Treated as a component (not a bare param candidate) --
                # tally it into component_inventory by Name (task v3-A: this
                # is the raw material hoger.core.describe uses to say things
                # like "this file uses Ladybug (LB *)").
                if type_name:
                    component_inventory[type_name] = component_inventory.get(type_name, 0) + 1
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
            elif not has_downstream:
                # No downstream consumer at all -> output candidate,
                # regardless of whether it has an upstream Source. This
                # mirrors the Panel rule above: any data object (Curve,
                # Surface, Brep, Mesh, Point, Geometry, ...) that nothing
                # reads from is a candidate output, whether it's fed by a
                # component (fed_by populated) or simply sitting unconnected
                # on the canvas (fed_by empty) — widened per task v2-G to
                # recognize any data object, not just wired-up ones.
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
        component_inventory=component_inventory,
    )


def _fed_by_for(o, all_param_records):
    """Resolve display names for the upstream objects feeding `o`.

    `o["sources"]` already holds the InstanceGuids of the upstream params.
    Each source guid is looked up in all_param_records (keyed by each
    record's own instance_guid) to obtain a display name (NickName, falling
    back to Name); if no record matches, the raw guid string is used as the
    label. Returns [{"component": <label>, "output": <label>}, ...].
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
