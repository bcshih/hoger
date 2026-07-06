"""
tests/test_describe.py — hoger.core.describe 單元測試。

describe.py 是 HOGER 自動描述生成的核心：把 scanner 的結構性事實
（元件清單、接線、值域）與 manifest 的欄位定義組合成給 AI 調用端讀的
自然語言描述。純函式、確定性（同輸入同輸出）、不呼叫任何外部服務。

測試資料：手構造的 InputSpec/OutputSpec + candidate dict（模擬
dataclasses.asdict(scanner.InputCandidate/OutputCandidate) 的形狀），
不依賴真實 .gh 檔案（那些留給 test_ghio_scanner.py / integration 測試）。
"""

from __future__ import annotations

from hoger.core.describe import (
    KNOWN_LIBRARIES,
    build_auto_doc,
    describe_input,
    describe_output,
    describe_tool,
)
from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest

# ── describe_input ───────────────────────────────────────────────────


def test_describe_input_with_candidate_feeds_and_value_context():
    spec = InputSpec(
        param_name="_grid_size",
        kind="number",
        param_type="Number",
        default=3.0,
        minimum=0.0,
        maximum=10.0,
    )
    candidate = {
        "instance_guid": "11111111-1111-1111-1111-111111111111",
        "object_type": "Number Slider",
        "nickname": "Grid Size",
        "current_value": "3.0",
        "minimum": 0.0,
        "maximum": 10.0,
        "feeds": [{"component": "LB Sensor Grid", "input": "_grid_size"}],
        "existing_mark": None,
    }
    text = describe_input(spec, candidate)
    assert "LB Sensor Grid" in text
    assert "_grid_size" in text
    # value context: current value / range should be mentioned
    assert "3" in text
    assert "0" in text and "10" in text


def test_describe_input_lists_at_most_first_three_feeds():
    spec = InputSpec(param_name="x", kind="number", param_type="Number")
    candidate = {
        "feeds": [
            {"component": f"Comp{i}", "input": f"in{i}"} for i in range(5)
        ],
        "nickname": None,
        "object_type": "Number Slider",
    }
    text = describe_input(spec, candidate)
    assert "Comp0" in text
    assert "Comp1" in text
    assert "Comp2" in text
    assert "Comp3" not in text
    assert "Comp4" not in text


def test_describe_input_boolean_value_context():
    spec = InputSpec(param_name="_run", kind="boolean", param_type="Boolean", default=True)
    candidate = {
        "feeds": [{"component": "Main", "input": "_run"}],
        "nickname": "Run",
        "object_type": "Boolean Toggle",
        "current_value": "True",
    }
    text = describe_input(spec, candidate)
    assert "True" in text or "true" in text.lower()


def test_describe_input_string_enum_value_context():
    spec = InputSpec(
        param_name="mode",
        kind="string",
        param_type="ValueList",
        enum_values=["fast", "accurate"],
    )
    text = describe_input(spec, None)
    assert "fast" in text
    assert "accurate" in text


def test_describe_input_geometry_kind_mentions_format():
    spec = InputSpec(param_name="_geometry", kind="geometry", param_type="Geometry")
    text = describe_input(spec, None)
    assert ".3dm" in text or "encoded" in text


def test_describe_input_no_candidate_degrades_to_value_context_only():
    # No scan candidate available (e.g. direct-parse import path with no
    # matching group) -- must not crash, must still produce something useful
    # from the spec's own default/min/max.
    spec = InputSpec(
        param_name="_grid_size",
        kind="number",
        param_type="Number",
        default=1.0,
        minimum=0.1,
        maximum=50.0,
    )
    text = describe_input(spec, None)
    assert text
    assert "1" in text
    assert "0.1" in text
    assert "50" in text
    # No wiring info available, so component names cannot appear.
    assert "LB" not in text


def test_describe_input_chinese_nickname_and_no_feeds():
    spec = InputSpec(param_name="roughness", kind="number", param_type="Number", default=0.5)
    candidate = {
        "feeds": [],
        "nickname": "粗糙度",
        "object_type": "Number Slider",
        "current_value": "0.5",
        "minimum": None,
        "maximum": None,
    }
    text = describe_input(spec, candidate)
    assert text
    # No feeds -> falls back gracefully, no crash, no bogus wiring claim.


def test_describe_input_never_empty():
    spec = InputSpec(param_name="x", kind="string", param_type="Text")
    assert describe_input(spec, None) != ""
    assert describe_input(spec, {}) != ""


# ── describe_output ──────────────────────────────────────────────────


def test_describe_output_with_candidate_fed_by():
    spec = OutputSpec(param_name="report", kind="string")
    candidate = {
        "fed_by": [{"component": "LB UTCI Comfort", "output": "report"}],
        "nickname": "Report",
        "object_type": "Panel",
    }
    text = describe_output(spec, candidate)
    assert "LB UTCI Comfort" in text
    assert "AttributeUserText" in text or ".3dm" in text


def test_describe_output_geometry_kind_mentions_3dm_write():
    spec = OutputSpec(param_name="Mesh", kind="geometry")
    text = describe_output(spec, None)
    assert ".3dm" in text


def test_describe_output_number_kind_mentions_json():
    spec = OutputSpec(param_name="total", kind="number")
    text = describe_output(spec, None)
    assert "JSON" in text or "json" in text


def test_describe_output_no_candidate_degrades_gracefully():
    spec = OutputSpec(param_name="total", kind="number")
    text = describe_output(spec, None)
    assert text
    assert "LB" not in text


def test_describe_output_never_empty():
    spec = OutputSpec(param_name="x", kind="string")
    assert describe_output(spec, None) != ""
    assert describe_output(spec, {}) != ""


# ── describe_tool ────────────────────────────────────────────────────


def _manifest(inputs=None, outputs=None, display_name="My Tool"):
    return ToolManifest(
        id="my-tool",
        display_name=display_name,
        gh_file="C:/x/My Tool.gh",
        inputs=inputs or [],
        outputs=outputs or [],
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_describe_tool_mentions_component_count():
    m = _manifest()
    inventory = {"LB Outdoor Solar MRT": 1, "Division": 2}
    text = describe_tool(m, inventory)
    assert "3" in text  # 1 + 2 = 3 components total


def test_describe_tool_detects_known_library_ladybug():
    m = _manifest()
    inventory = {"LB Outdoor Solar MRT": 1, "LB UTCI Comfort": 1}
    text = describe_tool(m, inventory)
    assert "Ladybug" in text


def test_describe_tool_detects_known_library_honeybee():
    m = _manifest()
    inventory = {"HB Room": 3}
    text = describe_tool(m, inventory)
    assert "Honeybee" in text


def test_describe_tool_detects_karamba():
    m = _manifest()
    inventory = {"Karamba.Beam": 2}
    text = describe_tool(m, inventory)
    assert "Karamba3D" in text


def test_describe_tool_lists_top_frequent_component_names():
    m = _manifest()
    inventory = {"Division": 10, "Multiplication": 8, "Relay": 5, "Panel": 1, "Addition": 1}
    text = describe_tool(m, inventory)
    assert "Division" in text
    assert "Multiplication" in text


def test_describe_tool_no_inventory_still_produces_text():
    m = _manifest()
    text = describe_tool(m, None)
    assert text
    assert "轉換" in text or "GH" in text or "Grasshopper" in text


def test_describe_tool_input_output_summary_counts():
    m = _manifest(
        inputs=[
            InputSpec(param_name="a", kind="number", param_type="Number"),
            InputSpec(param_name="b", kind="number", param_type="Number"),
            InputSpec(param_name="c", kind="geometry", param_type="Geometry"),
        ],
        outputs=[
            OutputSpec(param_name="Mesh", kind="geometry"),
            OutputSpec(param_name="report", kind="string"),
        ],
    )
    text = describe_tool(m, {})
    assert "3 個輸入" in text or "3個輸入" in text
    assert "2 個輸出" in text or "2個輸出" in text


def test_describe_tool_mentions_source_file():
    m = _manifest(display_name="Radiation Study")
    text = describe_tool(m, {})
    assert "Radiation Study" in text or "轉換" in text


def test_describe_tool_deterministic():
    m = _manifest(
        inputs=[InputSpec(param_name="a", kind="number", param_type="Number")],
    )
    inventory = {"LB Room": 2, "Division": 1}
    assert describe_tool(m, inventory) == describe_tool(m, inventory)


# ── build_auto_doc ───────────────────────────────────────────────────


def test_build_auto_doc_contains_sections():
    m = _manifest(
        inputs=[
            InputSpec(
                param_name="_grid_size",
                kind="number",
                param_type="Number",
                default=1.0,
                minimum=0.1,
                maximum=50.0,
            ),
        ],
        outputs=[OutputSpec(param_name="report", kind="string")],
    )
    text = build_auto_doc(m, None)
    assert "工具說明" in text
    assert "輸入參數" in text
    assert "輸出" in text
    assert "呼叫提示" in text
    assert "_grid_size" in text
    assert "report" in text


def test_build_auto_doc_with_scan_dict_enriches_params():
    m = _manifest(
        inputs=[
            InputSpec(
                param_name="size",
                compute_name="RH_IN:size",
                kind="number",
                param_type="Number",
                default=3.0,
            ),
        ],
        outputs=[
            OutputSpec(param_name="report", compute_name="RH_OUT:report", kind="string"),
        ],
    )
    scan_dict = {
        "inputs": [
            {
                "instance_guid": "g1",
                "object_type": "Number Slider",
                "nickname": "RH_IN:size",
                "current_value": "3.0",
                "minimum": 0.0,
                "maximum": 10.0,
                "feeds": [{"component": "LB Sensor Grid", "input": "_grid_size"}],
                "existing_mark": "RH_IN:size",
            }
        ],
        "outputs": [
            {
                "instance_guid": "g2",
                "object_type": "Panel",
                "nickname": "RH_OUT:report",
                "fed_by": [{"component": "LB UTCI Comfort", "output": "report"}],
                "existing_mark": "RH_OUT:report",
            }
        ],
        "already_marked_count": 2,
        "object_count": 10,
        "component_inventory": {"LB Sensor Grid": 1, "LB UTCI Comfort": 1},
    }
    text = build_auto_doc(m, scan_dict)
    assert "LB Sensor Grid" in text
    assert "LB UTCI Comfort" in text


def test_build_auto_doc_mentions_default_value_semantics():
    m = _manifest()
    text = build_auto_doc(m, None)
    assert "預設值" in text
    assert "滑桿" in text or "存檔" in text


def test_build_auto_doc_truncates_when_too_long():
    # Manufacture a manifest with many inputs/outputs to blow past ~3000 chars,
    # then confirm truncation happened and length stays bounded.
    inputs = [
        InputSpec(param_name=f"param_{i}", kind="number", param_type="Number", description="x" * 50)
        for i in range(80)
    ]
    m = _manifest(inputs=inputs)
    inventory = {f"Component{i}": i + 1 for i in range(200)}
    text = build_auto_doc(m, {"component_inventory": inventory})
    assert len(text) <= 3100  # small slack for truncation marker
    assert text.endswith("…") or "截斷" in text or len(text) <= 3000


def test_build_auto_doc_is_deterministic():
    m = _manifest(
        inputs=[InputSpec(param_name="a", kind="number", param_type="Number")],
        outputs=[OutputSpec(param_name="b", kind="string")],
    )
    assert build_auto_doc(m, None) == build_auto_doc(m, None)


# ── KNOWN_LIBRARIES sanity ───────────────────────────────────────────


def test_known_libraries_has_expected_entries():
    prefixes = {entry[0] for entry in KNOWN_LIBRARIES}
    assert "LB " in prefixes
    assert "HB " in prefixes
    assert any("Karamba" in p for p in prefixes)
    assert any("Galapagos" in p for p in prefixes)
