"""
tests/test_executor.py — hoger.core.executor 的單元測試。

executor 是 Phase 2 的整合層：使用者參數 -> DataTree payload ->
Rhino.Compute 執行 -> ToolResult（JSON-safe outputs + result_3dm + 診斷資訊）。

測試分兩大塊：
1. build_trees()：manifest.inputs 逐一處理成 tree payload 列表，涵蓋所有
   kind、required/default 邏輯、型別錯誤、geometry 的 encoded/file_3dm 兩路徑。
2. run_tool()：monkeypatch compute_client.evaluate，驗證整合邏輯（正常流程、
   ComputeError 不 crash、errors/warnings/modelunits 傳遞、ToolArgError 不被吞）。

geometry file_3dm 測試用 rhino3dm 在 tmp_path 真實建立 .3dm（含兩個 layer），
不 mock 檔案讀取——確保 _load_geometry_from_3dm 的 layer 篩選邏輯是對真實
rhino3dm API 驗證過的。
"""

import json
from datetime import datetime, timezone

import pytest
import rhino3dm

from hoger.core.compute_client import ComputeError
from hoger.core.executor import (
    ToolArgError,
    ToolResult,
    _load_geometry_from_3dm,
    build_trees,
    run_tool,
    run_tool_raw,
)
from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest

# ── helpers ──────────────────────────────────────────────────────────


def _make_manifest(inputs=None, outputs=None, gh_file="test.gh"):
    now = datetime.now(timezone.utc).isoformat()
    return ToolManifest(
        id="test-tool",
        display_name="Test Tool",
        gh_file=gh_file,
        inputs=inputs or [],
        outputs=outputs or [],
        created_at=now,
        updated_at=now,
    )


def _mesh():
    mesh = rhino3dm.Mesh()
    mesh.Vertices.Add(0, 0, 0)
    mesh.Vertices.Add(1, 0, 0)
    mesh.Vertices.Add(0, 1, 0)
    mesh.Faces.AddFace(0, 1, 2)
    return mesh


def _make_3dm_with_layers(tmp_path):
    """
    建立含兩個 layer 的真實 .3dm：
    - LayerA：一個 mesh
    - LayerB：一個 point
    回傳檔案路徑字串。
    """
    f = rhino3dm.File3dm()

    layer_a = rhino3dm.Layer()
    layer_a.Name = "LayerA"
    idx_a = f.Layers.Add(layer_a)

    layer_b = rhino3dm.Layer()
    layer_b.Name = "LayerB"
    idx_b = f.Layers.Add(layer_b)

    attrs_a = rhino3dm.ObjectAttributes()
    attrs_a.LayerIndex = idx_a
    f.Objects.AddMesh(_mesh(), attrs_a)

    attrs_b = rhino3dm.ObjectAttributes()
    attrs_b.LayerIndex = idx_b
    f.Objects.Add(rhino3dm.Point(rhino3dm.Point3d(1, 2, 3)), attrs_b)

    path = tmp_path / "geo.3dm"
    f.Write(str(path), 7)
    return str(path)


# ── build_trees: scalar kinds ───────────────────────────────────────


def test_build_trees_number():
    manifest = _make_manifest([InputSpec(param_name="width", kind="number", required=True)])
    trees = build_trees(manifest, {"width": 3.5})
    assert len(trees) == 1
    tree = trees[0]
    assert tree["ParamName"] == "width"
    item = tree["InnerTree"]["{0}"][0]
    assert item["type"] == "System.Double"
    assert item["data"] == 3.5


def test_build_trees_integer():
    manifest = _make_manifest([InputSpec(param_name="count", kind="integer", required=True)])
    trees = build_trees(manifest, {"count": "7"})
    item = trees[0]["InnerTree"]["{0}"][0]
    assert item["type"] == "System.Int32"
    assert item["data"] == 7


def test_build_trees_boolean_native_and_string():
    manifest = _make_manifest([InputSpec(param_name="flag", kind="boolean", required=True)])

    trees = build_trees(manifest, {"flag": True})
    item = trees[0]["InnerTree"]["{0}"][0]
    assert item["type"] == "System.Boolean"
    assert item["data"] == "true"

    trees = build_trees(manifest, {"flag": "FALSE"})
    item = trees[0]["InnerTree"]["{0}"][0]
    assert item["data"] == "false"


def test_build_trees_string():
    manifest = _make_manifest([InputSpec(param_name="label", kind="string", required=True)])
    trees = build_trees(manifest, {"label": "hello"})
    item = trees[0]["InnerTree"]["{0}"][0]
    assert item["type"] == "System.String"
    assert item["data"] == json.dumps("hello")


def test_build_trees_string_accepts_scalar_types_via_str():
    manifest = _make_manifest([InputSpec(param_name="label", kind="string", required=True)])
    trees = build_trees(manifest, {"label": 42})
    item = trees[0]["InnerTree"]["{0}"][0]
    assert item["data"] == json.dumps("42")


# ── build_trees: required / default logic ───────────────────────────


def test_build_trees_missing_required_raises_with_param_name():
    manifest = _make_manifest([InputSpec(param_name="width", kind="number", required=True)])
    with pytest.raises(ToolArgError) as exc_info:
        build_trees(manifest, {})
    assert "width" in str(exc_info.value)


def test_build_trees_missing_with_default_uses_default():
    manifest = _make_manifest(
        [InputSpec(param_name="width", kind="number", required=False, default=2.0)]
    )
    trees = build_trees(manifest, {})
    assert len(trees) == 1
    item = trees[0]["InnerTree"]["{0}"][0]
    assert item["data"] == 2.0


def test_build_trees_missing_optional_no_default_skips_param():
    manifest = _make_manifest(
        [InputSpec(param_name="width", kind="number", required=False, default=None)]
    )
    trees = build_trees(manifest, {})
    assert trees == []


# ── build_trees: type errors ─────────────────────────────────────────


def test_build_trees_number_bad_value_raises():
    manifest = _make_manifest([InputSpec(param_name="width", kind="number", required=True)])
    with pytest.raises(ToolArgError) as exc_info:
        build_trees(manifest, {"width": "abc"})
    msg = str(exc_info.value)
    assert "width" in msg
    assert "expected a number" in msg


def test_build_trees_integer_bad_value_raises():
    manifest = _make_manifest([InputSpec(param_name="count", kind="integer", required=True)])
    with pytest.raises(ToolArgError) as exc_info:
        build_trees(manifest, {"count": "abc"})
    msg = str(exc_info.value)
    assert "count" in msg
    assert "expected a number" in msg


def test_build_trees_boolean_bad_value_raises():
    manifest = _make_manifest([InputSpec(param_name="flag", kind="boolean", required=True)])
    with pytest.raises(ToolArgError) as exc_info:
        build_trees(manifest, {"flag": "yes"})
    # 錯誤訊息必須保留 expected 提示與參數名
    msg = str(exc_info.value)
    assert "flag" in msg
    assert "expected bool or 'true'/'false'" in msg


def test_build_trees_string_bad_value_raises():
    manifest = _make_manifest([InputSpec(param_name="label", kind="string", required=True)])
    with pytest.raises(ToolArgError):
        build_trees(manifest, {"label": {"not": "a scalar"}})


# ── build_trees: geometry — encoded ──────────────────────────────────


def test_build_trees_geometry_encoded():
    mesh = _mesh()
    encoded_json = json.dumps(mesh.Encode())
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    trees = build_trees(manifest, {"geo": {"encoded": [encoded_json]}})
    item = trees[0]["InnerTree"]["{0}"][0]
    assert item["type"] == "Rhino.Geometry.GeometryBase"
    assert item["data"] == encoded_json


def test_build_trees_geometry_encoded_type_error_becomes_tool_arg_error():
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    with pytest.raises(ToolArgError):
        build_trees(manifest, {"geo": {"encoded": [123]}})


def test_build_trees_geometry_encoded_empty_list_required_raises():
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    with pytest.raises(ToolArgError) as exc_info:
        build_trees(manifest, {"geo": {"encoded": []}})
    msg = str(exc_info.value)
    assert "geo" in msg
    assert "encoded list is empty" in msg


def test_build_trees_geometry_encoded_empty_list_optional_skips_param():
    # optional 參數給空 encoded list 視同未提供——跳過，不建 tree、不報錯
    manifest = _make_manifest(
        [InputSpec(param_name="geo", kind="geometry", required=False)]
    )
    trees = build_trees(manifest, {"geo": {"encoded": []}})
    assert trees == []


def test_build_trees_geometry_encoded_null_falls_through_to_file_3dm(tmp_path):
    # JSON 客戶端常送 "encoded": null——必須落到 file_3dm 分支正常載入，
    # 不得誤報 "encoded list is empty"
    path = _make_3dm_with_layers(tmp_path)
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    trees = build_trees(manifest, {"geo": {"encoded": None, "file_3dm": path}})
    items = trees[0]["InnerTree"]["{0}"]
    assert len(items) == 2


# ── build_trees: geometry — file_3dm ─────────────────────────────────


def test_build_trees_geometry_file_3dm_no_layer_gets_all(tmp_path):
    path = _make_3dm_with_layers(tmp_path)
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    trees = build_trees(manifest, {"geo": {"file_3dm": path}})
    items = trees[0]["InnerTree"]["{0}"]
    assert len(items) == 2


def test_build_trees_geometry_file_3dm_with_layer_filters(tmp_path):
    path = _make_3dm_with_layers(tmp_path)
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    trees = build_trees(manifest, {"geo": {"file_3dm": path, "layer": "LayerA"}})
    items = trees[0]["InnerTree"]["{0}"]
    assert len(items) == 1
    assert items[0]["type"] == "Rhino.Geometry.Mesh"


def test_build_trees_geometry_file_3dm_unknown_layer_raises(tmp_path):
    path = _make_3dm_with_layers(tmp_path)
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    with pytest.raises(ToolArgError):
        build_trees(manifest, {"geo": {"file_3dm": path, "layer": "NoSuchLayer"}})


def test_build_trees_geometry_file_missing_raises(tmp_path):
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    with pytest.raises(ToolArgError):
        build_trees(manifest, {"geo": {"file_3dm": str(tmp_path / "nope.3dm")}})


def test_build_trees_geometry_zero_objects_required_raises(tmp_path):
    # empty file (no objects at all) on a required geometry param -> ToolArgError
    f = rhino3dm.File3dm()
    path = tmp_path / "empty.3dm"
    f.Write(str(path), 7)

    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    with pytest.raises(ToolArgError):
        build_trees(manifest, {"geo": {"file_3dm": str(path)}})


def test_build_trees_geometry_dict_missing_both_keys_raises():
    manifest = _make_manifest([InputSpec(param_name="geo", kind="geometry", required=True)])
    with pytest.raises(ToolArgError):
        build_trees(manifest, {"geo": {}})


# ── build_trees: extra args / ordering ───────────────────────────────


def test_build_trees_extra_args_warns_and_ignored(caplog):
    manifest = _make_manifest([InputSpec(param_name="width", kind="number", required=True)])
    with caplog.at_level("WARNING", logger="hoger.executor"):
        trees = build_trees(manifest, {"width": 1.0, "unexpected_param": 42})
    assert len(trees) == 1
    assert any("hoger.executor" == r.name for r in caplog.records)
    assert any("unexpected_param" in r.message for r in caplog.records)


def test_build_trees_follows_manifest_order():
    manifest = _make_manifest(
        [
            InputSpec(param_name="b", kind="number", required=True),
            InputSpec(param_name="a", kind="number", required=True),
        ]
    )
    trees = build_trees(manifest, {"a": 1.0, "b": 2.0})
    assert [t["ParamName"] for t in trees] == ["b", "a"]


# ── _load_geometry_from_3dm direct tests ─────────────────────────────


def test_load_geometry_from_3dm_file_not_found(tmp_path):
    with pytest.raises(ToolArgError):
        _load_geometry_from_3dm(str(tmp_path / "missing.3dm"))


def test_load_geometry_from_3dm_returns_geometry_objects(tmp_path):
    path = _make_3dm_with_layers(tmp_path)
    objs = _load_geometry_from_3dm(path)
    assert len(objs) == 2


def test_load_geometry_from_3dm_layer_filter(tmp_path):
    path = _make_3dm_with_layers(tmp_path)
    objs = _load_geometry_from_3dm(path, layer="LayerB")
    assert len(objs) == 1
    assert isinstance(objs[0], rhino3dm.Point)


# ── run_tool ───────────────────────────────────────────────────────


def _gh_response(values):
    return {
        "values": [
            {"ParamName": p, "InnerTree": {"{0}": items}} for p, items in values
        ]
    }


def test_run_tool_normal_flow(tmp_path, monkeypatch):
    manifest = _make_manifest(
        inputs=[InputSpec(param_name="width", kind="number", required=True)],
        outputs=[
            OutputSpec(param_name="total", kind="number"),
            OutputSpec(param_name="report", kind="string"),
            OutputSpec(param_name="Mesh", kind="geometry"),
        ],
    )

    mesh = _mesh()
    fake_response = _gh_response(
        [
            ("RH_OUT:total", [{"type": "System.Double", "data": "9.5"}]),
            (
                "RH_OUT:report",
                [{"type": "System.String", "data": json.dumps("done")}],
            ),
            (
                "RH_OUT:Mesh",
                [{"type": "Rhino.Geometry.Mesh", "data": json.dumps(mesh.Encode())}],
            ),
        ]
    )
    fake_response["modelunits"] = "mm"

    captured = {}

    def fake_evaluate(gh_path, trees):
        captured["gh_path"] = gh_path
        captured["trees"] = trees
        return fake_response

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", fake_evaluate)

    result = run_tool(manifest, {"width": 5.0}, out_dir=tmp_path)

    assert isinstance(result, ToolResult)
    assert result.outputs["total"] == [9.5]
    assert result.outputs["report"] == ["done"]
    assert result.outputs["Mesh"] == {"count": 1, "in_3dm": True}
    assert result.result_3dm is not None
    assert result.elapsed_ms >= 0
    assert result.errors == []
    assert result.warnings == []
    assert result.modelunits == "mm"
    assert result.raw is fake_response
    assert captured["gh_path"] == manifest.gh_file

    # JSON-safe guarantee
    json.dumps(result.outputs)


def test_run_tool_compute_error_does_not_crash(tmp_path, monkeypatch):
    manifest = _make_manifest(
        inputs=[InputSpec(param_name="width", kind="number", required=True)],
        outputs=[
            OutputSpec(param_name="total", kind="number"),
            OutputSpec(param_name="Mesh", kind="geometry"),
        ],
    )

    def fake_evaluate(gh_path, trees):
        raise ComputeError(
            "Rhino.Compute HTTP 500: boom", status_code=500, body="boom body"
        )

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", fake_evaluate)

    result = run_tool(manifest, {"width": 1.0}, out_dir=tmp_path)

    # outputs 形狀與正常路徑一致：geometry kind 是 dict，不是 []
    assert result.outputs == {
        "total": [],
        "Mesh": {"count": 0, "in_3dm": False},
    }
    assert result.result_3dm is None
    assert len(result.errors) == 1
    assert "boom" in result.errors[0]
    assert result.warnings == []
    assert result.modelunits is None
    # raw 保留 ComputeError 攜帶的 status_code/body（JSON-safe）
    assert result.raw == {"error_status_code": 500, "error_body": "boom body"}
    json.dumps(result.outputs)
    json.dumps(result.raw)


def test_run_tool_compute_error_geometry_output_keeps_dict_shape(tmp_path, monkeypatch):
    # 下游讀 outputs["Mesh"]["count"] 在失敗時不得 TypeError
    manifest = _make_manifest(
        inputs=[],
        outputs=[OutputSpec(param_name="Mesh", kind="geometry")],
    )

    def fake_evaluate(gh_path, trees):
        raise ComputeError("connection refused")

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", fake_evaluate)

    result = run_tool(manifest, {}, out_dir=tmp_path)

    assert isinstance(result.outputs["Mesh"], dict)
    assert result.outputs["Mesh"]["count"] == 0
    assert result.outputs["Mesh"]["in_3dm"] is False
    json.dumps(result.outputs)


def test_run_tool_passes_through_errors_warnings_modelunits(tmp_path, monkeypatch):
    manifest = _make_manifest(
        inputs=[],
        outputs=[OutputSpec(param_name="total", kind="number")],
    )

    fake_response = _gh_response(
        [("RH_OUT:total", [{"type": "System.Double", "data": "1.0"}])]
    )
    fake_response["errors"] = ["some GH error"]
    fake_response["warnings"] = ["some GH warning"]
    fake_response["modelunits"] = "meters"

    monkeypatch.setattr(
        "hoger.core.executor.compute_client.evaluate", lambda gh_path, trees: fake_response
    )

    result = run_tool(manifest, {}, out_dir=tmp_path)

    assert result.errors == ["some GH error"]
    assert result.warnings == ["some GH warning"]
    assert result.modelunits == "meters"
    assert result.raw is fake_response


def test_run_tool_tool_arg_error_propagates(tmp_path, monkeypatch):
    manifest = _make_manifest(
        inputs=[InputSpec(param_name="width", kind="number", required=True)],
        outputs=[],
    )

    def fake_evaluate(gh_path, trees):
        raise AssertionError("evaluate should not be called when args are invalid")

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", fake_evaluate)

    with pytest.raises(ToolArgError):
        run_tool(manifest, {}, out_dir=tmp_path)


def test_run_tool_outputs_with_no_geometry_or_string_has_no_3dm(tmp_path, monkeypatch):
    manifest = _make_manifest(
        inputs=[],
        outputs=[OutputSpec(param_name="total", kind="number")],
    )
    fake_response = _gh_response(
        [("RH_OUT:total", [{"type": "System.Double", "data": "1.0"}])]
    )
    monkeypatch.setattr(
        "hoger.core.executor.compute_client.evaluate", lambda gh_path, trees: fake_response
    )

    result = run_tool(manifest, {}, out_dir=tmp_path)
    assert result.result_3dm is None
    assert result.outputs == {"total": [1.0]}


# ── run_tool_raw ──────────────────────────────────────────────────────


def test_run_tool_raw_passes_values_through_unchanged(tmp_path, monkeypatch):
    """
    關鍵規則：Hops 送來的 raw_values 必須原樣 passthrough 給
    compute_client.evaluate，不 decode/re-encode（build_trees 完全不參與）。
    """
    manifest = _make_manifest(
        inputs=[InputSpec(param_name="width", kind="number", required=True)],
        outputs=[OutputSpec(param_name="total", kind="number")],
    )

    raw_values = [
        {
            "ParamName": "width",
            "InnerTree": {"{0}": [{"type": "System.Double", "data": "3.5"}]},
        }
    ]

    fake_response = _gh_response(
        [("RH_OUT:total", [{"type": "System.Double", "data": "9.5"}])]
    )

    captured = {}

    def fake_evaluate(gh_path, tree_payloads):
        captured["gh_path"] = gh_path
        captured["payloads"] = tree_payloads
        return fake_response

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", fake_evaluate)

    result = run_tool_raw(manifest, raw_values, out_dir=tmp_path)

    # passthrough：同一物件、同一結構，未被 build_trees 改寫或重建
    assert captured["payloads"] is raw_values
    assert captured["payloads"] == raw_values
    assert captured["gh_path"] == manifest.gh_file
    assert result.outputs["total"] == [9.5]


def test_run_tool_raw_shape_matches_run_tool(tmp_path, monkeypatch):
    """ToolResult 形狀（outputs JSON-safe/result_3dm/elapsed_ms/errors/warnings/modelunits/raw）
    與 run_tool 一致——共用同一組私有實作，僅 tree 來源不同。"""
    manifest = _make_manifest(
        inputs=[],
        outputs=[
            OutputSpec(param_name="total", kind="number"),
            OutputSpec(param_name="report", kind="string"),
            OutputSpec(param_name="Mesh", kind="geometry"),
        ],
    )

    mesh = _mesh()
    fake_response = _gh_response(
        [
            ("RH_OUT:total", [{"type": "System.Double", "data": "9.5"}]),
            (
                "RH_OUT:report",
                [{"type": "System.String", "data": json.dumps("done")}],
            ),
            (
                "RH_OUT:Mesh",
                [{"type": "Rhino.Geometry.Mesh", "data": json.dumps(mesh.Encode())}],
            ),
        ]
    )
    fake_response["modelunits"] = "mm"

    monkeypatch.setattr(
        "hoger.core.executor.compute_client.evaluate", lambda gh_path, trees: fake_response
    )

    result = run_tool_raw(manifest, [], out_dir=tmp_path)

    assert isinstance(result, ToolResult)
    assert result.outputs["total"] == [9.5]
    assert result.outputs["report"] == ["done"]
    assert result.outputs["Mesh"] == {"count": 1, "in_3dm": True}
    assert result.result_3dm is not None
    assert result.elapsed_ms >= 0
    assert result.errors == []
    assert result.warnings == []
    assert result.modelunits == "mm"
    assert result.raw is fake_response
    json.dumps(result.outputs)


def test_run_tool_raw_compute_error_does_not_crash(tmp_path, monkeypatch):
    manifest = _make_manifest(
        inputs=[],
        outputs=[OutputSpec(param_name="total", kind="number")],
    )

    def fake_evaluate(gh_path, tree_payloads):
        raise ComputeError("Rhino.Compute HTTP 500: boom", status_code=500, body="boom body")

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", fake_evaluate)

    result = run_tool_raw(manifest, [], out_dir=tmp_path)

    assert result.outputs == {"total": []}
    assert result.result_3dm is None
    assert len(result.errors) == 1
    assert "boom" in result.errors[0]
    assert result.raw == {"error_status_code": 500, "error_body": "boom body"}
    json.dumps(result.outputs)
    json.dumps(result.raw)


def test_run_tool_raw_does_not_call_build_trees(tmp_path, monkeypatch):
    # build_trees 不應被呼叫——raw_values 直接當 tree_payloads 使用
    manifest = _make_manifest(
        inputs=[InputSpec(param_name="width", kind="number", required=True)],
        outputs=[],
    )

    def fail_build_trees(*args, **kwargs):
        raise AssertionError("build_trees should not be called by run_tool_raw")

    monkeypatch.setattr("hoger.core.executor.build_trees", fail_build_trees)
    monkeypatch.setattr(
        "hoger.core.executor.compute_client.evaluate", lambda gh_path, trees: {"values": []}
    )

    # 沒有提供 "width"，若誤走 build_trees 會因 required 缺值而 raise ToolArgError
    run_tool_raw(manifest, [], out_dir=tmp_path)
