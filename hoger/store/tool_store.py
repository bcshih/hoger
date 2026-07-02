"""
hoger/store/tool_store.py — tools/*.json 工具庫 CRUD。

工具定義（ToolManifest）以 JSON 檔存於 TOOLS_DIR（見 hoger.config），
一工具一檔（{manifest.id}.json）。FastAPI 後端與 MCP server 是兩個
不同進程共用此目錄——所以不做記憶體快取，每次操作直接讀寫磁碟，天然同步。

API：
  - save(manifest, tools_dir=None) -> str: 寫入檔案，更新 updated_at，回傳絕對路徑
  - get(tool_id, tools_dir=None) -> ToolManifest: 讀取並解析
  - list_tools(tools_dir=None) -> list[ToolManifest]: 依 updated_at 降冪排序
  - delete(tool_id, tools_dir=None) -> None: 刪除檔案

例外：
  - ToolNotFound: 指定 id 的工具不存在
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from hoger.core.manifest import ToolManifest

logger = logging.getLogger("hoger.store")

# 合法 tool_id：kebab-case（小寫字母、數字、連字號），與 manifest._slugify
# 的產出格式一致。拒絕其他字元可同時擋掉路徑逃逸（"../evil"、"..\\evil"）
# 與大寫、底線等異常輸入。
_TOOL_ID_RE = re.compile(r"^[a-z0-9-]+$")


class ToolNotFound(KeyError):
    """指定 id 的工具不存在"""

    pass


def _validate_tool_id(tool_id: str) -> None:
    """
    驗證 tool_id 格式。不合法（路徑逃逸字元、大寫、底線等）一律拋
    ToolNotFound——對呼叫端而言等同「查無此工具」，不洩漏檔案系統細節。
    """
    if not isinstance(tool_id, str) or not _TOOL_ID_RE.fullmatch(tool_id):
        raise ToolNotFound(tool_id)


def _get_tools_dir(tools_dir: Optional[Path]) -> Path:
    """解析 tools_dir：None 時用 config.TOOLS_DIR，否則轉換為 Path 物件"""
    if tools_dir is None:
        from hoger import config

        tools_dir = config.TOOLS_DIR
    return Path(tools_dir) if not isinstance(tools_dir, Path) else tools_dir


def save(manifest: ToolManifest, tools_dir: Optional[Path] = None) -> str:
    """
    保存工具定義到 {tools_dir}/{manifest.id}.json。

    更新 manifest.updated_at 為當前時間（ISO 8601）。
    寫入時用 ensure_ascii=False 保留中文等非 ASCII 字元。

    Args:
        manifest: ToolManifest 物件
        tools_dir: JSON 檔存放目錄；None 時使用 config.TOOLS_DIR

    Returns:
        寫入檔案的絕對路徑（字串）

    Raises:
        ToolNotFound: manifest.id 格式不合法（API 允許客戶端送 manifest，
            id 成為使用者輸入，須在落地前擋下路徑逃逸等異常字元）
    """
    _validate_tool_id(manifest.id)
    tools_dir = _get_tools_dir(tools_dir)

    # 更新 updated_at
    manifest.updated_at = datetime.now(timezone.utc).isoformat()

    # 序列化為 dict
    data = manifest.model_dump()

    # 寫入 JSON
    file_path = tools_dir / f"{manifest.id}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return str(file_path.resolve())


def get(tool_id: str, tools_dir: Optional[Path] = None) -> ToolManifest:
    """
    讀取工具定義。

    Args:
        tool_id: 工具 ID
        tools_dir: JSON 檔存放目錄；None 時使用 config.TOOLS_DIR

    Returns:
        ToolManifest 物件

    Raises:
        ToolNotFound: 如果工具不存在或 tool_id 格式不合法
        json.JSONDecodeError, pydantic.ValidationError: 檔案損壞時原樣拋出
    """
    _validate_tool_id(tool_id)
    tools_dir = _get_tools_dir(tools_dir)
    file_path = tools_dir / f"{tool_id}.json"

    if not file_path.exists():
        raise ToolNotFound(tool_id)

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return ToolManifest.model_validate(data)


def list_tools(tools_dir: Optional[Path] = None) -> list[ToolManifest]:
    """
    列出所有工具，按 updated_at 降冪排序。

    單一檔案損壞（JSON 或 pydantic 驗證錯誤）時，記錄 warning 並跳過，不影響其他工具。

    Args:
        tools_dir: JSON 檔存放目錄；None 時使用 config.TOOLS_DIR

    Returns:
        ToolManifest 列表，按 updated_at 降冪排序
    """
    tools_dir = _get_tools_dir(tools_dir)

    manifests = []
    for json_file in sorted(tools_dir.glob("*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            manifest = ToolManifest.model_validate(data)
            manifests.append(manifest)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Failed to load tool from {json_file.name}: {e}")
            continue

    # 按 updated_at 降冪排序（最新的在前）
    manifests.sort(key=lambda m: m.updated_at, reverse=True)
    return manifests


def delete(tool_id: str, tools_dir: Optional[Path] = None) -> None:
    """
    刪除工具定義檔案。

    Args:
        tool_id: 工具 ID
        tools_dir: JSON 檔存放目錄；None 時使用 config.TOOLS_DIR

    Raises:
        ToolNotFound: 如果工具不存在或 tool_id 格式不合法
    """
    _validate_tool_id(tool_id)
    tools_dir = _get_tools_dir(tools_dir)
    file_path = tools_dir / f"{tool_id}.json"

    if not file_path.exists():
        raise ToolNotFound(tool_id)

    file_path.unlink()
