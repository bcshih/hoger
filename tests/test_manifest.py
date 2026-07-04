"""
tests/test_manifest.py — hoger.core.manifest 的單元測試。

manifest.py 負責：
1. manifest_from_io(): Rhino.Compute /io 回應 dict -> ToolManifest（防禦性解析，
   缺欄位不 crash）。
2. to_mcp_tool(): ToolManifest -> MCP Tool Schema dict（複用 type_mapping.to_json_schema）。

測試資料來源：tests/fixtures/io_response_sample.json（手寫樣本，欄位可能與真實
Rhino.Compute 回應有出入，因此解析邏輯必須防禦性）。
"""

import json
import re
from pathlib import Path

import pytest

from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest, manifest_from_io, to_mcp_tool

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "io_response_sample.json"


@pytest.fixture
def io_response():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def manifest(io_response):
    return manifest_from_io("Radiation Study_hops.gh", io_response)


# ── manifest_from_io: basic parsing ─────────────────────────────────


def test_basic_parsing_input_count(manifest):
    assert len(manifest.inputs) == 4


def test_underscore_names_preserved(manifest):
    names = {i.param_name for i in manifest.inputs}
    assert "_geometry" in names
    assert "context_" in names


def test_grid_size_fields(manifest):
    grid = next(i for i in manifest.inputs if i.param_name == "_grid_size")
    assert grid.default == 1.0
    assert grid.minimum == 0.1
    assert grid.maximum == 50.0
    assert grid.kind == "number"
    assert grid.required is False  # has default


def test_context_at_least_zero_means_optional(manifest):
    context = next(i for i in manifest.inputs if i.param_name == "context_")
    assert context.at_least == 0
    assert context.required is False


def test_geometry_at_least_one_no_default_required(manifest):
    geom = next(i for i in manifest.inputs if i.param_name == "_geometry")
    assert geom.at_least == 1
    assert geom.default is None
    assert geom.required is True
    assert geom.kind == "geometry"


def test_outputs_mesh(manifest):
    mesh = next(o for o in manifest.outputs if o.param_name == "Mesh")
    assert mesh.kind == "geometry"


def test_outputs_total(manifest):
    total = next(o for o in manifest.outputs if o.param_name == "total")
    assert total.kind == "number"


def test_outputs_no_rh_out_prefix_remaining(manifest):
    for o in manifest.outputs:
        assert not o.param_name.startswith("RH_OUT:")


# ── id generation (kebab-case from filename) ────────────────────────


@pytest.mark.parametrize(
    "gh_path,expected_id",
    [
        ("Radiation Study_hops.gh", "radiation-study-hops"),
        ("comfort_分析 v2.gh", "comfort-v2"),
        ("__a  b__.gh", "a-b"),
        ("simple.gh", "simple"),
        ("Already-Kebab.gh", "already-kebab"),
        ("multiple___underscores.gh", "multiple-underscores"),
        ("C:/some/dir/Nested Tool.gh", "nested-tool"),
    ],
)
def test_id_generation(gh_path, expected_id):
    m = manifest_from_io(gh_path, {})
    assert m.id == expected_id


def test_display_name_keeps_original_stem():
    m = manifest_from_io("Radiation Study_hops.gh", {})
    assert m.display_name == "Radiation Study_hops"


# ── id generation: non-ASCII fallback ────────────────────────────────


@pytest.mark.parametrize("gh_path", ["分析.gh", "模擬測試.gh"])
def test_id_all_non_ascii_filename_hash_fallback(gh_path):
    m = manifest_from_io(gh_path, {})
    # Non-empty and matches the allowed charset.
    assert m.id
    assert re.fullmatch(r"[a-z0-9-]+", m.id)
    # Hash fallback shape: tool-<8 hex chars>.
    assert re.fullmatch(r"tool-[0-9a-f]{8}", m.id)
    # Stable: same input -> same output.
    assert manifest_from_io(gh_path, {}).id == m.id


def test_id_fallback_distinct_for_distinct_names():
    assert manifest_from_io("分析.gh", {}).id != manifest_from_io("模擬測試.gh", {}).id


@pytest.mark.parametrize(
    "gh_path",
    [
        "Radiation Study_hops.gh",
        "comfort_分析 v2.gh",
        "__a  b__.gh",
        "simple.gh",
        "分析.gh",
        "模擬測試.gh",
        "---.gh",
    ],
)
def test_id_never_empty(gh_path):
    m = manifest_from_io(gh_path, {})
    assert m.id
    assert re.fullmatch(r"[a-z0-9-]+", m.id)


# ── defensive parsing ────────────────────────────────────────────────


def test_empty_io_response_no_crash():
    m = manifest_from_io("foo.gh", {})
    assert m.inputs == []
    assert m.outputs == []
    assert m.description == ""


def test_missing_inputs_outputs_keys_no_crash():
    m = manifest_from_io("foo.gh", {"Description": "hi"})
    assert m.inputs == []
    assert m.outputs == []
    assert m.description == "hi"


def test_input_with_only_name_uses_defaults():
    io_response = {"Inputs": [{"Name": "_x"}]}
    m = manifest_from_io("foo.gh", io_response)
    assert len(m.inputs) == 1
    spec = m.inputs[0]
    assert spec.param_name == "_x"
    assert spec.kind == "string"  # ParamType missing -> classify("") fallback
    assert spec.description == ""
    assert spec.default is None
    assert spec.minimum is None
    assert spec.maximum is None
    assert spec.at_least == 1
    assert spec.at_most == 1
    # AtLeast defaults to 1, no Default -> required True
    assert spec.required is True


def test_output_with_only_name_uses_defaults():
    io_response = {"Outputs": [{"Name": "RH_OUT:foo"}]}
    m = manifest_from_io("foo.gh", io_response)
    assert len(m.outputs) == 1
    spec = m.outputs[0]
    assert spec.param_name == "foo"
    assert spec.kind == "string"
    assert spec.description == ""


def test_nickname_used_as_label_when_different():
    io_response = {
        "Inputs": [
            {"Name": "_x", "Nickname": "MyLabel", "ParamType": "Number"},
            {"Name": "_y", "Nickname": "_y", "ParamType": "Number"},
        ]
    }
    m = manifest_from_io("foo.gh", io_response)
    x = next(i for i in m.inputs if i.param_name == "_x")
    y = next(i for i in m.inputs if i.param_name == "_y")
    assert x.label == "MyLabel"
    assert y.label == ""


# ── to_mcp_tool ──────────────────────────────────────────────────────


def test_to_mcp_tool_basic_structure(manifest):
    tool = to_mcp_tool(manifest)
    assert tool["name"] == manifest.id
    assert tool["description"] == f"{manifest.display_name} — {manifest.description}"
    assert tool["inputSchema"]["type"] == "object"


def test_to_mcp_tool_description_no_description_field():
    m = manifest_from_io("foo.gh", {})  # description == ""
    tool = to_mcp_tool(m)
    assert tool["description"] == m.display_name


def test_to_mcp_tool_all_properties_present(manifest):
    tool = to_mcp_tool(manifest)
    props = tool["inputSchema"]["properties"]
    expected_names = {i.param_name for i in manifest.inputs}
    assert set(props.keys()) == expected_names


def test_to_mcp_tool_required_list_matches_sample(manifest):
    tool = to_mcp_tool(manifest)
    # Per sample: only _geometry has required=True and default is None.
    assert tool["inputSchema"]["required"] == ["_geometry"]


def test_to_mcp_tool_grid_size_schema_reuses_type_mapping(manifest):
    tool = to_mcp_tool(manifest)
    grid_schema = tool["inputSchema"]["properties"]["_grid_size"]
    assert grid_schema["default"] == 1.0
    assert grid_schema["minimum"] == 0.1
    assert grid_schema["maximum"] == 50.0


def test_to_mcp_tool_omits_required_key_when_all_optional():
    m = ToolManifest(
        id="all-optional",
        display_name="All Optional",
        gh_file="foo.gh",
        inputs=[
            InputSpec(
                param_name="_a",
                kind="number",
                param_type="Number",
                required=False,
                default=1.0,
            ),
            InputSpec(
                param_name="_b",
                kind="string",
                param_type="String",
                required=False,
                at_least=0,
            ),
        ],
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    tool = to_mcp_tool(m)
    assert "required" not in tool["inputSchema"]


def test_to_mcp_tool_required_field_is_single_source_of_truth():
    # InputSpec.required is authoritative: a manually-edited spec with
    # required=True AND a default must still appear in the required list.
    m = ToolManifest(
        id="manual-edit",
        display_name="Manual Edit",
        gh_file="foo.gh",
        inputs=[
            InputSpec(
                param_name="_grid",
                kind="number",
                param_type="Number",
                required=True,
                default=5.0,
            ),
        ],
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    tool = to_mcp_tool(m)
    assert tool["inputSchema"]["required"] == ["_grid"]


# ── ToolManifest roundtrip ───────────────────────────────────────────


def test_tool_manifest_roundtrip(manifest):
    dumped = manifest.model_dump()
    restored = ToolManifest.model_validate(dumped)
    assert restored == manifest


def test_input_spec_has_type_mapping_duck_type_attributes():
    # InputSpec must satisfy type_mapping's duck-typing contract exactly.
    spec = InputSpec(param_name="_x", kind="number", param_type="Number")
    for attr in ("param_name", "kind", "description", "required", "default", "minimum", "maximum", "enum_values"):
        assert hasattr(spec, attr)


# ── compute_name: RH_IN:/RH_OUT: separation (v2 group files) ──────────


def test_compute_name_none_by_default_for_v1_input():
    spec = InputSpec(param_name="_x", kind="number", param_type="Number")
    assert spec.compute_name is None


def test_compute_name_none_by_default_for_v1_output():
    spec = OutputSpec(param_name="foo", kind="number")
    assert spec.compute_name is None


def test_input_rh_in_prefix_stripped_and_compute_name_preserved():
    io_response = {
        "Inputs": [
            {"Name": "RH_IN:size", "Nickname": "RH_IN:size", "ParamType": "Number"},
        ]
    }
    m = manifest_from_io("foo.gh", io_response)
    spec = m.inputs[0]
    assert spec.param_name == "size"
    assert spec.compute_name == "RH_IN:size"


def test_input_without_rh_in_prefix_compute_name_is_none():
    io_response = {"Inputs": [{"Name": "_grid_size", "ParamType": "Number"}]}
    m = manifest_from_io("foo.gh", io_response)
    spec = m.inputs[0]
    assert spec.param_name == "_grid_size"
    assert spec.compute_name is None


def test_output_rh_out_prefix_stripped_and_compute_name_preserved():
    io_response = {"Outputs": [{"Name": "RH_OUT:report", "ParamType": "Text"}]}
    m = manifest_from_io("foo.gh", io_response)
    spec = m.outputs[0]
    assert spec.param_name == "report"
    assert spec.compute_name == "RH_OUT:report"


def test_output_without_rh_out_prefix_compute_name_is_none():
    io_response = {"Outputs": [{"Name": "Mesh", "ParamType": "Mesh"}]}
    m = manifest_from_io("foo.gh", io_response)
    spec = m.outputs[0]
    assert spec.param_name == "Mesh"
    assert spec.compute_name is None


def test_input_name_xrh_in_substring_not_prefix_compute_name_is_none():
    # "XRH_IN:foo" contains "RH_IN" as a substring but does NOT start with
    # the "RH_IN:" prefix -- manifest._split_name() only matches a leading
    # prefix (str.startswith), so this must fall through to the v1 branch:
    # param_name kept as-is, compute_name None.
    #
    # This is a different rule from marker.py's "RH_IN"/"RH_OUT" collision
    # check (hoger/ghio/marker.py::_validate_name), which matches the
    # substring *anywhere* in a mark name to prevent authors from choosing a
    # name that would itself collide with the "RH_IN:"/"RH_OUT:" NickName
    # prefix once written (e.g. mark name "XRH_IN" would produce NickName
    # "RH_IN:XRH_IN"). manifest.py's prefix check and marker.py's substring
    # check operate on different strings for different purposes (parsing an
    # already-written /io Name vs validating a not-yet-written mark name),
    # so a Name like "XRH_IN:foo" seen here is not something HOGER's own
    # marker.py would ever produce -- it could only reach manifest_from_io()
    # via a hand-authored/third-party GH group NickName. Since it doesn't
    # start with "RH_IN:", executor._compute_name() falls back to injecting
    # the literal param_name ("XRH_IN:foo") when talking to Rhino.Compute,
    # which is the correct (if unusual) behavior: whatever raw Name /io
    # reported for a non-prefixed group is exactly what must be echoed back.
    io_response = {"Inputs": [{"Name": "XRH_IN:foo", "ParamType": "Number"}]}
    m = manifest_from_io("foo.gh", io_response)
    spec = m.inputs[0]
    assert spec.param_name == "XRH_IN:foo"
    assert spec.compute_name is None


def test_input_name_exactly_rh_in_prefix_falls_back_to_slugify():
    # Name == "RH_IN:" (empty after stripping) -> param_name via slugify fallback,
    # never an empty string.
    io_response = {"Inputs": [{"Name": "RH_IN:", "ParamType": "Number"}]}
    m = manifest_from_io("foo.gh", io_response)
    spec = m.inputs[0]
    assert spec.param_name  # non-empty
    assert spec.compute_name == "RH_IN:"


def test_output_name_exactly_rh_out_prefix_falls_back_to_slugify():
    io_response = {"Outputs": [{"Name": "RH_OUT:", "ParamType": "Text"}]}
    m = manifest_from_io("foo.gh", io_response)
    spec = m.outputs[0]
    assert spec.param_name
    assert spec.compute_name == "RH_OUT:"


# ── DataTree-shaped Default parsing (v2 group files) ───────────────────


def test_datatree_default_double_parsed():
    io_response = {
        "Inputs": [
            {
                "Name": "RH_IN:size",
                "ParamType": "Number",
                "Minimum": 0.0,
                "Maximum": 10.0,
                "Default": {
                    "ParamName": "Number Slider",
                    "InnerTree": {"{0}": [{"type": "System.Double", "data": "3.0"}]},
                },
            }
        ]
    }
    m = manifest_from_io("foo.gh", io_response)
    spec = m.inputs[0]
    assert spec.default == 3.0
    assert spec.minimum == 0.0
    assert spec.maximum == 10.0
    assert spec.required is False  # DataTree default parsed successfully -> has default


def test_datatree_default_integer_parsed():
    io_response = {
        "Inputs": [
            {
                "Name": "RH_IN:count",
                "ParamType": "Integer",
                "Default": {
                    "InnerTree": {"{0}": [{"type": "System.Int32", "data": "7"}]},
                },
            }
        ]
    }
    m = manifest_from_io("foo.gh", io_response)
    spec = m.inputs[0]
    assert spec.default == 7


def test_datatree_default_boolean_parsed():
    io_response = {
        "Inputs": [
            {
                "Name": "RH_IN:flag",
                "ParamType": "Boolean",
                "Default": {
                    "InnerTree": {"{0}": [{"type": "System.Boolean", "data": "true"}]},
                },
            }
        ]
    }
    m = manifest_from_io("foo.gh", io_response)
    spec = m.inputs[0]
    assert spec.default is True


def test_datatree_default_non_string_data_used_as_is():
    io_response = {
        "Inputs": [
            {
                "Name": "RH_IN:size",
                "ParamType": "Number",
                "Default": {
                    "InnerTree": {"{0}": [{"type": "System.Double", "data": 3.0}]},
                },
            }
        ]
    }
    m = manifest_from_io("foo.gh", io_response)
    assert m.inputs[0].default == 3.0


def test_datatree_default_bad_shape_missing_innertree_key_treated_as_plain_dict():
    # A dict without "InnerTree" is not a DataTree default -> passed through as-is
    # (defensive: unrecognized dict shape, not our v2 case to unwrap).
    io_response = {
        "Inputs": [{"Name": "RH_IN:x", "ParamType": "Number", "Default": {"foo": "bar"}}]
    }
    m = manifest_from_io("foo.gh", io_response)
    assert m.inputs[0].default == {"foo": "bar"}


def test_datatree_default_empty_innertree_returns_none_with_warning(caplog):
    io_response = {
        "Inputs": [
            {"Name": "RH_IN:x", "ParamType": "Number", "Default": {"InnerTree": {}}}
        ]
    }
    with caplog.at_level("WARNING", logger="hoger.manifest"):
        m = manifest_from_io("foo.gh", io_response)
    assert m.inputs[0].default is None
    assert m.inputs[0].required is True  # no default -> AtLeast>=1 -> required
    assert any("hoger.manifest" == r.name for r in caplog.records)


def test_datatree_default_empty_branch_list_returns_none_with_warning(caplog):
    io_response = {
        "Inputs": [
            {
                "Name": "RH_IN:x",
                "ParamType": "Number",
                "Default": {"InnerTree": {"{0}": []}},
            }
        ]
    }
    with caplog.at_level("WARNING", logger="hoger.manifest"):
        m = manifest_from_io("foo.gh", io_response)
    assert m.inputs[0].default is None
    assert any("hoger.manifest" == r.name for r in caplog.records)


def test_datatree_default_item_missing_data_key_returns_none_with_warning(caplog):
    io_response = {
        "Inputs": [
            {
                "Name": "RH_IN:x",
                "ParamType": "Number",
                "Default": {"InnerTree": {"{0}": [{"type": "System.Double"}]}},
            }
        ]
    }
    with caplog.at_level("WARNING", logger="hoger.manifest"):
        m = manifest_from_io("foo.gh", io_response)
    assert m.inputs[0].default is None
    assert any("hoger.manifest" == r.name for r in caplog.records)


def test_datatree_default_bad_json_string_kept_as_raw_string():
    # data is a string that fails json.loads -> keep the original string
    # (not a case we expect for numeric sliders, but defensively verified).
    io_response = {
        "Inputs": [
            {
                "Name": "RH_IN:x",
                "ParamType": "String",
                "Default": {
                    "InnerTree": {"{0}": [{"type": "System.String", "data": "not json{"}]},
                },
            }
        ]
    }
    m = manifest_from_io("foo.gh", io_response)
    assert m.inputs[0].default == "not json{"


def test_v1_plain_default_unaffected_by_datatree_parsing(io_response):
    # Regression: the v1 sample fixture's plain (non-dict) Default values
    # must parse exactly as before.
    m = manifest_from_io("Radiation Study_hops.gh", io_response)
    grid = next(i for i in m.inputs if i.param_name == "_grid_size")
    assert grid.default == 1.0
    run = next(i for i in m.inputs if i.param_name == "_run")
    assert run.default is False


# ── roundtrip with compute_name ────────────────────────────────────────


def test_roundtrip_preserves_compute_name():
    m = ToolManifest(
        id="v2-tool",
        display_name="V2 Tool",
        gh_file="foo.gh",
        inputs=[
            InputSpec(
                param_name="size",
                compute_name="RH_IN:size",
                kind="number",
                param_type="Number",
                required=False,
                default=3.0,
            )
        ],
        outputs=[
            OutputSpec(param_name="report", compute_name="RH_OUT:report", kind="string"),
        ],
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    dumped = m.model_dump()
    restored = ToolManifest.model_validate(dumped)
    assert restored == m
    assert restored.inputs[0].compute_name == "RH_IN:size"
    assert restored.outputs[0].compute_name == "RH_OUT:report"


def test_legacy_json_without_compute_name_field_deserializes_with_none():
    # Old tools/*.json written before this task has no "compute_name" key at all.
    legacy_dump = {
        "id": "legacy-tool",
        "display_name": "Legacy Tool",
        "gh_file": "foo.gh",
        "inputs": [
            {
                "param_name": "_grid_size",
                "kind": "number",
                "param_type": "Number",
                "required": False,
                "default": 1.0,
            }
        ],
        "outputs": [{"param_name": "total", "kind": "number"}],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    m = ToolManifest.model_validate(legacy_dump)
    assert m.inputs[0].compute_name is None
    assert m.outputs[0].compute_name is None


def test_to_mcp_tool_uses_clean_param_name_not_compute_name():
    m = ToolManifest(
        id="v2-tool",
        display_name="V2 Tool",
        gh_file="foo.gh",
        inputs=[
            InputSpec(
                param_name="size",
                compute_name="RH_IN:size",
                kind="number",
                param_type="Number",
                required=True,
            )
        ],
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    tool = to_mcp_tool(m)
    assert "size" in tool["inputSchema"]["properties"]
    assert "RH_IN:size" not in tool["inputSchema"]["properties"]
    assert tool["inputSchema"]["required"] == ["size"]
