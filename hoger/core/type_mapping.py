"""
hoger/core/type_mapping.py — GH ParamType 分類與 JSON Schema 產生。

本模組負責三件事：
1. classify(): 把 Rhino.Compute `/io` 端點回傳的 `ParamType` 字串
   （例如 "Number", "Brep", "ValueList"）分類為 HOGER 內部使用的
   kind（number / integer / boolean / string / geometry）。
2. to_json_schema(): 把工具輸入定義轉成 MCP 需要的 JSON Schema property。
3. net_type_for(): 把 rhino3dm 型別名轉成 .NET 完整名稱
   （移植自 v1.0.3 compute_core.py 的對照表)。

注意：本模組刻意不 import hoger.core.manifest（下一個 task 才會建立），
避免循環相依。to_json_schema() 用鴨子型別接受任何具備
`param_name, kind, description, required, default, minimum, maximum,
enum_values` 屬性的物件（見 manifest.InputSpec，測試中以本地 stub 替代）。
"""

import logging

logger = logging.getLogger("hoger.type_mapping")

# ── classify ─────────────────────────────────────────────────────────

_NUMBER_TYPES = {"number"}
_INTEGER_TYPES = {"integer"}
_BOOLEAN_TYPES = {"boolean"}
_STRING_TYPES = {"string", "text", "filepath", "valuelist"}
_GEOMETRY_TYPES = {
    "geometry",
    "brep",
    "mesh",
    "curve",
    "surface",
    "point",
    "subd",
    "extrusion",
    "line",
    "circle",
    "arc",
    "rectangle",
    "box",
    "plane",
    "vector",
}


def classify(param_type: str) -> str:
    """
    把 /io 回傳的 ParamType 字串分類為內部 kind。

    大小寫不敏感。未知型別回傳 "string" 並記一筆 warning log。
    """
    key = param_type.lower()

    if key in _NUMBER_TYPES:
        return "number"
    if key in _INTEGER_TYPES:
        return "integer"
    if key in _BOOLEAN_TYPES:
        return "boolean"
    if key in _STRING_TYPES:
        return "string"
    if key in _GEOMETRY_TYPES:
        return "geometry"

    logger.warning("Unknown ParamType %r, falling back to kind 'string'", param_type)
    return "string"


# ── to_json_schema ───────────────────────────────────────────────────


def _add_common_fields(schema: dict, spec) -> None:
    """加上 description / default（所有 kind 共通）。"""
    if spec.description:
        schema["description"] = spec.description
    if spec.default is not None:
        schema["default"] = spec.default


def to_json_schema(spec) -> dict:
    """
    把工具輸入定義（鴨子型別，見模組 docstring）轉成 MCP JSON Schema property。
    """
    kind = spec.kind

    if kind == "number":
        schema: dict = {"type": "number"}
        _add_common_fields(schema, spec)
        if spec.minimum is not None:
            schema["minimum"] = spec.minimum
        if spec.maximum is not None:
            schema["maximum"] = spec.maximum
        return schema

    if kind == "integer":
        schema = {"type": "integer"}
        _add_common_fields(schema, spec)
        if spec.minimum is not None:
            schema["minimum"] = spec.minimum
        if spec.maximum is not None:
            schema["maximum"] = spec.maximum
        return schema

    if kind == "boolean":
        schema = {"type": "boolean"}
        _add_common_fields(schema, spec)
        return schema

    if kind == "string":
        schema = {"type": "string"}
        _add_common_fields(schema, spec)
        if spec.enum_values:
            schema["enum"] = list(spec.enum_values)
        return schema

    if kind == "geometry":
        schema = {
            "type": "object",
            "properties": {
                "file_3dm": {
                    "type": "string",
                    "description": "Rhino .3dm 檔案絕對路徑",
                },
                "layer": {
                    "type": "string",
                    "description": "（選填）只取此圖層的物件",
                },
                "encoded": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "（替代）rhino3dm JSON 編碼的幾何物件列表",
                },
            },
        }
        if spec.description:
            schema["description"] = spec.description
        if spec.default is not None:
            schema["default"] = spec.default
        return schema

    # 理論上 kind 一定是 classify() 產生的四種之一，這裡防禦性 fallback。
    logger.warning("Unknown kind %r for param %r, defaulting to string schema", kind, spec.param_name)
    schema = {"type": "string"}
    _add_common_fields(schema, spec)
    return schema


# ── net_type_for ─────────────────────────────────────────────────────

_NET_TYPE_NAMES = {
    "Brep",
    "Mesh",
    "Extrusion",
    "SubD",
    "Curve",
    "ArcCurve",
    "LineCurve",
    "NurbsCurve",
    "PolylineCurve",
    "Surface",
    "NurbsSurface",
    "Point",
}


def net_type_for(type_name: str) -> str:
    """
    把 rhino3dm 型別名轉成 .NET 完整名稱。

    對照表中的名稱與不在表中的名稱都回傳 f"Rhino.Geometry.{type_name}"
    ——目前保留對照表結構是為了未來若有型別對映到不同命名空間時方便擴充。
    """
    if type_name in _NET_TYPE_NAMES:
        return f"Rhino.Geometry.{type_name}"
    return f"Rhino.Geometry.{type_name}"
