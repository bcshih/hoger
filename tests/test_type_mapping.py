"""
tests/test_type_mapping.py — hoger.core.type_mapping 的單元測試。

type_mapping 負責三件事：
1. classify(): /io 的 ParamType 字串 → 內部 kind（number/integer/boolean/string/geometry）
2. to_json_schema(): 工具輸入定義（鴨子型別）→ MCP JSON Schema property
3. net_type_for(): rhino3dm 型別名 → .NET 完整名稱

to_json_schema 刻意不 import manifest.InputSpec（manifest.py 尚未建立），
改用本檔案內的 SpecStub dataclass 模擬鴨子型別介面。
"""

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from hoger.core.type_mapping import classify, net_type_for, to_json_schema


@dataclass
class SpecStub:
    """鴨子型別 stub，模擬未來 manifest.InputSpec 的介面。"""

    param_name: str
    kind: str
    description: str = ""
    required: bool = True
    default: Optional[Any] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum_values: Optional[list] = None


# ── classify ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pt,kind",
    [
        ("Number", "number"),
        ("Integer", "integer"),
        ("Boolean", "boolean"),
        ("String", "string"),
        ("Text", "string"),
        ("FilePath", "string"),
        ("Brep", "geometry"),
        ("Mesh", "geometry"),
        ("Curve", "geometry"),
        ("Geometry", "geometry"),
        ("Point", "geometry"),
        ("Surface", "geometry"),
        ("SubD", "geometry"),
        ("Extrusion", "geometry"),
        ("Line", "geometry"),
        ("Circle", "geometry"),
        ("Arc", "geometry"),
        ("Rectangle", "geometry"),
        ("Box", "geometry"),
        ("Plane", "geometry"),
        ("Vector", "geometry"),
        ("ValueList", "string"),
        ("number", "number"),  # 大小寫不敏感
        ("BREP", "geometry"),  # 大小寫不敏感
        ("UnknownXyz", "string"),  # fallback
    ],
)
def test_classify(pt, kind):
    assert classify(pt) == kind


def test_classify_unknown_logs_warning(caplog):
    with caplog.at_level("WARNING", logger="hoger.type_mapping"):
        result = classify("SomeWeirdType")
    assert result == "string"
    assert any("SomeWeirdType" in record.message for record in caplog.records)


def test_classify_known_type_does_not_log_warning(caplog):
    with caplog.at_level("WARNING", logger="hoger.type_mapping"):
        classify("Number")
    assert len(caplog.records) == 0


# ── to_json_schema: number / integer / boolean ──────────────────────


def test_number_schema_with_bounds():
    spec = SpecStub(
        param_name="_grid_size",
        kind="number",
        description="網格大小",
        default=1.0,
        minimum=0.1,
        maximum=50.0,
    )
    assert to_json_schema(spec) == {
        "type": "number",
        "description": "網格大小",
        "default": 1.0,
        "minimum": 0.1,
        "maximum": 50.0,
    }


def test_number_schema_plain_no_extras():
    spec = SpecStub(param_name="_x", kind="number")
    assert to_json_schema(spec) == {"type": "number"}


def test_integer_schema():
    spec = SpecStub(
        param_name="_count",
        kind="integer",
        description="次數",
        default=3,
        minimum=1,
        maximum=10,
    )
    assert to_json_schema(spec) == {
        "type": "integer",
        "description": "次數",
        "default": 3,
        "minimum": 1,
        "maximum": 10,
    }


def test_boolean_schema_with_default():
    spec = SpecStub(param_name="_run", kind="boolean", description="執行開關", default=False)
    assert to_json_schema(spec) == {
        "type": "boolean",
        "description": "執行開關",
        "default": False,
    }


def test_boolean_schema_plain():
    spec = SpecStub(param_name="_run", kind="boolean")
    assert to_json_schema(spec) == {"type": "boolean"}


# ── to_json_schema: string / enum ───────────────────────────────────


def test_string_schema_plain():
    spec = SpecStub(param_name="_name", kind="string")
    assert to_json_schema(spec) == {"type": "string"}


def test_string_schema_with_description_and_default():
    spec = SpecStub(param_name="_name", kind="string", description="名稱", default="foo")
    assert to_json_schema(spec) == {
        "type": "string",
        "description": "名稱",
        "default": "foo",
    }


def test_value_list_schema_has_enum():
    spec = SpecStub(
        param_name="_direction",
        kind="string",
        description="方向",
        enum_values=["north", "south"],
    )
    schema = to_json_schema(spec)
    assert schema["type"] == "string"
    assert schema["enum"] == ["north", "south"]
    assert schema["description"] == "方向"


def test_string_schema_no_enum_key_when_enum_values_empty():
    spec = SpecStub(param_name="_name", kind="string", enum_values=[])
    schema = to_json_schema(spec)
    assert "enum" not in schema


# ── to_json_schema: geometry ─────────────────────────────────────────


def test_geometry_schema_has_file_and_encoded():
    spec = SpecStub(param_name="_geometry", kind="geometry", description="要分析的幾何面")
    schema = to_json_schema(spec)

    assert schema["type"] == "object"
    assert set(schema["properties"].keys()) == {"file_3dm", "layer", "encoded"}
    assert schema["description"] == "要分析的幾何面"

    assert schema["properties"]["file_3dm"] == {
        "type": "string",
        "description": "Rhino .3dm 檔案絕對路徑",
    }
    assert schema["properties"]["layer"] == {
        "type": "string",
        "description": "（選填）只取此圖層的物件",
    }
    assert schema["properties"]["encoded"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "（替代）rhino3dm JSON 編碼的幾何物件列表",
    }


def test_geometry_schema_no_description_key_when_blank():
    spec = SpecStub(param_name="_geometry", kind="geometry")
    schema = to_json_schema(spec)
    assert "description" not in schema


def test_geometry_schema_no_minimum_maximum_keys():
    spec = SpecStub(param_name="_geometry", kind="geometry", minimum=0, maximum=100)
    schema = to_json_schema(spec)
    assert "minimum" not in schema
    assert "maximum" not in schema


# ── net_type_for ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "type_name,expected",
    [
        ("Brep", "Rhino.Geometry.Brep"),
        ("Mesh", "Rhino.Geometry.Mesh"),
        ("Extrusion", "Rhino.Geometry.Extrusion"),
        ("SubD", "Rhino.Geometry.SubD"),
        ("Curve", "Rhino.Geometry.Curve"),
        ("ArcCurve", "Rhino.Geometry.ArcCurve"),
        ("LineCurve", "Rhino.Geometry.LineCurve"),
        ("NurbsCurve", "Rhino.Geometry.NurbsCurve"),
        ("PolylineCurve", "Rhino.Geometry.PolylineCurve"),
        ("Surface", "Rhino.Geometry.Surface"),
        ("NurbsSurface", "Rhino.Geometry.NurbsSurface"),
        ("Point", "Rhino.Geometry.Point"),
    ],
)
def test_net_type_for_known(type_name, expected):
    assert net_type_for(type_name) == expected


def test_net_type_for_fallback():
    assert net_type_for("SomethingElse") == "Rhino.Geometry.SomethingElse"
