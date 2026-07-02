"""
hoger/core/trees.py — Rhino.Compute DataTree 序列化。

把使用者參數轉成 Rhino.Compute `/grasshopper` 端點需要的 DataTree payload。
刻意不依賴 `compute_rhino3d` 的 DataTree 類別（避免其 stdout/patch 問題）——
這裡的 tree 直接是純 dict：

    {"ParamName": <名稱>, "InnerTree": {"{0}": [items...]}}

compute_client.evaluate(gh_path, tree_payloads) 已接受這種 dict 列表。

以下每一條序列化規則都是 v1.0.3 生產環境踩坑驗證過的（移植自
`C:\\Users\\User\\Desktop\\rhino.compute.test\\v1.0.3\\compute_core\\compute_core.py`），
錯一條就是靜默失敗（GH 元件收到 null、Ladybug 默默跳過分析）：

1. bool -> 小寫字串 "true"/"false"。
   JSON 的 True 經過 .NET JToken.ToString() 會變成大寫 "True"，
   GH Boolean input 解析大寫字串會 crash，故一律送小寫字串。
   注意：isinstance(True, int) 為 True，因此 bool 判斷必須寫在 int 判斷之前。

2. 整數值的 float（例如 18.0）-> System.Int32。
   C# 端 NumericStepper 傳來的整數值永遠是 float。若原樣送 System.Double
   "18.0"，GH Integer input 呼叫 ReadAsInt32("18.0") 會 crash。
   因此 value 為整數值（含 float 18.0）一律送 System.Int32 + int data。

3. int -> System.Int32；其餘 float（真正有小數，如 1.5）-> System.Double。

4. 字串 -> json.dumps 二次編碼。
   Rhino.Compute 的兩層 JSON 解碼規則：data 經過 .ToString() 之後必須仍是
   合法的 JSON 字面值。Windows 路徑用 json.dumps 會正確跳脫反斜線。

5. 幾何物件 -> net_type_for(type(obj).__name__) 當作 type，
   json.dumps(obj.Encode()) 當作 data。

6. encoded_tree：已編碼項目原樣 passthrough，不 decode/re-encode
   （rhino3dm Encode/Decode 往返對某些 Brep 會損壞資料，Hops solve 路徑
   必須原樣轉發）：
   - dict 且同時含 "type" 與 "data" -> 視為已組好的 item，原樣放入
   - dict（rhino3dm Encode() 產物，例如含 archive3dm）-> 包成
     {"type": "Rhino.Geometry.GeometryBase", "data": json.dumps(item)}
   - str（已經是 JSON 字串）-> 包成
     {"type": "Rhino.Geometry.GeometryBase", "data": item}（字串原樣，不再包一層）
"""

import json

from hoger.core.type_mapping import net_type_for

_GEOMETRY_BASE_TYPE = "Rhino.Geometry.GeometryBase"


def _wrap(param_name: str, items: list) -> dict:
    return {"ParamName": param_name, "InnerTree": {"{0}": items}}


def scalar_tree(param_name: str, value) -> dict:
    """
    bool / int / float -> DataTree。

    bool 判斷必須在 int 判斷之前（isinstance(True, int) 為 True）。
    """
    if isinstance(value, bool):
        item = {"type": "System.Boolean", "data": "true" if value else "false"}
    elif isinstance(value, float) and value == int(value):
        item = {"type": "System.Int32", "data": int(value)}
    elif isinstance(value, int):
        item = {"type": "System.Int32", "data": value}
    else:
        item = {"type": "System.Double", "data": float(value)}

    return _wrap(param_name, [item])


def string_tree(param_name: str, value: str) -> dict:
    """字串（含 Windows 路徑、中文）-> DataTree。data 用 json.dumps 二次編碼。"""
    item = {"type": "System.String", "data": json.dumps(value)}
    return _wrap(param_name, [item])


def geometry_tree(param_name: str, objects: list) -> dict:
    """rhino3dm 幾何物件列表 -> DataTree。type 用 net_type_for，data 用 Encode() 的 JSON。"""
    items = [
        {"type": net_type_for(type(obj).__name__), "data": json.dumps(obj.Encode())}
        for obj in objects
    ]
    return _wrap(param_name, items)


def encoded_tree(param_name: str, encoded_list: list) -> dict:
    """
    已編碼項目列表 -> DataTree，原樣 passthrough，不 decode/re-encode。

    每項可以是：
    - dict 且含 "type" + "data"：視為已組好的 item，原樣放入
    - dict（rhino3dm Encode() 產物）：包成 GeometryBase item，data 用 json.dumps
    - str（已是 JSON 字串）：包成 GeometryBase item，data 原樣（不再包一層）

    其他型別（int、list、None...）一律 raise TypeError——本模組的宗旨是
    錯誤要在邊界炸開，不能靜默產生壞 payload 讓 GH 元件收到 null。
    """
    items = []
    for entry in encoded_list:
        if isinstance(entry, dict) and "type" in entry and "data" in entry:
            # 「同時含 "type"+"data"」不會誤判 rhino3dm Encode() 產物：
            # 實測 rhino3dm 8.17.0 的 Brep/Mesh/NurbsCurve/Point/Extrusion
            # .Encode().keys() 全部恰為 {version, archive3dm, opennurbs, data}
            # ——含 "data" 但不含 "type"，故不碰撞，安全落到下一分支。
            items.append(entry)
        elif isinstance(entry, dict):
            items.append({"type": _GEOMETRY_BASE_TYPE, "data": json.dumps(entry)})
        elif isinstance(entry, str):
            items.append({"type": _GEOMETRY_BASE_TYPE, "data": entry})
        else:
            raise TypeError(
                f"encoded_tree: unsupported entry type {type(entry).__name__}; "
                f"expected dict or str"
            )

    return _wrap(param_name, items)
