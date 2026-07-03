"""
hoger/hops/hops_routes.py — Grasshopper Hops 元件直連端點（掛在 /hops prefix）。

Grasshopper 的 Hops 元件指向一個 URL 時：
1. GET  {url}       -> 期待 /io 相容的 JSON（描述 Inputs/Outputs），
                       Hops 據此生成元件參數。
2. POST {url}/solve -> body 為 {"pointer": ..., "values": [...]}，
                       期待 Grasshopper 風格回應 {"values": [...]}。

**關鍵規則（生產驗證過）**：Hops solve 送來的 InnerTree items 必須原樣
passthrough 給 Rhino.Compute，不 decode/re-encode（rhino3dm 往返會損壞
部分 Brep）。本模組不解析 body["values"] 的內容，只做最外層的型別檢查
（是否為 list），內容原封不動交給 executor.run_tool_raw()。

只有 status == "registered" 的工具才能透過本端點存取——draft 工具（尚未
確認參數/描述）不該讓外部的 Grasshopper 檔案連上。與 /api/tools/{id} 不同，
本端點對「查無工具」與「工具是 draft」一律回 404（不洩漏工具存在與否的
細節，且 Hops 端使用者看到的行為應一致：這個 URL 沒有可用的工具）。
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from hoger.config import TOOLS_DIR
from hoger.core import executor
from hoger.core.compute_client import ComputeError
from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest
from hoger.store import tool_store

router = APIRouter(prefix="/hops")
logger = logging.getLogger("hoger.hops")

# GH_Component 的 IGH_Param.AtMost 上限（Grasshopper 用 int.MaxValue 代表
# 「無上限」），/io 回應與手動編輯過的 manifest 都可能把它存成 None——
# 對外一律補回這個哨兵值，維持與 Rhino.Compute /io 原生格式一致。
_UNLIMITED = 2147483647

_KIND_TO_PARAM_TYPE = {
    "number": "Number",
    "integer": "Integer",
    "boolean": "Boolean",
    "string": "String",
    "geometry": "Geometry",
}


def _param_type_for(kind: str, param_type: str) -> str:
    """優先使用 manifest 原始 param_type；缺值時由 kind 反推 Hops 慣用名稱。"""
    if param_type:
        return param_type
    return _KIND_TO_PARAM_TYPE.get(kind, "String")


def _input_definition(spec: InputSpec) -> dict:
    entry: dict = {
        "Name": spec.param_name,
        "Nickname": spec.label or spec.param_name,
        "Description": spec.description,
        "ParamType": _param_type_for(spec.kind, spec.param_type),
        "AtLeast": spec.at_least,
        "AtMost": spec.at_most if spec.at_most is not None else _UNLIMITED,
    }
    if spec.default is not None:
        entry["Default"] = spec.default
    if spec.minimum is not None:
        entry["Minimum"] = spec.minimum
    if spec.maximum is not None:
        entry["Maximum"] = spec.maximum
    return entry


def _output_definition(spec: OutputSpec) -> dict:
    return {
        "Name": spec.param_name,
        "Nickname": spec.param_name,
        "Description": spec.description,
        "ParamType": _param_type_for(spec.kind, ""),
    }


def _get_registered_or_404(tool_id: str) -> ToolManifest:
    """
    查詢工具並確認 status == "registered"。draft 或不存在一律 404——
    見模組 docstring「只有 registered 工具才能透過本端點存取」。
    """
    try:
        manifest = tool_store.get(tool_id, tools_dir=TOOLS_DIR)
    except tool_store.ToolNotFound:
        raise HTTPException(status_code=404, detail=f"tool not found: {tool_id!r}")

    if manifest.status != "registered":
        raise HTTPException(status_code=404, detail=f"tool not found: {tool_id!r}")

    return manifest


@router.get("/{tool_id}")
def get_hops_definition(tool_id: str):
    """GET /hops/{tool_id} —— Hops 元件定義（/io 相容格式）。"""
    manifest = _get_registered_or_404(tool_id)

    return {
        "Description": manifest.description or manifest.display_name,
        "InputNames": [i.param_name for i in manifest.inputs],
        "OutputNames": [o.param_name for o in manifest.outputs],
        "Inputs": [_input_definition(i) for i in manifest.inputs],
        "Outputs": [_output_definition(o) for o in manifest.outputs],
    }


@router.post("/{tool_id}/solve")
async def post_hops_solve(tool_id: str, request: Request):
    """
    POST /hops/{tool_id}/solve —— Hops solve passthrough。

    body: {"pointer": ..., "values": [...]}（pointer 忽略，HOGER 每次都是
    無狀態的一次性求值，不支援 Rhino.Compute 的 cache pointer 機制）。

    values 原樣（未經任何 decode/re-encode）交給 executor.run_tool_raw()，
    其內部再原樣交給 compute_client.evaluate()——維持模組 docstring
    描述的 passthrough 規則。
    """
    manifest = _get_registered_or_404(tool_id)

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc

    if not isinstance(body, dict) or "values" not in body:
        raise HTTPException(status_code=400, detail="missing 'values' in request body")

    values = body["values"]
    if not isinstance(values, list):
        raise HTTPException(status_code=400, detail="'values' must be a list")

    try:
        result = executor.run_tool_raw(manifest, values)
    except ComputeError as exc:
        # run_tool_raw 本身不拋 ComputeError（由 executor 內部軟處理），
        # 這裡防禦性保留以防未來實作變動；目前實務上不會走到此分支。
        return JSONResponse(status_code=502, content={"errors": [str(exc)]})

    raw = result.raw or {}
    if "values" not in raw:
        # compute_client.evaluate 失敗時 raw 形狀是
        # {"error_status_code": ..., "error_body": ...}（見 executor.run_tool
        # docstring），此時 result.errors 已帶有可讀訊息。
        return JSONResponse(status_code=502, content={"errors": result.errors})

    return {
        "values": raw["values"],
        "errors": result.errors,
        "warnings": result.warnings,
    }
