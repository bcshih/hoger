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
