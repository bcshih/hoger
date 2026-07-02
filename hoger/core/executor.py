"""
hoger/core/executor.py — 參數 -> DataTree -> Rhino.Compute 執行 -> ToolResult。

HOGER Phase 2 的整合層，串起既有模組：

    使用者參數 (dict)
        --build_trees()-->      DataTree payload 列表（hoger.core.trees）
        --compute_client.evaluate()--> Rhino.Compute /grasshopper 回應
        --results.parse()-->    {output.param_name: list}
        --results.write_result_3dm()--> .3dm 檔（geometry + string UserText）
        -->                     ToolResult（JSON-safe outputs + 診斷資訊）

呼叫端有二：MCP server 的 `tools/call`（Task 4.1）與 FastAPI 的
`/api/tools/{id}/run`（Task 3.2）。兩者都應該：
- 讓 ToolArgError 往外拋，轉成各自協定的錯誤格式（4xx / MCP isError）。
- run_tool() 內部的 Rhino.Compute 失敗（ComputeError）不 crash，
  而是回傳帶 errors 的 ToolResult，讓呼叫端決定如何呈現給使用者。

用 logging（logger name "hoger.executor"），不 print，理由同其他 core 模組：
之後 MCP stdio 模式 stdout 會被 JSON-RPC 佔用。
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import rhino3dm

from hoger.core import compute_client, results
from hoger.core.compute_client import ComputeError
from hoger.core.manifest import InputSpec, ToolManifest
from hoger.core.trees import encoded_tree, geometry_tree, scalar_tree, string_tree

logger = logging.getLogger("hoger.executor")


class ToolArgError(ValueError):
    """使用者參數錯誤（缺 required、型別錯、幾何載入失敗）——呼叫端回 4xx / MCP isError。"""


@dataclass
class ToolResult:
    outputs: dict
    result_3dm: Optional[str]
    elapsed_ms: int
    errors: list
    warnings: list
    modelunits: Optional[str]
    raw: Optional[dict]


# ── build_trees ──────────────────────────────────────────────────────


def _coerce_boolean(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ToolArgError(
        f"invalid value for boolean parameter: {value!r} "
        f"(expected bool or 'true'/'false')"
    )


def _build_scalar_tree(spec: InputSpec, value) -> dict:
    kind = spec.kind

    if kind == "number":
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ToolArgError(
                f"invalid value for number parameter {spec.param_name!r}: {value!r}"
            ) from exc
        return scalar_tree(spec.param_name, num)

    if kind == "integer":
        try:
            num = int(value)
        except (TypeError, ValueError) as exc:
            raise ToolArgError(
                f"invalid value for integer parameter {spec.param_name!r}: {value!r}"
            ) from exc
        return scalar_tree(spec.param_name, num)

    if kind == "boolean":
        try:
            flag = _coerce_boolean(value)
        except ToolArgError as exc:
            raise ToolArgError(
                f"invalid value for boolean parameter {spec.param_name!r}: {value!r}"
            ) from exc
        return scalar_tree(spec.param_name, flag)

    if kind == "string":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ToolArgError(
                f"invalid value for string parameter {spec.param_name!r}: {value!r}"
            )
        return string_tree(spec.param_name, str(value))

    raise ToolArgError(f"unsupported kind {kind!r} for parameter {spec.param_name!r}")


def _load_geometry_from_3dm(path: str, layer: Optional[str] = None) -> list:
    """
    讀取 .3dm 檔案，回傳其中的 geometry 物件列表。

    layer 指定時只回傳該圖層的物件；layer 名稱不存在於檔案中 -> ToolArgError。
    """
    file3dm = rhino3dm.File3dm.Read(path)
    if file3dm is None:
        raise ToolArgError(f"failed to read 3dm file: {path!r}")

    layer_index = None
    if layer is not None:
        for lyr in file3dm.Layers:
            if lyr.Name == layer:
                layer_index = lyr.Index
                break
        if layer_index is None:
            raise ToolArgError(f"layer {layer!r} not found in 3dm file: {path!r}")

    objects = []
    for obj in file3dm.Objects:
        if layer_index is not None and obj.Attributes.LayerIndex != layer_index:
            continue
        objects.append(obj.Geometry)

    return objects


def _build_geometry_tree(spec: InputSpec, value) -> dict:
    if not isinstance(value, dict):
        raise ToolArgError(
            f"invalid value for geometry parameter {spec.param_name!r}: expected object "
            f"with 'encoded' or 'file_3dm', got {value!r}"
        )

    encoded = value.get("encoded")
    if encoded:
        try:
            return encoded_tree(spec.param_name, encoded)
        except TypeError as exc:
            raise ToolArgError(
                f"invalid 'encoded' entry for geometry parameter {spec.param_name!r}: {exc}"
            ) from exc

    file_3dm = value.get("file_3dm")
    if file_3dm:
        layer = value.get("layer")
        if not os.path.exists(file_3dm):
            raise ToolArgError(
                f"file_3dm not found for geometry parameter {spec.param_name!r}: {file_3dm!r}"
            )
        objects = _load_geometry_from_3dm(file_3dm, layer)
        if not objects and spec.required:
            layer_info = f" (layer={layer!r})" if layer else ""
            raise ToolArgError(
                f"geometry parameter {spec.param_name!r} is required but no objects "
                f"were loaded from {file_3dm!r}{layer_info}"
            )
        return geometry_tree(spec.param_name, objects)

    raise ToolArgError(
        f"geometry parameter {spec.param_name!r} requires either 'encoded' or 'file_3dm'"
    )


def build_trees(manifest: ToolManifest, args: dict) -> list:
    """
    使用者參數 -> DataTree payload 列表（順序照 manifest.inputs）。

    args 中不在 manifest.inputs 的 key 會被記錄一筆 warning 並忽略。
    """
    trees = []
    known_params = {spec.param_name for spec in manifest.inputs}

    for spec in manifest.inputs:
        value = args.get(spec.param_name)

        if value is None:
            if spec.default is not None:
                value = spec.default
            elif spec.required:
                raise ToolArgError(f"missing required parameter: {spec.param_name!r}")
            else:
                continue

        if spec.kind == "geometry":
            trees.append(_build_geometry_tree(spec, value))
        else:
            trees.append(_build_scalar_tree(spec, value))

    for key in args:
        if key not in known_params:
            logger.warning(
                "hoger.executor: ignoring unknown argument %r not in manifest %s",
                key,
                manifest.id,
            )

    return trees


# ── run_tool ─────────────────────────────────────────────────────────


def _json_safe_outputs(parsed: dict, manifest: ToolManifest, result_3dm: Optional[str]) -> dict:
    kind_by_name = {o.param_name: o.kind for o in manifest.outputs}
    outputs: dict = {}
    for param_name, values in parsed.items():
        if kind_by_name.get(param_name) == "geometry":
            outputs[param_name] = {"count": len(values), "in_3dm": result_3dm is not None}
        else:
            outputs[param_name] = values
    return outputs


def run_tool(manifest: ToolManifest, args: dict, out_dir=None) -> ToolResult:
    """
    使用者參數 -> DataTree -> Rhino.Compute 執行 -> ToolResult。

    ToolArgError（build_trees 產生）往外拋——是呼叫端的參數錯誤。
    ComputeError（compute_client.evaluate 產生）不往外拋——回傳帶 errors 的
    ToolResult，讓呼叫端決定如何呈現給使用者（Compute 掛掉不該讓整個服務崩潰）。
    """
    trees = build_trees(manifest, args)

    t0 = time.perf_counter()
    try:
        res = compute_client.evaluate(manifest.gh_file, trees)
    except ComputeError as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return ToolResult(
            outputs={o.param_name: [] for o in manifest.outputs},
            result_3dm=None,
            elapsed_ms=elapsed_ms,
            errors=[str(exc)],
            warnings=[],
            modelunits=None,
            raw=None,
        )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    parsed = results.parse(res, manifest)
    result_3dm = results.write_result_3dm(parsed, manifest, out_dir)
    outputs = _json_safe_outputs(parsed, manifest, result_3dm)

    errors = res.get("errors", []) or []
    warnings = res.get("warnings", []) or []
    modelunits = res.get("modelunits")

    return ToolResult(
        outputs=outputs,
        result_3dm=result_3dm,
        elapsed_ms=elapsed_ms,
        errors=errors,
        warnings=warnings,
        modelunits=modelunits,
        raw=res,
    )
