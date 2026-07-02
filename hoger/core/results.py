"""
hoger/core/results.py — /grasshopper 回應解析 + AttributeUserText .3dm 輸出。

HOGER 的核心設計約束：所有文字輸出必須以 Rhino AttributeUserText
（ObjectAttributes.SetUserString）附著在幾何物件上，不裸傳字串，確保
跨環境（不同 MCP client、不同顯示層）不遺失。

資料流：

    Rhino.Compute /grasshopper 回應
        --parse()-->            {output.param_name: list}（依 ToolManifest.outputs）
        --write_result_3dm()--> .3dm 檔（geometry 物件 + UserText）

parse() 改寫自
`C:\\Users\\User\\Desktop\\rhino.compute.test\\v1.0.3\\compute_core\\compute_core.py`
的 `parse_outputs()`，但輸出一律為 list（同名多值全部保留、不覆蓋），
而不是單一預設值 + 累加（原版只有 brep/mesh 是 list，number/string 是覆蓋式
的單一值）。

用 logging（logger name "hoger.results"），不 print，理由同 compute_client.py：
之後 MCP stdio 模式 stdout 會被 JSON-RPC 佔用。
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import rhino3dm

from hoger.core.manifest import ToolManifest

logger = logging.getLogger("hoger.results")


# ── parse ────────────────────────────────────────────────────────────


def _branch_sort_key(branch_key: str):
    """
    GH DataTree branch key（如 "{0}"、"{0;1}"、"{10}"）-> 可排序的數字 tuple。

    確保多分支（"{0}", "{1}", ..., "{10}"）依真實分支順序排列，而不是字典序
    （字典序會把 "{10}" 排在 "{2}" 之前）。無法解析為數字時 fallback 為原字串，
    不 crash。
    """
    nums = re.findall(r"\d+", branch_key)
    if nums:
        return (0, tuple(int(n) for n in nums))
    return (1, branch_key)


def _strip_rh_out_prefix(name: str) -> str:
    if name.startswith("RH_OUT:"):
        return name[len("RH_OUT:") :]
    return name


def _parse_item(kind: str, item: dict, param_name: str):
    """解析單一 InnerTree item -> 解析後的值，或 None（代表跳過）。"""
    raw = item.get("data")
    if raw is None:
        return None

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = raw
    else:
        parsed = raw

    if kind == "number":
        try:
            return float(parsed)
        except (TypeError, ValueError):
            logger.warning(
                "hoger.results: 無法將 %s 的值解析為 number: %r", param_name, parsed
            )
            return None
    elif kind == "integer":
        try:
            return int(parsed)
        except (TypeError, ValueError):
            logger.warning(
                "hoger.results: 無法將 %s 的值解析為 integer: %r", param_name, parsed
            )
            return None
    elif kind == "string":
        return str(parsed)
    elif kind == "geometry":
        obj = rhino3dm.CommonObject.Decode(parsed)
        if obj is None:
            logger.warning(
                "hoger.results: 無法 Decode %s 的 geometry 資料", param_name
            )
            return None
        return obj
    else:
        # 未知 kind：以字串處理，不 crash
        return str(parsed)


def parse(res: Optional[dict], manifest: ToolManifest) -> dict:
    """
    /grasshopper 回應 -> {output.param_name: list}。

    每個 manifest.outputs 的 param_name 保證有 key（無資料 -> 空 list）。
    res 為 None 或缺 "values" -> 全部空 list，並記一筆 warning。
    res["values"] 中不屬於 manifest.outputs 的 ParamName 一律忽略。
    """
    results: dict = {o.param_name: [] for o in manifest.outputs}
    kind_by_name = {o.param_name: o.kind for o in manifest.outputs}

    if not res or "values" not in res:
        logger.warning(
            "hoger.results: /grasshopper 回應為空或缺少 'values'，manifest=%s",
            manifest.id,
        )
        return results

    for value in res.get("values", []) or []:
        param_name = _strip_rh_out_prefix(value.get("ParamName", ""))
        kind = kind_by_name.get(param_name)
        if kind is None:
            continue  # 未知 ParamName，忽略

        inner_tree = value.get("InnerTree", {}) or {}
        for branch_key in sorted(inner_tree.keys(), key=_branch_sort_key):
            items = inner_tree[branch_key]
            if not items:
                continue
            for item in items:
                parsed_value = _parse_item(kind, item, param_name)
                if parsed_value is not None:
                    results[param_name].append(parsed_value)

    return results


# ── write_result_3dm ─────────────────────────────────────────────────


def _user_text_value(values: list) -> Optional[str]:
    """string kind 的 list -> UserText 字串值（len==0 -> None，略過該 param）。"""
    if len(values) == 0:
        return None
    if len(values) == 1:
        return str(values[0])
    return json.dumps(values, ensure_ascii=False)


def write_result_3dm(
    outputs: dict, manifest: ToolManifest, out_dir=None
) -> Optional[str]:
    """
    parse() 的輸出 -> .3dm 檔案。回傳絕對路徑字串；無 geometry 且無字串輸出時
    回傳 None（不寫檔）。

    所有幾何物件都帶上全部的 string UserText（不分別附著在特定物件上）——
    符合 HOGER 的設計：文字結果必須能在任一被選取的幾何上讀到。
    無 geometry 但有 string 輸出時，建立原點 Point 物件承載 UserText。
    """
    if out_dir is None:
        from hoger.config import RESULTS_DIR as out_dir  # 延遲 import，方便測試 monkeypatch

    kind_by_name = {o.param_name: o.kind for o in manifest.outputs}

    geometry_objects: list = []
    string_user_text: dict = {}

    for param_name, values in outputs.items():
        kind = kind_by_name.get(param_name)
        if kind == "geometry":
            geometry_objects.extend(values)
        elif kind == "string":
            text_value = _user_text_value(values)
            if text_value is not None:
                string_user_text[param_name] = text_value

    if not geometry_objects and not string_user_text:
        return None

    file3dm = rhino3dm.File3dm()

    def _make_attributes() -> "rhino3dm.ObjectAttributes":
        attrs = rhino3dm.ObjectAttributes()
        for param_name, text_value in string_user_text.items():
            attrs.SetUserString(param_name, text_value)
        return attrs

    if geometry_objects:
        for geo in geometry_objects:
            file3dm.Objects.Add(geo, _make_attributes())
    else:
        origin = rhino3dm.Point(rhino3dm.Point3d(0, 0, 0))
        file3dm.Objects.Add(origin, _make_attributes())

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{manifest.id}_{timestamp}.3dm"
    out_path = (out_dir / filename).resolve()

    file3dm.Write(str(out_path), 7)

    return str(out_path)
