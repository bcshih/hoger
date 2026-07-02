"""
hoger/core/manifest.py — 工具定義模型、/io 回應解析、MCP Tool Schema 產生。

HOGER 的資料流：

    Rhino.Compute /io 回應
        --manifest_from_io()-->  ToolManifest（唯一真相，之後存成 tools/*.json）
        --to_mcp_tool()-->       MCP Tool Schema

本模組刻意複用 hoger.core.type_mapping 的 classify()/to_json_schema()，
不重寫任何 schema 產生邏輯。InputSpec 的屬性名與 type_mapping 的鴨子型別
契約（param_name/kind/description/required/default/minimum/maximum/
enum_values）完全一致。

/io 回應的欄位在真實 Rhino.Compute 環境可能與 tests/fixtures 的手寫樣本有
出入，因此 manifest_from_io() 對每個欄位的讀取都是防禦性的（用 .get() +
預設值），缺欄位不會 crash。
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from hoger.core import type_mapping

# ── models ───────────────────────────────────────────────────────────


class InputSpec(BaseModel):
    param_name: str  # GH 原名，底線原樣保留（_geometry、context_）
    label: str = ""
    kind: str  # number|integer|boolean|string|geometry
    param_type: str = ""  # /io 原始 ParamType（保留供除錯）
    description: str = ""
    required: bool = True
    default: Any = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum_values: Optional[list[str]] = None
    at_least: int = 1
    at_most: Optional[int] = 1


class OutputSpec(BaseModel):
    param_name: str  # 去除 RH_OUT: 前綴後的名稱
    kind: str
    description: str = ""
    unit: str = ""


class ToolManifest(BaseModel):
    id: str
    display_name: str
    description: str = ""
    gh_file: str
    status: str = "draft"  # draft | registered
    inputs: list[InputSpec] = []
    outputs: list[OutputSpec] = []
    created_at: str  # ISO 8601
    updated_at: str


# ── id generation ────────────────────────────────────────────────────


def _slugify(stem: str) -> str:
    """
    檔名（不含副檔名）-> kebab-case id。

    規則：小寫化 -> 空白與底線變 '-' -> 移除非 [a-z0-9-] 字元
    -> 連續 '-' 合併 -> 去頭尾 '-'。
    """
    s = stem.lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    return s


# ── manifest_from_io ─────────────────────────────────────────────────


def _parse_input(raw: dict) -> InputSpec:
    name = raw.get("Name", "")
    nickname = raw.get("Nickname", "")
    label = nickname if nickname and nickname != name else ""

    param_type = raw.get("ParamType", "")
    kind = type_mapping.classify(param_type)

    at_least = raw.get("AtLeast", 1)
    at_most = raw.get("AtMost", 1)
    default = raw.get("Default", None)

    # GH 慣例：AtLeast 0（如 context_）代表選填；有 Default 也視為選填。
    required = at_least >= 1 and default is None

    return InputSpec(
        param_name=name,
        label=label,
        kind=kind,
        param_type=param_type,
        description=raw.get("Description", ""),
        required=required,
        default=default,
        minimum=raw.get("Minimum", None),
        maximum=raw.get("Maximum", None),
        at_least=at_least,
        at_most=at_most,
    )


def _parse_output(raw: dict) -> OutputSpec:
    name = raw.get("Name", "")
    if name.startswith("RH_OUT:"):
        name = name[len("RH_OUT:") :]

    param_type = raw.get("ParamType", "")
    kind = type_mapping.classify(param_type)

    return OutputSpec(
        param_name=name,
        kind=kind,
        description=raw.get("Description", ""),
    )


def manifest_from_io(gh_path: str, io_response: dict) -> ToolManifest:
    """
    Rhino.Compute /io 回應 -> ToolManifest。

    防禦性解析：io_response 缺 "Inputs"/"Outputs"/"Description" 等 key，
    或個別 Input/Output 項目只有部分欄位，一律用預設值填補，不 crash。
    """
    stem = Path(gh_path).stem
    tool_id = _slugify(stem)

    inputs = [_parse_input(raw) for raw in io_response.get("Inputs", []) or []]
    outputs = [_parse_output(raw) for raw in io_response.get("Outputs", []) or []]

    now = datetime.now(timezone.utc).isoformat()

    return ToolManifest(
        id=tool_id,
        display_name=stem,
        description=io_response.get("Description", ""),
        gh_file=gh_path,
        inputs=inputs,
        outputs=outputs,
        created_at=now,
        updated_at=now,
    )


# ── to_mcp_tool ──────────────────────────────────────────────────────


def to_mcp_tool(m: ToolManifest) -> dict:
    """ToolManifest -> MCP Tool Schema dict。複用 type_mapping.to_json_schema()。"""
    if m.description:
        description = f"{m.display_name} — {m.description}"
    else:
        description = m.display_name

    properties = {i.param_name: type_mapping.to_json_schema(i) for i in m.inputs}
    required = [i.param_name for i in m.inputs if i.required and i.default is None]

    input_schema: dict = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required

    return {
        "name": m.id,
        "description": description,
        "inputSchema": input_schema,
    }
