"""
hoger/api/app.py — FastAPI app 組裝。

全域 exception handler 把 core/store 層的例外轉成 HTTP 狀態碼，讓
routes.py 不必到處包 try/except：
- ToolNotFound（tool_store）-> 404
- ToolArgError（executor）  -> 400

MCP Streamable HTTP 掛載於 /mcp（見 hoger.api.routes 的 /api/mcp-config，
它已經假設這個路徑）：StreamableHTTPSessionManager.handle_request() 需要
先透過 session_manager.run() 取得一個 anyio task group 才能運作，否則
會拋 RuntimeError("Task group is not initialized...")。這個 task group
的生命週期必須涵蓋整個 app 運行期間，因此用 FastAPI 的 lifespan
context manager，在 app 啟動時進入 session_manager.run()、app 關閉時
離開——確保每個 HTTP request 進來時 task group 都已就緒。

掛載順序（重要）：
1. app.include_router(api_router)  — /api/* 端點
2. app.mount("/mcp", ...)          — MCP Streamable HTTP
3. app.mount("/", StaticFiles(...)) — 必須最後：掛在 "/" 會攔截所有未被
   前面路由匹配到的路徑，若先掛載會蓋掉 /api/* 與 /mcp。

精確路徑 "/mcp"（無結尾斜線）的特例：Starlette 的 Mount("/mcp") 用 regex
"^/mcp/(?P<path>.*)$" 匹配，精確的 "/mcp" 匹配不到；正常情況 Router 的
redirect_slashes 會發 307 轉到 "/mcp/"，但 webui 的 StaticFiles 掛在 "/"
攔截了所有未匹配路徑，redirect 永遠沒機會發生——POST /mcp 會打到
StaticFiles 而收到 405。而 /api/mcp-config 公佈給 MCP client 的 URL 正是
無結尾斜線的 http://localhost:{port}/mcp。因此用一個純 ASGI middleware
把恰好 "/mcp" 的請求改寫成 "/mcp/"（見 _McpExactPathRewrite）；不用
FastAPI 的 @app.middleware("http")（BaseHTTPMiddleware）——它會包住整個
response cycle，對 Streamable HTTP 的 SSE streaming 不友善。
"""

import contextlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.staticfiles import StaticFiles

from hoger import config
from hoger.api.routes import router as api_router
from hoger.core.executor import ToolArgError
from hoger.mcp_server.server import server as mcp_server
from hoger.store.tool_store import ToolNotFound

# stateless=True：每個 request 都是獨立的一次性 session，不在 request 之間
# 保留狀態——HOGER 的工具清單本來就無快取、每次直接讀磁碟（見
# hoger.mcp_server.server 模組說明），MCP 這層也不需要額外的 session 狀態。
# json_response=False：保留 SSE streaming 能力（Streamable HTTP 的預設行為）。
# ⚠️ StreamableHTTPSessionManager.run() 每個 instance 只能進入一次——同一進程內
# 不可二次啟動本 app 的 lifespan（uvicorn reload / 多次 TestClient with-lifespan 會踩到）。
mcp_session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    json_response=False,
    stateless=True,
)


async def _mcp_asgi_app(scope, receive, send):
    await mcp_session_manager.handle_request(scope, receive, send)


class _McpExactPathRewrite:
    """把恰好 "/mcp" 的請求路徑改寫成 "/mcp/"，讓 Mount("/mcp") 能匹配。

    背景見模組 docstring「精確路徑 '/mcp' 的特例」段落。純 ASGI middleware，
    只改 scope["path"] 和 scope["raw_path"]，不碰 request/response body。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            # 同步更新 path 和 raw_path 以保持 ASGI spec 一致性
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    async with mcp_session_manager.run():
        yield


app = FastAPI(title="HOGER", version="0.1.0", lifespan=_lifespan)


@app.exception_handler(ToolNotFound)
def _tool_not_found_handler(request: Request, exc: ToolNotFound):
    tool_id = exc.args[0] if exc.args else ""
    return JSONResponse(status_code=404, content={"detail": f"tool not found: {tool_id!r}"})


@app.exception_handler(ToolArgError)
def _tool_arg_error_handler(request: Request, exc: ToolArgError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.add_middleware(_McpExactPathRewrite)

app.include_router(api_router)
app.mount("/mcp", _mcp_asgi_app)

webui_dir = config.ROOT / "webui"
app.mount("/", StaticFiles(directory=str(webui_dir), html=True), name="webui")
