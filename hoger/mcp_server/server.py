"""
hoger/mcp_server/server.py — HOGER MCP Server：從 tool_store 動態註冊工具。

MCP server 與 FastAPI 後端是共用 tools/*.json 目錄的獨立進程（見
hoger.store.tool_store 模組說明）。工具清單無快取——每次 list_tools 都
直接讀磁碟，UI 建立/註冊工具後，MCP 端立即可見，不必重啟進程。

status 過濾（雙重防線）：
- handle_list_tools()：只回傳 status=="registered" 的工具，draft 不出現
  在工具清單，AI 端看不到、不會嘗試呼叫。
- handle_call_tool()：即使呼叫端仍握有 draft 工具的 name（例如清單快取、
  手動指定），也在呼叫時再次檢查 status，拒絕執行未註冊工具。

錯誤機制：mcp SDK 1.28.1 的 low-level Server.call_tool() decorator
（見 mcp/server/lowlevel/server.py）會把 handler 拋出的例外包成
isError=True 的 CallToolResult（第 589-590 行 `except Exception as e:
return self._make_error_result(str(e))`）。但本模組選擇**不依賴這個隱式
包裝**，而是在 handle_call_tool() 內部直接 catch 並回傳
types.CallToolResult(isError=True, ...)：
1. handler 函式被設計成可直接 await 呼叫（測試需求），若靠 decorator
   包裝例外，直接呼叫 handler 時測試就得自己 try/except，語意不一致。
2. 回傳型別在「查無工具」「draft 工具」「參數錯誤」三種錯誤情境下一致，
   呼叫端（decorator 或測試）都拿到結構化的 CallToolResult，不必分別
   處理「拋例外」與「回傳值」兩種路徑。

executor.run_tool() 是同步阻塞呼叫（HTTP 呼叫 Rhino.Compute + 檔案
I/O），用 anyio.to_thread.run_sync 包起來，避免卡住 stdio transport 的
event loop。

logging 用 logger "hoger.mcp"，絕不 print——stdio transport 下 stdout
被 JSON-RPC 訊息獨佔，任何 print 都會污染協定 stream。
"""

import json
import logging

import anyio.to_thread
import mcp.types as types
from mcp.server import Server

from hoger.core import executor
from hoger.core.executor import ToolArgError
from hoger.core.manifest import to_mcp_tool
from hoger.store import tool_store
from hoger.store.tool_store import ToolNotFound

logger = logging.getLogger("hoger.mcp")

server = Server("hoger")


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        isError=True,
    )


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """回傳 status=="registered" 的工具；draft 不出現。無快取，直接讀磁碟。"""
    manifests = tool_store.list_tools()
    tools = []
    for m in manifests:
        if m.status != "registered":
            continue
        schema = to_mcp_tool(m)
        tools.append(
            types.Tool(
                name=schema["name"],
                description=schema["description"],
                inputSchema=schema["inputSchema"],
            )
        )
    return tools


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent] | types.CallToolResult:
    """
    呼叫工具。錯誤情境（查無工具 / draft 工具 / 參數錯誤）一律回傳
    isError=True 的 CallToolResult，讓 AI 端能讀到訊息並自我修正。
    """
    if arguments is None:
        arguments = {}

    try:
        manifest = tool_store.get(name)
    except ToolNotFound:
        logger.warning("hoger.mcp: call_tool for unknown tool %r", name)
        return _error_result(f"tool not found: {name!r}")

    if manifest.status != "registered":
        logger.warning("hoger.mcp: call_tool for non-registered tool %r (status=%r)", name, manifest.status)
        return _error_result(f"tool not registered: {name!r}")

    try:
        result = await anyio.to_thread.run_sync(executor.run_tool, manifest, arguments)
    except ToolArgError as exc:
        logger.info("hoger.mcp: tool arg error for %r: %s", name, exc)
        return _error_result(str(exc))

    payload = {
        "outputs": result.outputs,
        "result_3dm": result.result_3dm,
        "elapsed_ms": result.elapsed_ms,
        "warnings": result.warnings,
        "errors": result.errors,
        "modelunits": result.modelunits,
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return [types.TextContent(type="text", text=text)]
