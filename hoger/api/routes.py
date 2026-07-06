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

import dataclasses
import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from hoger import config
from hoger.config import GH_FILES_DIR, HOGER_PORT, ROOT, TOOLS_DIR
from hoger.core import compute_client, executor, llm
from hoger.core.compute_client import ComputeError
from hoger.core.describe import (
    build_auto_doc,
    build_graph_digest,
    describe_input,
    describe_output,
    describe_tool,
)
from hoger.core.manifest import ToolManifest, manifest_from_io, to_mcp_tool
from hoger.ghio import loader, marker, scanner
from hoger.ghio.marker import MarkError
from hoger.mcp_server import config_gen
from hoger.store import tool_store

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


# ── request/response models ─────────────────────────────────────────


class ImportGhPathBody(BaseModel):
    gh_path: str


class RunToolBody(BaseModel):
    args: dict = {}


class ScanGhPathBody(BaseModel):
    gh_path: str


class MarkEntry(BaseModel):
    guid: str
    name: str


class ConvertBody(BaseModel):
    gh_path: str
    inputs: list[MarkEntry] = []
    outputs: list[MarkEntry] = []
    ai_describe: bool = False


# ── health ───────────────────────────────────────────────────────────


@router.get("/health")
def get_health():
    return {"hoger": True, "compute": compute_client.health()}


# ── llm-status ───────────────────────────────────────────────────────


@router.get("/llm-status")
def get_llm_status():
    return dataclasses.asdict(llm.status())


# ── import ───────────────────────────────────────────────────────────


def _candidate_index_by_mark(candidates: list) -> dict:
    """以 existing_mark 為 key 索引 scan candidate（InputCandidate/OutputCandidate）。

    existing_mark 為 None 的 candidate 不索引——無法對回任何 spec（例如直接
    解析路徑掃到的 Hops 檔案，沒有 RH_IN:/RH_OUT: 分組）。與
    hoger.core.describe._candidate_index 同一慣例，這裡另建一份是因為 routes
    層還要用它來決定「是否要覆寫某個 spec 的 description」，這個決定權責
    在呼叫端（見模組 docstring），describe.py 只管生成文字。
    """
    index: dict = {}
    for cand in candidates:
        mark = getattr(cand, "existing_mark", None)
        if mark:
            index[mark] = cand
    return index


def _find_scan_candidate(spec, index: dict):
    name = spec.compute_name or spec.param_name
    return index.get(name)


def _enrich_manifest_with_scan(manifest: ToolManifest, gh_path: str) -> ToolManifest:
    """轉換/匯入後，盡力用 scanner.scan_gh() 的掃描結果補足自動描述。

    Best-effort：scan 失敗（GH_IO 拋例外、檔案格式問題等）只記 warning，
    絕不讓呼叫端的轉換/匯入流程失敗——自動描述是錦上添花，不是關鍵路徑。
    規則：
    - manifest.description（工具層級）只在原本是空字串時，用
      describe_tool() 生成的文字填入；使用者/上游已提供的說明不覆寫。
    - 每個 InputSpec/OutputSpec.description 同理，只在空字串時填入
      describe_input()/describe_output() 生成的文字。
    - manifest.auto_doc 一律（重新）生成——這是獨立欄位，永遠可以安全地
      重算，不涉及「覆寫使用者輸入」的疑慮。

    薄封裝 _enrich_manifest_with_scan_and_dict()（只取第一個回傳值），
    保留給既有呼叫端（/api/import）與既有測試套件用，避免改動它們的
    介面。/api/convert 的 ai_describe 路徑改呼叫下面那個版本，重用同一次
    掃描的 scan_dict 做 digest，不必為了 AI 解讀再多掃一次可能很大的檔案。
    """
    manifest, _scan_dict = _enrich_manifest_with_scan_and_dict(manifest, gh_path)
    return manifest


def _enrich_manifest_with_scan_and_dict(
    manifest: ToolManifest, gh_path: str
) -> tuple[ToolManifest, Optional[dict]]:
    try:
        scan_result = scanner.scan_gh(gh_path)
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment, never break the caller
        logger.warning("hoger.routes: 轉換後 scan_gh 失敗，略過自動描述: %s", exc)
        return manifest, None

    scan_dict = dataclasses.asdict(scan_result)
    input_index = _candidate_index_by_mark(scan_result.inputs)
    output_index = _candidate_index_by_mark(scan_result.outputs)

    if not manifest.description:
        manifest.description = describe_tool(manifest, scan_result.component_inventory)

    for spec in manifest.inputs:
        if not spec.description:
            candidate = _find_scan_candidate(spec, input_index)
            candidate_dict = dataclasses.asdict(candidate) if candidate is not None else None
            spec.description = describe_input(spec, candidate_dict)

    for spec in manifest.outputs:
        if not spec.description:
            candidate = _find_scan_candidate(spec, output_index)
            candidate_dict = dataclasses.asdict(candidate) if candidate is not None else None
            spec.description = describe_output(spec, candidate_dict)

    manifest.auto_doc = build_auto_doc(manifest, scan_dict)

    return manifest, scan_dict


def _import_from_gh_path(gh_path: str) -> ToolManifest:
    try:
        io_response = compute_client.io_query(gh_path)
    except ComputeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Rhino.Compute 呼叫失敗，請確認 Rhino.Compute 已啟動：{exc}",
        ) from exc
    manifest = manifest_from_io(gh_path, io_response)

    if loader.is_available():
        manifest = _enrich_manifest_with_scan(manifest, gh_path)

    return manifest


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB；本工具僅供 localhost 單人使用，
# 信任模型是「執行此程式的人」而非「任意遠端使用者」，此上限只是防呆
# （避免不慎上傳超大檔案拖垮磁碟/記憶體），不是對抗惡意使用者的防線。


def _save_upload(filename: str, content: bytes) -> Path:
    """消毒檔名並把上傳內容寫入 GH_FILES_DIR，回傳落地路徑。

    共用於 /import 與 /scan 的 multipart 分支。消毒規則：防止路徑逃逸
    （"../escaped.gh"）或絕對路徑（"C:/x/evil.gh"）蓋過 GH_FILES_DIR 以外
    的檔案——統一分隔符後只取 basename，再確認落點仍在 GH_FILES_DIR 內
    （雙重防禦，避免 symlink 等邊角案例繞過）。
    """
    if not filename:
        raise HTTPException(status_code=400, detail="missing filename")

    safe_name = Path(filename.replace("\\", "/")).name
    if not safe_name or not safe_name.lower().endswith(".gh"):
        raise HTTPException(status_code=400, detail="filename must be a plain *.gh file name")

    dest = (GH_FILES_DIR / safe_name).resolve()
    if not dest.is_relative_to(GH_FILES_DIR.resolve()):
        raise HTTPException(status_code=400, detail="invalid filename")

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 50MB)")

    with open(dest, "wb") as f:
        f.write(content)

    return dest


def _save_upload_and_import(filename: str, content: bytes) -> dict:
    """阻塞工作（檔案寫入 + Rhino.Compute 呼叫）：丟到 threadpool 執行。"""
    dest = _save_upload(filename, content)
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
        # 空檔名（filename=""）時，starlette 會把這個欄位解析成普通字串而非
        # UploadFile（沒有 .filename/.read()）；一併視為「缺少檔名」。
        if upload is None or not hasattr(upload, "filename"):
            raise HTTPException(status_code=400, detail="missing filename")

        content = await upload.read()
        return await run_in_threadpool(_save_upload_and_import, upload.filename, content)

    try:
        payload = await request.json()
        body = ImportGhPathBody.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid request body: {exc}") from exc

    return await run_in_threadpool(_import_by_gh_path, body.gh_path)


# ── scan ─────────────────────────────────────────────────────────────


_GHIO_UNAVAILABLE_DETAIL = (
    "GH_IO.dll 不可用：需要安裝 Rhino 8（含 Grasshopper）"
    "或設定環境變數 HOGER_GHIO_DLL 指向 GH_IO.dll 的路徑。"
)

_SANITIZE_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


def _require_ghio_available() -> None:
    if not loader.is_available():
        raise HTTPException(status_code=501, detail=_GHIO_UNAVAILABLE_DETAIL)


def _check_gh_path(gh_path: str) -> None:
    if not gh_path.lower().endswith(".gh"):
        raise HTTPException(status_code=400, detail="only .gh files are supported")
    if not Path(gh_path).exists():
        raise HTTPException(status_code=404, detail=f"gh_path not found: {gh_path}")


def _suggest_name(candidate, used_names: set) -> str:
    """建議參數名（掃描階段預填給使用者確認/修改用，不是最終權威值）：優先
    第一個 feed 的接線端名稱，其次 nickname，否則 object_type 小寫加序號。
    名字消毒為 ^[A-Za-z0-9_]+$（移除所有其他字元），消毒後為空則 fallback
    為 object_type 形式。同名衝突加 _2/_3... 後綴。

    candidate 可以是 scanner.InputCandidate 或 scanner.OutputCandidate
    （呼叫端把 inputs 與 outputs 混在同一個 used_names 集合裡跑，見
    _build_suggested_names，確保建議名跨輸入/輸出也不重複）。兩者的接線
    欄位名稱與 dict key 都不同（見 hoger/ghio/scanner.py）：
    - InputCandidate.feeds：接到哪個元件的哪個腳位 -> dict 用 "input" key。
    - OutputCandidate.fed_by：由哪個元件的哪個輸出腳位餵入 -> dict 用
      "output" key。
    下方 getattr 依序嘗試兩個屬性名、feeds[0] 依序嘗試兩個 key，讓同一份
    程式碼服務 input 與 output 兩種 candidate。
    """
    feeds = getattr(candidate, "feeds", None) or getattr(candidate, "fed_by", None) or []
    nickname = getattr(candidate, "nickname", None) or ""

    if feeds:
        raw = feeds[0].get("input") or feeds[0].get("output") or ""
    else:
        raw = nickname

    base = _SANITIZE_NAME_RE.sub("", raw or "")

    if not base:
        type_slug = _SANITIZE_NAME_RE.sub("_", candidate.object_type.strip().lower()) or "param"
        n = 1
        candidate_name = f"{type_slug}_{n}"
        while candidate_name in used_names:
            n += 1
            candidate_name = f"{type_slug}_{n}"
        used_names.add(candidate_name)
        return candidate_name

    if base not in used_names:
        used_names.add(base)
        return base

    n = 2
    candidate_name = f"{base}_{n}"
    while candidate_name in used_names:
        n += 1
        candidate_name = f"{base}_{n}"
    used_names.add(candidate_name)
    return candidate_name


def _build_suggested_names(scan_result) -> dict:
    used_names: set = set()
    suggested = {}
    for cand in list(scan_result.inputs) + list(scan_result.outputs):
        suggested[cand.instance_guid] = _suggest_name(cand, used_names)
    return suggested


def _scan_by_gh_path(gh_path: str) -> dict:
    """阻塞工作（磁碟檢查 + .gh 解析）：丟到 threadpool 執行。"""
    _require_ghio_available()
    _check_gh_path(gh_path)

    try:
        scan_result = scanner.scan_gh(gh_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "gh_path": gh_path,
        "scan": dataclasses.asdict(scan_result),
        "suggested_names": _build_suggested_names(scan_result),
    }


def _save_upload_and_scan(filename: str, content: bytes) -> dict:
    """阻塞工作（GH_IO 可用性 + 檔案寫入 + 掃描）：丟到 threadpool 執行。"""
    _require_ghio_available()
    dest = _save_upload(filename, content)

    try:
        scan_result = scanner.scan_gh(str(dest))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "gh_path": str(dest),
        "scan": dataclasses.asdict(scan_result),
        "suggested_names": _build_suggested_names(scan_result),
    }


@router.post("/scan")
async def scan_gh_file(request: Request):
    """POST /api/scan — 掃描 .gh 檔案的候選輸入/輸出（唯讀，不動檔案）。

    與 /api/import 相同的雙形式 body 解析理由（見模組 docstring）：
    multipart 上傳 vs JSON gh_path 無法共存於單一 FastAPI 參數簽名。
    """
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "filename"):
            raise HTTPException(status_code=400, detail="missing filename")

        content = await upload.read()
        return await run_in_threadpool(_save_upload_and_scan, upload.filename, content)

    try:
        payload = await request.json()
        body = ScanGhPathBody.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid request body: {exc}") from exc

    return await run_in_threadpool(_scan_by_gh_path, body.gh_path)


# ── convert: AI 深度解讀（task v3-B） ───────────────────────────────


def _apply_ai_describe(manifest: ToolManifest, scan_dict: Optional[dict]) -> Optional[str]:
    """勾選「AI 深度解讀」時，用 llm.interpret() 覆蓋規則式描述。

    回傳值：None 表示成功套用；非 None 字串是 ai_describe_error 訊息
    （provider 不可用的 reason，或 LlmError 的訊息）——呼叫端據此決定
    ai_describe_used 為 True/False，但無論哪種情況，manifest 上已由
    _enrich_manifest_with_scan 填好的規則式內容都原封不動保留（本函式
    只在成功時「疊加覆蓋」，從不刪除既有內容）。
    """
    llm_status = llm.status()
    if not llm_status.available:
        return llm_status.reason

    param_names = [spec.param_name for spec in manifest.inputs]
    output_names = [spec.param_name for spec in manifest.outputs]

    try:
        interpretation = llm.interpret(
            build_graph_digest(manifest, scan_dict), param_names, output_names
        )
    except llm.LlmError as exc:
        logger.warning("hoger.routes: AI 深度解讀失敗，fallback 至規則式描述: %s", exc)
        return str(exc)

    manifest.description = interpretation.tool_purpose

    for spec in manifest.inputs:
        override = interpretation.param_descriptions.get(spec.param_name)
        if override:
            spec.description = override

    for spec in manifest.outputs:
        override = interpretation.output_descriptions.get(spec.param_name)
        if override:
            spec.description = override

    ai_section = ["## AI 解讀", "", interpretation.tool_purpose]
    if interpretation.usage_notes:
        ai_section.extend(["", interpretation.usage_notes])
    ai_section.append("")
    manifest.auto_doc = "\n".join(ai_section) + "\n" + manifest.auto_doc

    return None


# ── convert ──────────────────────────────────────────────────────────


def _convert(body: ConvertBody) -> dict:
    """阻塞工作（磁碟檢查、marker 寫檔、Rhino.Compute 呼叫）：丟到 threadpool 執行。"""
    _require_ghio_available()
    _check_gh_path(body.gh_path)

    if not body.inputs and not body.outputs:
        raise HTTPException(status_code=400, detail="至少選擇一個輸入或輸出")

    input_marks = [m.model_dump() for m in body.inputs]
    output_marks = [m.model_dump() for m in body.outputs]

    try:
        mark_result = marker.apply_marks(body.gh_path, input_marks, output_marks, backup=True)
    except MarkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # /io 逾時依標記數量分級：>200 個標記的大型定義解析明顯更久，給到
    # 540s（前端對應給 10 分鐘）；其餘維持預設 300s。
    total_marks = len(input_marks) + len(output_marks)
    io_timeout = 540 if total_marks > 200 else 300

    try:
        io_response = compute_client.io_query(body.gh_path, timeout=io_timeout)
    except ComputeError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"標記已完成且已備份（backup_path: {mark_result.backup_path}），"
                f"但呼叫 Rhino.Compute /io 失敗：{exc}。"
                "Compute 上線後，可在轉換區用「本機路徑」直接重新匯入這個已標記的檔案，"
                "不需要重新掃描或重新標記。"
            ),
        ) from exc

    manifest = manifest_from_io(body.gh_path, io_response)
    manifest, scan_dict = _enrich_manifest_with_scan_and_dict(manifest, body.gh_path)

    ai_describe_used = False
    ai_describe_error = None
    if body.ai_describe:
        # 重用規則式 enrich 剛做的那次掃描結果（scan_dict）做 digest，不必
        # 為了 AI 解讀再多掃一次可能很大的檔案；scan_dict 為 None（enrich
        # 本身掃描失敗）時 build_graph_digest 對 None 有防禦，digest 只是
        # 少了結構事實，不影響是否呼叫 LLM 的判斷（那由 llm.status() 決定）。
        ai_error = _apply_ai_describe(manifest, scan_dict)
        if ai_error is None:
            ai_describe_used = True
        else:
            ai_describe_error = ai_error

    response = {
        "manifest": manifest.model_dump(),
        "backup_path": mark_result.backup_path,
        "marked_inputs": mark_result.marked_inputs,
        "marked_outputs": mark_result.marked_outputs,
        "updated": mark_result.updated,
        "ai_describe_used": ai_describe_used,
    }
    if ai_describe_error is not None:
        response["ai_describe_error"] = ai_describe_error
    return response


@router.post("/convert")
def convert_gh_file(body: ConvertBody):
    return _convert(body)


# ── tools CRUD ───────────────────────────────────────────────────────


def _get_or_404(tool_id: str) -> ToolManifest:
    """tool_store.get 的薄封裝：查無此工具時拋 ToolNotFound -> 全域 handler 轉 404。"""
    return tool_store.get(tool_id, tools_dir=TOOLS_DIR)


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
    manifest = _get_or_404(tool_id)
    return {"manifest": manifest.model_dump(), "mcp_schema": to_mcp_tool(manifest)}


@router.put("/tools/{tool_id}")
def update_tool(tool_id: str, manifest: ToolManifest):
    if manifest.id != tool_id:
        raise HTTPException(
            status_code=400,
            detail=f"path id {tool_id!r} does not match body id {manifest.id!r}",
        )
    # 工具必須已存在；不存在時 tool_store.get 拋 ToolNotFound -> 全域 handler 轉 404
    _get_or_404(tool_id)
    tool_store.save(manifest, tools_dir=TOOLS_DIR)
    return manifest.model_dump()


@router.delete("/tools/{tool_id}", status_code=204)
def delete_tool(tool_id: str):
    tool_store.delete(tool_id, tools_dir=TOOLS_DIR)


# ── run ──────────────────────────────────────────────────────────────


@router.post("/tools/{tool_id}/run")
def run_tool(tool_id: str, body: RunToolBody, debug: bool = Query(False)):
    manifest = _get_or_404(tool_id)
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
    # 每次查詢都刷新落地檔——確保 snippet JSON 總是與當前設定同步。
    # 寫檔只是副作用（方便使用者直接拿現成檔案），不是這個端點的核心職責；
    # 失敗（例如磁碟權限問題）不應讓查詢設定本身這件事回傳 500。
    try:
        config_gen.write_mcp_config_snippet()
    except OSError as exc:
        logger.warning("write_mcp_config_snippet failed: %s", exc)
    return config_gen.build_mcp_config()
