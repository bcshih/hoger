"""
hoger/api/app.py — FastAPI app 組裝。

全域 exception handler 把 core/store 層的例外轉成 HTTP 狀態碼，讓
routes.py 不必到處包 try/except：
- ToolNotFound（tool_store）-> 404
- ToolArgError（executor）  -> 400

靜態檔案（webui/）必須最後掛載：StaticFiles 掛在 "/" 會攔截所有未被前面
路由匹配到的路徑，若先掛載會蓋掉 /api/* 端點。
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from hoger import config
from hoger.api.routes import router as api_router
from hoger.core.executor import ToolArgError
from hoger.store.tool_store import ToolNotFound

app = FastAPI(title="HOGER", version="0.1.0")


@app.exception_handler(ToolNotFound)
def _tool_not_found_handler(request: Request, exc: ToolNotFound):
    tool_id = exc.args[0] if exc.args else ""
    return JSONResponse(status_code=404, content={"detail": f"tool not found: {tool_id!r}"})


@app.exception_handler(ToolArgError)
def _tool_arg_error_handler(request: Request, exc: ToolArgError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(api_router)
# /mcp mount 由 Task 4.2 加入

webui_dir = config.ROOT / "webui"
app.mount("/", StaticFiles(directory=str(webui_dir), html=True), name="webui")
