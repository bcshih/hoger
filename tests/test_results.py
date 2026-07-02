"""
tests/test_results.py — hoger.core.results 的單元測試。

results.py 負責兩件事：
1. parse(): /grasshopper 回應 dict -> {param_name: list}（依 ToolManifest.outputs
   逐一解析，防禦性——res 為 None / 缺 "values" / 壞資料都不 crash）。
2. write_result_3dm(): parse() 的輸出 -> .3dm 檔案（絕對路徑字串）。
   HOGER 的核心設計約束：字串輸出一律以 Rhino AttributeUserText
   （ObjectAttributes.SetUserString）附著在幾何物件上，不裸傳字串。

回應 fixture 在測試內程式化構造（不用靜態檔）——幾何 data 必須是真的
rhino3dm Encode() 產物才能 Decode() 回來，靜態 JSON 檔案無法保證這點。
"""

import json
from datetime import datetime, timezone

import pytest
import rhino3dm

from hoger.core.manifest import OutputSpec, ToolManifest
from hoger.core.results import parse, write_result_3dm

# ── helpers ──────────────────────────────────────────────────────────


def _gh_response(values):
    """values: [(param_name_with_prefix, items), ...] -> /grasshopper 回應 dict"""
    return {
        "values": [
            {"ParamName": p, "InnerTree": {"{0}": items}} for p, items in values
        ]
    }


def _mesh_item():
    mesh = rhino3dm.Mesh()
    mesh.Vertices.Add(0, 0, 0)
    mesh.Vertices.Add(1, 0, 0)
    mesh.Vertices.Add(0, 1, 0)
    mesh.Faces.AddFace(0, 1, 2)
    return {"type": "Rhino.Geometry.Mesh", "data": json.dumps(mesh.Encode())}


def _make_manifest(outputs):
    now = datetime.now(timezone.utc).isoformat()
    return ToolManifest(
        id="test-tool",
        display_name="Test Tool",
        gh_file="test.gh",
        outputs=outputs,
        created_at=now,
        updated_at=now,
    )


# ── parse: basic values ──────────────────────────────────────────────


def test_parse_number_single_value():
    manifest = _make_manifest([OutputSpec(param_name="total", kind="number")])
    res = _gh_response([("RH_OUT:total", [{"type": "System.Double", "data": "123.4"}])])
    result = parse(res, manifest)
    assert result["total"] == [123.4]


def test_parse_multi_value_same_name_all_preserved_in_order():
    manifest = _make_manifest([OutputSpec(param_name="values", kind="number")])
    # build a response with multiple branches manually to control ordering
    res = {
        "values": [
            {
                "ParamName": "RH_OUT:values",
                "InnerTree": {
                    "{0}": [
                        {"type": "System.Double", "data": "1.0"},
                        {"type": "System.Double", "data": "2.0"},
                    ],
                    "{1}": [
                        {"type": "System.Double", "data": "3.0"},
                    ],
                },
            }
        ]
    }
    result = parse(res, manifest)
    assert result["values"] == [1.0, 2.0, 3.0]


def test_parse_string_output_with_chinese():
    manifest = _make_manifest([OutputSpec(param_name="report", kind="string")])
    res = _gh_response(
        [("RH_OUT:report", [{"type": "System.String", "data": json.dumps("總輻射 123 kWh")}])]
    )
    result = parse(res, manifest)
    assert result["report"] == ["總輻射 123 kWh"]


def test_parse_geometry_output_decodes_to_rhino3dm_object():
    manifest = _make_manifest([OutputSpec(param_name="Mesh", kind="geometry")])
    res = _gh_response([("RH_OUT:Mesh", [_mesh_item()])])
    result = parse(res, manifest)
    assert len(result["Mesh"]) == 1
    assert isinstance(result["Mesh"][0], rhino3dm.Mesh)


def test_parse_strips_rh_out_prefix_to_match_manifest():
    manifest = _make_manifest([OutputSpec(param_name="total", kind="number")])
    res = _gh_response([("RH_OUT:total", [{"type": "System.Double", "data": "42.0"}])])
    result = parse(res, manifest)
    assert result["total"] == [42.0]


# ── parse: defensive paths ───────────────────────────────────────────


def test_parse_res_is_none_returns_empty_lists_for_all_keys(caplog):
    manifest = _make_manifest(
        [OutputSpec(param_name="total", kind="number"), OutputSpec(param_name="Mesh", kind="geometry")]
    )
    with caplog.at_level("WARNING", logger="hoger.results"):
        result = parse(None, manifest)
    assert result == {"total": [], "Mesh": []}
    assert any("hoger.results" == r.name for r in caplog.records)


def test_parse_missing_values_key_returns_empty_lists(caplog):
    manifest = _make_manifest([OutputSpec(param_name="total", kind="number")])
    with caplog.at_level("WARNING", logger="hoger.results"):
        result = parse({}, manifest)
    assert result == {"total": []}
    assert len(caplog.records) >= 1


def test_parse_unknown_param_name_ignored_and_bad_number_skipped(caplog):
    manifest = _make_manifest([OutputSpec(param_name="total", kind="number")])
    res = _gh_response(
        [
            ("RH_OUT:unknown_param", [{"type": "System.String", "data": json.dumps("whatever")}]),
            (
                "RH_OUT:total",
                [
                    {"type": "System.String", "data": json.dumps("abc")},
                    {"type": "System.Double", "data": "5.0"},
                ],
            ),
        ]
    )
    with caplog.at_level("WARNING", logger="hoger.results"):
        result = parse(res, manifest)
    assert result == {"total": [5.0]}
    assert any("hoger.results" == r.name for r in caplog.records)


# ── write_result_3dm ──────────────────────────────────────────────────


def test_write_mesh_and_string_roundtrip(tmp_path):
    manifest = _make_manifest(
        [OutputSpec(param_name="Mesh", kind="geometry"), OutputSpec(param_name="report", kind="string")]
    )
    mesh = rhino3dm.Mesh()
    mesh.Vertices.Add(0, 0, 0)
    mesh.Vertices.Add(1, 0, 0)
    mesh.Vertices.Add(0, 1, 0)
    mesh.Faces.AddFace(0, 1, 2)
    outputs = {"Mesh": [mesh], "report": ["總輻射 123 kWh"]}

    path = write_result_3dm(outputs, manifest, out_dir=tmp_path)
    assert path is not None

    from pathlib import Path

    p = Path(path)
    assert p.is_absolute()
    assert p.exists()
    assert p.parent == tmp_path

    f = rhino3dm.File3dm.Read(str(p))
    assert len(f.Objects) == 1
    obj = f.Objects[0]
    assert isinstance(obj.Geometry, rhino3dm.Mesh)
    assert obj.Attributes.GetUserString("report") == "總輻射 123 kWh"


def test_write_no_geometry_uses_origin_point_for_usertext(tmp_path):
    manifest = _make_manifest([OutputSpec(param_name="report", kind="string")])
    outputs = {"report": ["hello"]}

    path = write_result_3dm(outputs, manifest, out_dir=tmp_path)
    assert path is not None

    f = rhino3dm.File3dm.Read(path)
    assert len(f.Objects) == 1
    obj = f.Objects[0]
    assert isinstance(obj.Geometry, rhino3dm.Point)
    pt = obj.Geometry.Location
    assert (pt.X, pt.Y, pt.Z) == (0.0, 0.0, 0.0)
    assert obj.Attributes.GetUserString("report") == "hello"


def test_write_multi_value_string_is_json_list(tmp_path):
    manifest = _make_manifest([OutputSpec(param_name="notes", kind="string")])
    outputs = {"notes": ["a", "b", "中文"]}

    path = write_result_3dm(outputs, manifest, out_dir=tmp_path)
    f = rhino3dm.File3dm.Read(path)
    obj = f.Objects[0]
    raw = obj.Attributes.GetUserString("notes")
    assert json.loads(raw) == ["a", "b", "中文"]


def test_write_all_empty_outputs_returns_none_and_no_file(tmp_path):
    manifest = _make_manifest(
        [OutputSpec(param_name="Mesh", kind="geometry"), OutputSpec(param_name="report", kind="string")]
    )
    outputs = {"Mesh": [], "report": []}

    path = write_result_3dm(outputs, manifest, out_dir=tmp_path)
    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_write_uses_tmp_path_out_dir_not_config_results_dir(tmp_path, monkeypatch):
    import hoger.config as config

    # sanity: make config.RESULTS_DIR point somewhere else so we can assert
    # the written file does NOT land there.
    other_dir = tmp_path / "should_not_be_used"
    other_dir.mkdir()
    monkeypatch.setattr(config, "RESULTS_DIR", other_dir)

    manifest = _make_manifest([OutputSpec(param_name="report", kind="string")])
    outputs = {"report": ["x"]}

    write_dir = tmp_path / "actual_out"
    write_dir.mkdir()
    path = write_result_3dm(outputs, manifest, out_dir=write_dir)

    from pathlib import Path

    assert Path(path).parent == write_dir
    assert list(other_dir.iterdir()) == []
