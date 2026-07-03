"""
hoger/api/routes.py — HOGER FastAPI 端點（掛在 /api prefix）。

這一層是薄封裝：驗證 -> 呼叫 hoger.core / hoger.store -> JSON 回應。
除 `/import` 外，所有端點用 sync `def`（非 async def）——FastAPI 自動丟進
threadpool 執行，避免 requests（compute_client）與檔案 I/O（tool_store）
阻塞 event loop。

`/import` 例外是 async def：它要依 content-type 動態判斷 body 是 JSON 還是
multipart（FastAPI 的 UploadFile 參數會強迫整個 request 走 form-data 解析，
無法與 JSON body 參數共存於同一簽名），因此改讀取原始 Request 並手動分流。
但實際的阻塞工作（檔案寫入、Rhino.Compute 呼叫）仍透過
`starlette.concurrency.run_in_threadpool` 丟到 threadpool，不阻塞 event loop。

錯誤轉換策略：
- ToolNotFound / ToolArgError 由 hoger.api.app 的全域 exception handler 轉
  404 / 400，這裡直接讓它們往外拋即可（不必到處包 try/except）。
- import 端點的 ComputeError 轉 502（提示啟動 Rhino.Compute）——本地
  catch，因為訊息內容（"啟動 Rhino.Compute"）是 import 端點特有的。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from hoger import config
from hoger.config import GH_FILES_DIR, HOGER_PORT, ROOT, TOOLS_DIR
from hoger.core import compute_client, executor
from hoger.core.compute_client import ComputeError
from hoger.core.manifest import ToolManifest, manifest_from_io, to_mcp_tool
from hoger.store import tool_store

router = APIRouter(prefix="/api")


# ── request/response models ─────────────────────────────────────────


class ImportGhPathBody(BaseModel):
    gh_path: str


class RunToolBody(BaseModel):
    args: dict = {}


# ── health ───────────────────────────────────────────────────────────


@router.get("/health")
def get_health():
    return {"hoger": True, "compute": compute_client.health()}


# ── import ───────────────────────────────────────────────────────────


def _import_from_gh_path(gh_path: str) -> ToolManifest:
    try:
        io_response = compute_client.io_query(gh_path)
    except ComputeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Rhino.Compute 呼叫失敗，請確認 Rhino.Compute 已啟動：{exc}",
        ) from exc
    return manifest_from_io(gh_path, io_response)


def _save_upload_and_import(filename: str, content: bytes) -> dict:
    """阻塞工作（檔案寫入 + Rhino.Compute 呼叫）：丟到 threadpool 執行。"""
    if not filename.lower().endswith(".gh"):
        raise HTTPException(status_code=400, detail="only .gh files are supported")

    dest = GH_FILES_DIR / filename
    with open(dest, "wb") as f:
        f.write(content)

    manifest = _import_from_gh_path(str(dest))
    return manifest.model_dump()


def _import_by_gh_path(gh_path: str) -> dict:
    """阻塞工作（磁碟檢查 + Rhino.Compute 呼叫）：丟到 threadpool 執行。"""
    if not gh_path.lower().endswith(".gh"):
        raise HTTPException(status_code=400, detail="only .gh files are supported")
    if not Path(gh_path).exists():
        raise HTTPException(status_code=404, detail=f"gh_path not found: {gh_path}")

    manifest = _import_from_gh_path(gh_path)
    return manifest.model_dump()


@router.post("/import")
async def import_gh_file(request: Request):
    """
    兩種 body 形狀，無法用單一 FastAPI 參數簽名同時宣告（UploadFile 參數會
    強迫整個 request 被當成 multipart/form-data 解析，JSON body 讀不到），
    因此改用 Request 依 content-type 手動分流。這個端點本身是 async def
    （僅為了讀取 request body），但實際的阻塞工作（檔案 I/O、Rhino.Compute
    呼叫）一律丟進 run_in_threadpool，不阻塞 event loop。
    """
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise HTTPException(status_code=400, detail="missing 'file' in multipart body")

        content = await upload.read()
        return await run_in_threadpool(_save_upload_and_import, upload.filename, content)

    try:
        payload = await request.json()
        body = ImportGhPathBody.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid request body: {exc}") from exc

    return await run_in_threadpool(_import_by_gh_path, body.gh_path)


# ── tools CRUD ───────────────────────────────────────────────────────


@router.get("/tools")
def list_tools():
    manifests = tool_store.list_tools(tools_dir=TOOLS_DIR)
    return [
        {
            "id": m.id,
            "display_name": m.display_name,
            "status": m.status,
            "inputs_count": len(m.inputs),
            "outputs_count": len(m.outputs),
            "updated_at": m.updated_at,
        }
        for m in manifests
    ]


@router.post("/tools", status_code=201)
def create_tool(manifest: ToolManifest):
    # tool_store.save() 對不合法 id 拋 ToolNotFound（對 tool_store 而言等同
    # 「查無此工具」）；但在這個端點，manifest.id 是使用者剛送出的建立請求，
    # 語意上是「請求本身不合法」-> 400，而非「找不到工具」-> 404。
    try:
        tool_store.save(manifest, tools_dir=TOOLS_DIR)
    except tool_store.ToolNotFound as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid tool id: {manifest.id!r}"
        ) from exc
    return manifest.model_dump()


@router.get("/tools/{tool_id}")
def get_tool(tool_id: str):
    manifest = tool_store.get(tool_id, tools_dir=TOOLS_DIR)
    return {"manifest": manifest.model_dump(), "mcp_schema": to_mcp_tool(manifest)}


@router.put("/tools/{tool_id}")
def update_tool(tool_id: str, manifest: ToolManifest):
    if manifest.id != tool_id:
        raise HTTPException(
            status_code=400,
            detail=f"path id {tool_id!r} does not match body id {manifest.id!r}",
        )
    # 工具必須已存在；不存在時 tool_store.get 拋 ToolNotFound -> 全域 handler 轉 404
    tool_store.get(tool_id, tools_dir=TOOLS_DIR)
    tool_store.save(manifest, tools_dir=TOOLS_DIR)
    return manifest.model_dump()


@router.delete("/tools/{tool_id}", status_code=204)
def delete_tool(tool_id: str):
    tool_store.delete(tool_id, tools_dir=TOOLS_DIR)


# ── run ──────────────────────────────────────────────────────────────


@router.post("/tools/{tool_id}/run")
def run_tool(tool_id: str, body: RunToolBody, debug: bool = Query(False)):
    manifest = tool_store.get(tool_id, tools_dir=TOOLS_DIR)
    result = executor.run_tool(manifest, body.args)

    response = {
        "outputs": result.outputs,
        "result_3dm": result.result_3dm,
        "elapsed_ms": result.elapsed_ms,
        "errors": result.errors,
        "warnings": result.warnings,
        "modelunits": result.modelunits,
    }
    if debug:
        response["raw"] = result.raw
    return response


# ── mcp-config ───────────────────────────────────────────────────────


@router.get("/mcp-config")
def get_mcp_config():
    venv_python = str(ROOT / ".venv" / "Scripts" / "python.exe")
    return {
        "stdio": {
            "mcpServers": {
                "hoger": {
                    "command": venv_python,
                    "args": ["-m", "hoger.mcp_server.stdio_main"],
                    "cwd": str(ROOT),
                    "env": {"HOGER_COMPUTE_URL": config.COMPUTE_URL},
                }
            }
        },
        "http": {
            "mcpServers": {
                "hoger": {"url": f"http://localhost:{HOGER_PORT}/mcp"},
            }
        },
    }
