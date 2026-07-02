"""
tests/test_trees.py — hoger.core.trees 的單元測試。

trees.py 負責把使用者參數序列化成 Rhino.Compute `/grasshopper` 端點需要的
DataTree payload（純 dict，不依賴 compute_rhino3d.Grasshopper.DataTree）。

每一條序列化規則都是 v1.0.3 生產環境踩坑驗證過的，錯一條就是靜默失敗
（GH 元件收到 null、Ladybug 默默跳過分析），因此每條規則都有專屬測試鎖住：

- bool 判斷必須在 int 之前（isinstance(True, int) 為 True）
- bool -> 小寫字串 "true"/"false"（大寫 "True" 會讓 .NET JToken.ToString() crash）
- 整數值的 float（如 18.0）-> System.Int32（GH Integer input ReadAsInt32("18.0") 會 crash）
- 字串 -> json.dumps 二次編碼（.ToString() 後必須是合法 JSON 字面值）
- 幾何 -> net_type_for() + Encode() 的 json.dumps
- encoded_tree -> 原樣 passthrough，不 decode/re-encode（Brep 往返可能損壞）
"""

import json

import pytest
import rhino3dm

from hoger.core import trees


def _make_brep():
    bbox = rhino3dm.BoundingBox(rhino3dm.Point3d(0, 0, 0), rhino3dm.Point3d(10, 10, 10))
    box = rhino3dm.Box(bbox)
    return rhino3dm.Brep.CreateFromBox(box)


# ── tree shape ───────────────────────────────────────────────────────


def test_tree_shape():
    t = trees.scalar_tree("_run", True)
    assert t["ParamName"] == "_run"
    assert set(t.keys()) == {"ParamName", "InnerTree"}
    assert set(t["InnerTree"].keys()) == {"{0}"}
    assert isinstance(t["InnerTree"]["{0}"], list)


# ── scalar_tree: bool ────────────────────────────────────────────────


def test_bool_true_is_lowercase_string():
    item = trees.scalar_tree("_run", True)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Boolean", "data": "true"}


def test_bool_false_is_lowercase_string():
    item = trees.scalar_tree("_run", False)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Boolean", "data": "false"}


def test_bool_checked_before_int():
    # True 是 isinstance(int) 也成立，必須先被 bool 分支攔截，不會變 System.Int32
    item = trees.scalar_tree("_flag", True)["InnerTree"]["{0}"][0]
    assert item["type"] == "System.Boolean"
    assert item["data"] == "true"


# ── scalar_tree: int / whole float ──────────────────────────────────


def test_whole_float_becomes_int32():
    item = trees.scalar_tree("_month", 18.0)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Int32", "data": 18}


def test_int_is_int32():
    item = trees.scalar_tree("_count", 3)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Int32", "data": 3}


def test_negative_whole_float_becomes_int32():
    item = trees.scalar_tree("_offset", -5.0)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Int32", "data": -5}


def test_zero_float_becomes_int32():
    item = trees.scalar_tree("_zero", 0.0)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Int32", "data": 0}


# ── scalar_tree: true float ──────────────────────────────────────────


def test_true_float_is_double():
    item = trees.scalar_tree("_gs", 1.5)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Double", "data": 1.5}


def test_negative_true_float_is_double():
    item = trees.scalar_tree("_gs", -2.75)["InnerTree"]["{0}"][0]
    assert item == {"type": "System.Double", "data": -2.75}


# ── string_tree ───────────────────────────────────────────────────────


def test_string_double_encoded():
    t = trees.string_tree("_epw", r"C:\weather\taipei.epw")
    assert t["InnerTree"]["{0}"][0] == {
        "type": "System.String",
        "data": json.dumps(r"C:\weather\taipei.epw"),
    }


def test_string_with_unicode():
    value = "台北氣象站"
    t = trees.string_tree("_city", value)
    item = t["InnerTree"]["{0}"][0]
    assert item["type"] == "System.String"
    # 往返：json.loads(data) 必須還原成原字串
    assert json.loads(item["data"]) == value


def test_string_tree_shape():
    t = trees.string_tree("_name", "hello")
    assert t["ParamName"] == "_name"
    assert t["InnerTree"]["{0}"][0]["data"] == json.dumps("hello")


# ── geometry_tree ─────────────────────────────────────────────────────


def test_geometry_uses_net_type_and_encoded_json():
    brep = _make_brep()
    item = trees.geometry_tree("_geometry", [brep])["InnerTree"]["{0}"][0]
    assert item["type"] == "Rhino.Geometry.Brep"
    decoded = json.loads(item["data"])  # data 必須是合法 JSON 字串
    assert "archive3dm" in decoded or "data" in decoded


def test_geometry_mesh_type():
    mesh = rhino3dm.Mesh()
    item = trees.geometry_tree("_geometry", [mesh])["InnerTree"]["{0}"][0]
    assert item["type"] == "Rhino.Geometry.Mesh"
    decoded = json.loads(item["data"])
    assert "archive3dm" in decoded or "data" in decoded


def test_geometry_multiple_objects():
    brep = _make_brep()
    mesh = rhino3dm.Mesh()
    items = trees.geometry_tree("_geometry", [brep, mesh])["InnerTree"]["{0}"]
    assert len(items) == 2
    assert items[0]["type"] == "Rhino.Geometry.Brep"
    assert items[1]["type"] == "Rhino.Geometry.Mesh"


def test_geometry_tree_shape():
    brep = _make_brep()
    t = trees.geometry_tree("_geometry", [brep])
    assert t["ParamName"] == "_geometry"
    assert set(t["InnerTree"].keys()) == {"{0}"}


# ── encoded_tree ─────────────────────────────────────────────────────


def test_encoded_tree_str_passthrough_no_reencode():
    raw = '{"version":10070,"archive3dm":70,"opennurbs":0,"data":"...abc"}'
    item = trees.encoded_tree("_geometry", [raw])["InnerTree"]["{0}"][0]
    assert item == {"type": "Rhino.Geometry.GeometryBase", "data": raw}


def test_encoded_tree_dict_with_type_and_data_passthrough():
    pre_wrapped = {"type": "Rhino.Geometry.Mesh", "data": "already-json-string"}
    item = trees.encoded_tree("_geometry", [pre_wrapped])["InnerTree"]["{0}"][0]
    # 現行實作保證不複製——斷更強的不變量（同一物件原樣轉發）
    assert item is pre_wrapped
    assert item == {"type": "Rhino.Geometry.Mesh", "data": "already-json-string"}


def test_encoded_tree_plain_dict_wrapped():
    encoded_dict = {"version": 10070, "archive3dm": 70, "opennurbs": 0, "data": "...abc"}
    item = trees.encoded_tree("_geometry", [encoded_dict])["InnerTree"]["{0}"][0]
    assert item["type"] == "Rhino.Geometry.GeometryBase"
    assert json.loads(item["data"]) == encoded_dict


def test_encoded_tree_multiple_mixed_items():
    raw_str = '{"archive3dm":70,"data":"str-item"}'
    plain_dict = {"archive3dm": 70, "data": "dict-item"}
    pre_wrapped = {"type": "Rhino.Geometry.Brep", "data": "brep-data"}

    items = trees.encoded_tree("_geometry", [raw_str, plain_dict, pre_wrapped])["InnerTree"]["{0}"]

    assert items[0] == {"type": "Rhino.Geometry.GeometryBase", "data": raw_str}
    assert items[1]["type"] == "Rhino.Geometry.GeometryBase"
    assert json.loads(items[1]["data"]) == plain_dict
    assert items[2] == pre_wrapped


def test_encoded_tree_shape():
    t = trees.encoded_tree("_geometry", ["{}"])
    assert t["ParamName"] == "_geometry"
    assert set(t["InnerTree"].keys()) == {"{0}"}


def test_encoded_tree_rejects_unexpected_type():
    # 非 dict/str 的項目必須在邊界炸開，不能靜默產生壞 payload
    with pytest.raises(TypeError, match="unsupported entry type int"):
        trees.encoded_tree("_x", [1])
