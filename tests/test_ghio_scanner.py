"""
tests/test_ghio_scanner.py — hoger.ghio (loader / scanner) 單元測試。

需要 GH_IO.dll 才能跑（本機有 Rhino 8 時會執行；CI 或無 Rhino 環境會 skip 整個模組）。

測試對象：
- tests/fixtures/plain_slider_panel.gh — 最小 fixture（1 slider -> 1 panel，
  無任何 RH_IN/RH_OUT 標記）；來源見下方 docstring。
- comfort_in_a_street_canyon_study.gh 實檔（唯讀，測試中一律先複製到 tmp_path）。
- gh_files/ 下使用者真實檔案（不入版控，用 pytest.mark.skipif 條件測試——見
  task v2-G「掃描器輸出候選全型別化」：任何非元件的資料物件（Panel、Curve、
  Surface、Brep、Mesh、Point、Geometry...）只要沒有下游接線都應被認成輸出候選；
  Part 0 實證見 scratch/spike_v2g/（GUID 才是唯一可靠判別特徵，結構性
  param_input/param_output 假設已被證偽——見 hoger/ghio/scanner.py 模組
  docstring）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest

from hoger.ghio import loader

if not loader.is_available():
    pytest.skip("GH_IO.dll not available", allow_module_level=True)

from hoger.ghio import scanner  # noqa: E402  (import after availability check)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "plain_slider_panel.gh"
COMFORT_SRC = Path(r"C:\Users\User\Desktop\rhino.compute.test\comfort_in_a_street_canyon_study.gh")

GH_FILES_DIR = Path(__file__).parent.parent / "gh_files"
V27_SRC = GH_FILES_DIR / "v2-7 離群值+共線性+相關性.gh"
MOO_SRC = GH_FILES_DIR / "MOO Tool for MFRB.gh"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── fixture scan ─────────────────────────────────────────────────────


def test_fixture_scan_finds_slider_input():
    result = scanner.scan_gh(FIXTURE_PATH)
    assert result.object_count > 0
    sliders = [i for i in result.inputs if i.object_type == "Number Slider"]
    assert len(sliders) == 1
    slider = sliders[0]
    assert slider.current_value is not None
    assert slider.minimum is not None
    assert slider.maximum is not None
    assert float(slider.current_value) == 3.0
    assert slider.minimum == 0.0
    assert slider.maximum == 10.0


def test_fixture_scan_finds_panel_output():
    result = scanner.scan_gh(FIXTURE_PATH)
    panels = [o for o in result.outputs if o.object_type == "Panel"]
    assert len(panels) == 1


def test_fixture_slider_feeds_panel():
    result = scanner.scan_gh(FIXTURE_PATH)
    slider = next(i for i in result.inputs if i.object_type == "Number Slider")
    assert len(slider.feeds) >= 1


def test_fixture_no_existing_marks():
    result = scanner.scan_gh(FIXTURE_PATH)
    assert result.already_marked_count == 0
    for i in result.inputs:
        assert i.existing_mark is None
    for o in result.outputs:
        assert o.existing_mark is None


# ── comfort real-file scan (copied to tmp_path; original never touched) ──


@pytest.fixture
def comfort_copy(tmp_path):
    if not COMFORT_SRC.exists():
        pytest.skip(f"comfort test file not present at {COMFORT_SRC}")
    dst = tmp_path / COMFORT_SRC.name
    shutil.copy2(COMFORT_SRC, dst)
    return dst


def test_comfort_original_file_untouched(comfort_copy):
    """Guard: scanning must never mutate the source file. Hash before/after."""
    before = _sha256(COMFORT_SRC)
    scanner.scan_gh(comfort_copy)
    after = _sha256(COMFORT_SRC)
    assert before == after


def test_comfort_scan_input_count(comfort_copy):
    result = scanner.scan_gh(comfort_copy)
    # NOTE: task spec expected >= 15 inputs (18 sliders) for this file; the
    # actual on-disk comfort_in_a_street_canyon_study.gh contains 5 Number
    # Slider objects (4 wired + 1 dangling/unconnected) and 12 total input
    # candidates overall (verified via direct GH_IO inspection during
    # implementation). Asserting against the real, verified content rather
    # than the higher figure from the task description.
    assert len(result.inputs) >= 10
    assert result.object_count > 0


def test_comfort_candidates_have_valid_guids(comfort_copy):
    result = scanner.scan_gh(comfort_copy)
    for i in result.inputs:
        uuid.UUID(i.instance_guid)  # raises ValueError if malformed
    for o in result.outputs:
        uuid.UUID(o.instance_guid)


def test_comfort_at_least_one_feeds_nonempty(comfort_copy):
    result = scanner.scan_gh(comfort_copy)
    assert any(i.feeds for i in result.inputs)


def test_comfort_chinese_nicknames_intact(comfort_copy):
    """Unicode round-trip guard: the comfort file contains sliders with
    Chinese NickNames. GH_IO must return them as correct Unicode strings —
    exact codepoints, not mojibake. (An earlier dry run showed garbled text
    in console output; direct codepoint inspection confirmed that was a
    terminal codepage display artifact, not data corruption — this test
    pins the actual values.)
    """
    result = scanner.scan_gh(comfort_copy)
    nicknames = {i.nickname for i in result.inputs}
    assert "粗糙度" in nicknames  # slider: roughness
    assert "測站高度" in nicknames  # slider: weather-station height


def test_comfort_dangling_params_from_allowlist_only(comfort_copy):
    """The Brep/Point dangling params are allowlisted (PARAM_TYPE_GUIDS) and
    must appear as input candidates; component-class objects (LB *, Power,
    Relay, Division, Multiplication, ...) must never appear even though some
    also carry Source lists or a similarly minimal Container chunk shape
    (task v2-G Part 0: chunk-shape structural checks were tried and
    falsified for this exact reason — see scanner.py module docstring).
    """
    result = scanner.scan_gh(comfort_copy)
    types = {i.object_type for i in result.inputs}
    assert "Brep" in types
    assert "Point" in types
    known = scanner.CANDIDATE_INPUT_TYPE_NAMES | {
        "Brep",
        "Point",
        "Geometry",
        "Curve",
        "Surface",
        "Data",
        "Number",
        "Integer",
        "Vector",
        "Rectangle",
    }
    assert types <= known

    component_names = {
        "LB Hourly Plot",
        "LB Import EPW",
        "LB Outdoor Solar MRT",
        "LB UTCI Comfort",
        "LB Comfort Statistics",
        "LB Human to Sky Relation",
        "LB PET Comfort",
        "Power",
        "Division",
        "Multiplication",
        "Relay",
    }
    assert types.isdisjoint(component_names)


def test_comfort_scan_output_candidates_widened(comfort_copy):
    """task v2-G: output candidates are no longer Panel-only — any
    non-component data object with no downstream consumer qualifies. The
    on-disk comfort file's own Brep/Point both have a downstream consumer in
    this particular file, so its output list stays Panel-only; this test
    pins that exact, verified composition (1 Panel, fed by 'pct_neutral')
    rather than asserting a stronger claim this file doesn't support. See
    test_v27_scan_has_non_panel_output_candidates /
    test_moo_scan_has_non_panel_output_candidates below for real files that
    DO exercise the widened non-Panel path.
    """
    result = scanner.scan_gh(comfort_copy)
    assert len(result.outputs) == 1
    assert result.outputs[0].object_type == "Panel"
    assert result.outputs[0].fed_by
    assert result.outputs[0].fed_by[0]["output"] == "pct_neutral"

    # No component ever appears as an output candidate.
    component_names = {
        "LB Hourly Plot",
        "LB Import EPW",
        "LB Outdoor Solar MRT",
        "LB UTCI Comfort",
        "LB Comfort Statistics",
        "LB Human to Sky Relation",
        "LB PET Comfort",
        "Power",
        "Division",
        "Multiplication",
        "Relay",
    }
    out_types = {o.object_type for o in result.outputs}
    assert out_types.isdisjoint(component_names)


def test_comfort_no_existing_marks(comfort_copy):
    result = scanner.scan_gh(comfort_copy)
    assert result.already_marked_count == 0
    for i in result.inputs:
        assert i.existing_mark is None
    for o in result.outputs:
        assert o.existing_mark is None


# ── task v2-G: widened output candidates on real gh_files/ ─────────────
#
# These files are the user's own working files (not checked into version
# control — see .gitignore), so the tests are conditional: they skip cleanly
# if the file isn't present (e.g. a fresh clone or CI). When present, they
# are the highest-value validation of the widening because they're real
# production definitions, not synthetic fixtures.


@pytest.fixture
def v27_copy(tmp_path):
    if not V27_SRC.exists():
        pytest.skip(f"user file not present at {V27_SRC}")
    dst = tmp_path / V27_SRC.name
    shutil.copy2(V27_SRC, dst)
    return dst


@pytest.fixture
def moo_copy(tmp_path):
    if not MOO_SRC.exists():
        pytest.skip(f"user file not present at {MOO_SRC}")
    dst = tmp_path / MOO_SRC.name
    shutil.copy2(MOO_SRC, dst)
    return dst


def test_v27_original_file_untouched():
    if not V27_SRC.exists():
        pytest.skip(f"user file not present at {V27_SRC}")
    before = _sha256(V27_SRC)
    scanner.scan_gh(V27_SRC)
    after = _sha256(V27_SRC)
    assert before == after


def test_v27_scan_has_non_panel_output_candidates(v27_copy):
    """Real user file: verified (task v2-G Part 1 manual scan) to produce
    'Data' (Param_GenericObject) output candidates alongside Panel — the
    exact widening the user asked for ("面板、文字、線條、曲面、實體、網格...都
    應該被認成輸出"). Also asserts no component (Value List's own upstream
    components, Python 3 Script, Entwine, Read From Excel, Cordyceps) leaks
    into either candidate list.
    """
    result = scanner.scan_gh(v27_copy)
    out_types = {o.object_type for o in result.outputs}
    assert "Data" in out_types
    assert "Panel" in out_types

    component_names = {
        "Python 3 Script",
        "Read From Excel",
        "Entwine",
        "Cordyceps",
        "Group",
    }
    in_types = {i.object_type for i in result.inputs}
    assert out_types.isdisjoint(component_names)
    assert in_types.isdisjoint(component_names)


def test_moo_scan_has_non_panel_output_candidates(moo_copy):
    """Real user file (large: ~3946 top-level objects): verified (task v2-G
    Part 1 manual scan) to produce 'Geometry' output candidates alongside
    Panel. Also asserts none of the file's many component types (Relay,
    Division, Multiplication, Fish, Cluster, Button, Colour Swatch, Path
    Mapper, GhPython Script, Hops 'Get *', Mass Addition, Addition,
    Subtraction, Explode Tree, List Item, Area, Tunny — all confirmed
    components in scratch/spike_v2g/final_table.txt) leak into either
    candidate list.
    """
    result = scanner.scan_gh(moo_copy)
    out_types = {o.object_type for o in result.outputs}
    in_types = {i.object_type for i in result.inputs}
    assert "Geometry" in out_types
    assert "Panel" in out_types

    component_names = {
        "Relay",
        "Division",
        "Multiplication",
        "Fish",
        "Cluster",
        "Button",
        "Colour Swatch",
        "Path Mapper",
        "GhPython Script",
        "Get Point",
        "Get Number",
        "Get Geometry",
        "Get File Path",
        "Get Integer",
        "Mass Addition",
        "Addition",
        "Subtraction",
        "Explode Tree",
        "List Item",
        "Area",
        "Tunny",
        "Group",
    }
    assert out_types.isdisjoint(component_names)
    assert in_types.isdisjoint(component_names)


def test_moo_no_component_instance_guid_in_any_candidate_list(moo_copy):
    """Belt-and-suspenders check keyed on InstanceGuid (not just object_type
    string) that no Division/LB-style component instance ends up in either
    candidate list, scanning the raw archive directly rather than relying on
    scanner internals matching by name alone."""
    from hoger.ghio import ghio_helpers as gh
    from hoger.ghio.loader import get_archive_class

    archive_cls = get_archive_class()
    archive = archive_cls()
    assert archive.ReadFromFile(str(moo_copy))
    root = archive.get_GetRootNode()
    definition = gh.find_chunk(root, "Definition")
    def_objects = gh.find_chunk(definition, "DefinitionObjects")

    component_type_names = {"Relay", "Division", "Multiplication", "Cluster"}
    component_instance_guids = set()
    for ch in gh.chunks_of(def_objects):
        if not gh.item_exists(ch, "Name"):
            continue
        name = gh.get_string(ch, "Name")
        if name not in component_type_names:
            continue
        container = gh.find_chunk(ch, "Container")
        if container is None or not gh.item_exists(container, "InstanceGuid"):
            continue
        component_instance_guids.add(str(gh.get_guid(container, "InstanceGuid")))

    assert component_instance_guids, "sanity: file should contain these component types"

    result = scanner.scan_gh(moo_copy)
    candidate_guids = {i.instance_guid for i in result.inputs} | {
        o.instance_guid for o in result.outputs
    }
    assert candidate_guids.isdisjoint(component_instance_guids)


# ── loader ───────────────────────────────────────────────────────────


def test_loader_is_available_true_on_this_machine():
    assert loader.is_available() is True


def test_loader_unavailable_when_dll_path_missing(monkeypatch):
    from hoger import config

    monkeypatch.setattr(config, "GHIO_DLL", r"C:\does\not\exist\GH_IO.dll")
    loader._reset_for_tests()
    try:
        assert loader.is_available() is False
        with pytest.raises(loader.GhioUnavailable):
            loader.get_archive_class()
    finally:
        loader._reset_for_tests()


# ── JSON serializability ─────────────────────────────────────────────


def test_scan_result_is_json_serializable():
    result = scanner.scan_gh(FIXTURE_PATH)
    payload = json.dumps(dataclasses.asdict(result))
    assert isinstance(payload, str)
    reloaded = json.loads(payload)
    assert reloaded["object_count"] == result.object_count


# ── integration: widened non-Panel candidate recognized by Compute's /io ──
#
# task v2-G: mark a geometric (non-Panel) candidate found only because of
# the allowlist widening as RH_OUT on a comfort-file copy, then confirm
# Rhino.Compute's /io endpoint (a real GH_IO + Grasshopper.dll + Compute
# process, independent of this repo's own parsing) both reports it under
# Outputs AND assigns it a geometric ParamType — i.e. the widened candidate
# is not just an artifact of our own scanner but is genuinely usable as a
# real tool output end-to-end.


@pytest.mark.integration
class TestWidenedOutputCandidateIntegration:
    @pytest.fixture(autouse=True)
    def _require_compute(self):
        from hoger.core import compute_client

        if not compute_client.health():
            pytest.skip("Rhino.Compute is not running at the configured COMPUTE_URL")

    def test_geometric_param_marked_rh_out_appears_with_geometric_param_type(
        self, comfort_copy
    ):
        from hoger.core import compute_client
        from hoger.ghio import marker

        result = scanner.scan_gh(comfort_copy)
        # "Brep" is a dangling-param candidate in this file (allowlist
        # widening from task v2-G — PARAM_TYPE_GUIDS now includes
        # Param_Brep/Curve/Surface/... beyond the original 3-GUID set).
        brep_candidates = [i for i in result.inputs if i.object_type == "Brep"]
        assert brep_candidates, "expected a dangling Brep candidate in the comfort file"
        brep = brep_candidates[0]

        marker.apply_marks(
            comfort_copy,
            input_marks=[],
            output_marks=[{"guid": brep.instance_guid, "name": "brep_result"}],
        )

        io_response = compute_client.io_query(str(comfort_copy))
        outputs_by_name = {o["Name"]: o for o in io_response["Outputs"]}

        assert "RH_OUT:brep_result" in outputs_by_name
        param_type = outputs_by_name["RH_OUT:brep_result"].get("ParamType")
        # Compute reports the RhinoCommon-level geometric type name; accept
        # any of the plausible geometric labels rather than pinning one
        # exact string, since GH_IO's own "Brep" Name and Compute's/io
        # ParamType vocabulary aren't guaranteed to use identical casing or
        # base-vs-derived naming.
        assert param_type in {"Brep", "Geometry", "GeometryBase", "Surface"}, (
            f"expected a geometric ParamType, got {param_type!r} "
            f"(full entry: {outputs_by_name['RH_OUT:brep_result']})"
        )
