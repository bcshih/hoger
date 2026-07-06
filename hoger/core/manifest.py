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

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from hoger.core import type_mapping

logger = logging.getLogger("hoger.manifest")

# ── models ───────────────────────────────────────────────────────────


class InputSpec(BaseModel):
    """
    工具輸入定義。屬性名與 type_mapping 的鴨子型別契約完全一致。

    required 欄位是「單一真相」：其推導（AtLeast >= 1 且無 Default）只發生在
    manifest_from_io() 解析 /io 回應時。之後（UI 或手動編輯 tools/*.json）
    required 欄位本身即為權威值——to_mcp_tool() 只看這個欄位，不會再依
    default 重新推導。
    """

    param_name: str  # GH 原名，底線原樣保留（_geometry、context_）；v2 群組檔已剝除 RH_IN: 前綴的乾淨名
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
    # /io 原始 Name（v2 群組檔含 "RH_IN:" 前綴，注入 compute 時 ParamName 必須
    # 完全等於它）。None ⇒ 沿用 param_name（v1 行為：Name 本身即是注入用的名稱）。
    # 預設 None 保證既有 tools/*.json（無此欄位）反序列化相容。
    compute_name: Optional[str] = None


class OutputSpec(BaseModel):
    param_name: str  # 去除 RH_OUT: 前綴後的名稱
    kind: str
    description: str = ""
    unit: str = ""
    # /io 原始 Name（v2 群組檔含 "RH_OUT:" 前綴）。None ⇒ 沿用 param_name。
    # 預設 None 保證既有 tools/*.json 反序列化相容。
    compute_name: Optional[str] = None


class ToolManifest(BaseModel):
    id: str
    display_name: str
    description: str = ""
    # 自動生成的完整說明（hoger.core.describe.build_auto_doc()）。空字串 ⇒
    # 尚未生成或轉換時 scan 失敗被跳過。預設空字串保證既有 tools/*.json
    # （無此欄位）反序列化相容。to_mcp_tool() 會把非空的 auto_doc 附加到
    # description 後面（見該函式），這是 AI 調用端實際看到的內容。
    auto_doc: str = ""
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

    注意：非拉丁字元（中文、日文等）會被移除。若移除後結果為空字串
    （例如檔名全為非 ASCII 字元），fallback 為 "tool-" + 原始 stem 的
    SHA-1 前 8 碼（十六進位），保證 id 非空且對同一輸入穩定。
    """
    s = stem.lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    if not s:
        return f"tool-{hashlib.sha1(stem.encode('utf-8')).hexdigest()[:8]}"
    return s


# ── manifest_from_io ─────────────────────────────────────────────────


_RH_IN_PREFIX = "RH_IN:"
_RH_OUT_PREFIX = "RH_OUT:"


def _str(raw: dict, key: str, default: str = "") -> str:
    """
    dict.get(key, default) 只在 key 完全不存在時才用 default——key 存在但
    值明確是 None（真實 Rhino.Compute /io 回應對未填寫的欄位會這樣做，
    例如未填 Description 的參數回傳 "Description": null，而不是省略該
    key）時會原樣回傳 None，讓下游的 .startswith()/.lower()/Pydantic
    的 str 型別驗證炸掉。這裡用 `or default` 同時擋兩種情況。
    """
    return raw.get(key) or default


def _split_name(name: str, prefix: str) -> tuple[str, Optional[str]]:
    """
    /io 原始 Name -> (param_name, compute_name)。

    Name 以 prefix（"RH_IN:"/"RH_OUT:"）開頭 -> 剝除前綴當 param_name、
    原樣 Name 當 compute_name。剝除後為空字串（Name 恰好等於 prefix）時，
    param_name 改用 _slugify(compute_name) 當 fallback，保證非空
    （複用既有 slugify + hash fallback，見 _slugify）。

    否則（v1 行為，無前綴）：param_name=Name、compute_name=None。
    """
    if name.startswith(prefix):
        stripped = name[len(prefix) :]
        param_name = stripped if stripped else _slugify(name)
        return param_name, name
    return name, None


def _parse_default(raw_default: Any, param_name: str) -> Any:
    """
    /io 回應的 Default 欄位 -> 實際預設值。

    v1（裸值）：原樣回傳。
    v2（群組檔，DataTree 形）：dict 且含 "InnerTree" -> 取第一個 branch 的
    第一個 item 的 "data"；data 是字串時嘗試 json.loads（例如 "3.0" ->
    3.0、"true" -> True），失敗則保留原字串。

    任何步驟出錯（形狀不對、branch/items 為空等）-> 回傳 None 並記
    warning，不 crash、不讓整個 manifest_from_io 失敗。
    """
    if not isinstance(raw_default, dict) or "InnerTree" not in raw_default:
        return raw_default

    try:
        inner_tree = raw_default["InnerTree"]
        first_branch_key = next(iter(inner_tree))
        first_item = inner_tree[first_branch_key][0]
        data = first_item["data"]
        if isinstance(data, str):
            try:
                return json.loads(data)
            except (TypeError, ValueError):
                return data
        return data
    except (StopIteration, IndexError, KeyError, TypeError) as exc:
        logger.warning(
            "hoger.manifest: 無法解析 %s 的 DataTree 形 Default: %r (%s)",
            param_name,
            raw_default,
            exc,
        )
        return None


def _parse_input(raw: dict) -> InputSpec:
    name = _str(raw, "Name")
    param_name, compute_name = _split_name(name, _RH_IN_PREFIX)

    nickname = _str(raw, "Nickname")
    label = nickname if nickname and nickname != name else ""

    param_type = _str(raw, "ParamType")
    kind = type_mapping.classify(param_type)

    at_least = raw.get("AtLeast", 1)
    at_most = raw.get("AtMost", 1)
    default = _parse_default(raw.get("Default", None), param_name)

    # GH 慣例：AtLeast 0（如 context_）代表選填；有 Default 也視為選填。
    required = at_least >= 1 and default is None

    return InputSpec(
        param_name=param_name,
        compute_name=compute_name,
        label=label,
        kind=kind,
        param_type=param_type,
        description=_str(raw, "Description"),
        required=required,
        default=default,
        minimum=raw.get("Minimum", None),
        maximum=raw.get("Maximum", None),
        at_least=at_least,
        at_most=at_most,
    )


def _parse_output(raw: dict) -> OutputSpec:
    name = _str(raw, "Name")
    param_name, compute_name = _split_name(name, _RH_OUT_PREFIX)

    param_type = _str(raw, "ParamType")
    kind = type_mapping.classify(param_type)

    return OutputSpec(
        param_name=param_name,
        compute_name=compute_name,
        kind=kind,
        description=_str(raw, "Description"),
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
        description=_str(io_response, "Description"),
        gh_file=gh_path,
        inputs=inputs,
        outputs=outputs,
        created_at=now,
        updated_at=now,
    )


# ── to_mcp_tool ──────────────────────────────────────────────────────


_MAX_MCP_DESCRIPTION_CHARS = 4000


def to_mcp_tool(m: ToolManifest) -> dict:
    """ToolManifest -> MCP Tool Schema dict。複用 type_mapping.to_json_schema()。

    description 組成：`display_name — description`（description 為空時只用
    display_name），若 auto_doc 非空則再附加 `\\n\\n` + auto_doc——這是 AI
    調用端實際看到、用來理解工具用途與自動填參的內容。總長度上限
    _MAX_MCP_DESCRIPTION_CHARS，超過從尾部截斷並加 "…"。
    """
    if m.description:
        description = f"{m.display_name} — {m.description}"
    else:
        description = m.display_name

    if m.auto_doc:
        description = f"{description}\n\n{m.auto_doc}"

    if len(description) > _MAX_MCP_DESCRIPTION_CHARS:
        description = description[: _MAX_MCP_DESCRIPTION_CHARS - 1].rstrip() + "…"

    properties = {i.param_name: type_mapping.to_json_schema(i) for i in m.inputs}
    # InputSpec.required 是單一真相（見 InputSpec docstring），這裡不再依
    # default 重新推導，避免手動編輯過的 required=True 被靜默丟棄。
    required = [i.param_name for i in m.inputs if i.required]

    input_schema: dict = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required

    return {
        "name": m.id,
        "description": description,
        "inputSchema": input_schema,
    }
