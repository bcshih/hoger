# HOGER Implementation Plan（GH → Hops + MCP 轉換平台）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 HOGER——一個 Web UI 平台，將任意 `.gh` 檔案透過 Rhino.Compute `/io` 自動解析輸入/輸出，產生 Hops 端點與標準 MCP 工具註冊，讓 AI 能以 MCP 協議無頭執行 GH 運算。

**Architecture:** 單一 Python 套件 `hoger`，核心層（compute client / type mapping / executor）被三個介面共用：FastAPI 後端（供 Web UI）、MCP Server（stdio + Streamable HTTP，動態註冊工具）、Hops 端點（/io 相容格式）。工具定義以 JSON manifest 存於 `tools/`，Web UI 為無建置步驟的靜態 SPA。

**Tech Stack:** Python 3.11+、FastAPI + uvicorn、官方 `mcp` Python SDK（low-level Server，動態 schema）、`rhino3dm` + `requests`（沿用 v1.0.3 已驗證的 compute_core 序列化規則）、pytest + respx/fixture 錄製、Vanilla JS ES Modules（無 build step）。

---

## 0. 技術選型與關鍵決策

| 決策點 | 選擇 | 理由 |
|---|---|---|
| 後端框架 | **FastAPI + uvicorn** | 自動 OpenAPI、可同時 mount 靜態 UI 與 MCP Streamable HTTP app；async 適合長時間 compute 呼叫 |
| MCP SDK | **官方 `mcp` Python SDK，low-level `Server`** | 工具 schema 來自 GH 檔案、執行期動態變化，FastMCP 的函式簽名推導不適用；low-level `list_tools`/`call_tool` handler 可直接回傳 manifest 產生的 JSON Schema |
| MCP 傳輸 | **stdio**（Claude Desktop）+ **Streamable HTTP**（mount 於 FastAPI，`/mcp`） | 規格要求雙模式；Streamable HTTP 是 SSE 的後繼標準，Cursor/Claude Desktop 均支援 |
| Rhino.Compute 呼叫 | **requests 直接 POST**（沿用 `compute_core.evaluate` 模式） | v1.0.3 已驗證：繞過 `compute_rhino3d.ComputeFetch` 的 stdout/timeout 問題，保留錯誤診斷 |
| GH 解析 | **只用 `/io` 端點**，不解析 ghx XML | 規格明訂；/io 回傳格式即 Hops 元件定義格式 |
| 工具儲存 | **JSON manifest 檔（`tools/*.json`），一工具一檔** | 本機單人工具，無需 DB；git-diff 友善；MCP server 與 backend 共用同一份 |
| 前端 | **Vanilla JS ES Modules，無 build step** | 本機工具，避免 node_modules/打包鏈；FastAPI 直接 serve 靜態檔 |
| 幾何輸入（headless） | manifest 幾何參數接受 `{file_3dm, layer}` 或 `{encoded:[rhino3dm JSON]}` 兩種形式 | AI/MCP 無 Rhino 場景可選取；.3dm 路徑最實用，encoded 供既有 C# plugin 之後串接 |
| 文字輸出 | 執行結果寫成 `.3dm`，字串輸出以 `ObjectAttributes.SetUserString` 附著於幾何（AttributeUserText） | 規格明訂，rhino3dm 可純 Python 完成 |
| 埠號 | HOGER 後端 **8600**；Rhino.Compute 5000（既有）；避開 ai_bridge 8765 | 不與現有系統衝突 |

**沿用 v1.0.3 已驗證的序列化規則**（來源：`compute_core.py` + rhino-compute-hops-bridge skill）：
- Boolean → `{"type":"System.Boolean","data":"true"/"false"}`（小寫字串）
- 整數值 float（18.0）→ `System.Int32` + int（避免 GH Integer input `ReadAsInt32("18.0")` crash）
- String/路徑 → `json.dumps(value)` 包一層（兩層 JSON 解碼規則）
- 幾何 → `obj.Encode()` + 完整 .NET type 名稱（`Rhino.Geometry.Brep` 等）
- GH 檔案輸入端必須是標準 Hops Get 元件（`IGH_ContextualParameter`），輸出端必須是 Context Bake / `RH_OUT:` 群組

---

## 1. 專案結構

```
C:\Users\User\Desktop\Hoger\
├── hoger\                          # Python 套件
│   ├── __init__.py
│   ├── config.py                   # 埠號、compute URL、路徑常數（env 可覆寫）
│   ├── core\
│   │   ├── __init__.py
│   │   ├── compute_client.py       # POST /io、POST /grasshopper、health check
│   │   ├── type_mapping.py         # /io ParamType → 內部 kind → JSON Schema
│   │   ├── trees.py                # geometry/scalar/string DataTree builder（移植 compute_core）
│   │   ├── manifest.py             # ToolManifest pydantic model + 驗證 + /io 回應 → manifest
│   │   ├── executor.py             # manifest + args → trees → compute → parsed outputs
│   │   └── results.py              # 輸出解析 + AttributeUserText .3dm writer
│   ├── store\
│   │   ├── __init__.py
│   │   └── tool_store.py           # tools/*.json CRUD（list/get/save/delete）
│   ├── api\
│   │   ├── __init__.py
│   │   ├── app.py                  # FastAPI app 組裝：routes + 靜態 UI + MCP HTTP mount
│   │   └── routes.py               # /api/* 端點
│   ├── mcp_server\
│   │   ├── __init__.py
│   │   ├── server.py               # low-level MCP Server：動態 list_tools/call_tool
│   │   └── stdio_main.py           # python -m hoger.mcp_server.stdio_main（Claude Desktop 入口）
│   └── hops\
│       ├── __init__.py
│       └── hops_routes.py          # GET/POST /hops/{tool_id}：/io 相容格式（Hops 元件可直連）
├── webui\                          # 靜態 SPA（FastAPI mount 於 /）
│   ├── index.html
│   ├── style.css
│   └── js\
│       ├── app.js                  # 路由/頁籤切換 + API client
│       ├── convert.js              # 3A 轉換區
│       ├── manager.js              # 3B 工具管理區
│       └── tester.js               # 3C 測試區（動態表單）
├── tools\                          # 產生的 tool manifest（*.json）
├── generated\
│   ├── results\                    # 執行結果 .3dm
│   └── mcp_config\                 # claude_desktop_config 片段
├── tests\
│   ├── fixtures\                   # 錄製的 /io、/grasshopper 回應 JSON
│   ├── test_type_mapping.py
│   ├── test_trees.py
│   ├── test_manifest.py
│   ├── test_executor.py
│   ├── test_results.py
│   ├── test_tool_store.py
│   ├── test_api.py                 # FastAPI TestClient
│   └── test_mcp_server.py
├── requirements.txt
├── run_hoger.ps1                   # 啟動腳本（uvicorn）
├── README.md
└── docs\
    └── USAGE.md                    # 使用說明（GH 檔案準備規則 + UI 操作 + MCP 接入）
```

**模組相依方向**（單向，不可反向 import）：

```
webui (HTTP) ─→ api ─→ store ─→ core
mcp_server ────────→ store ─→ core
hops ──────────────→ store ─→ core
```

---

## 2. 各模組職責與介面

### 2.1 `core/compute_client.py`
| 函式 | 簽名 | 職責 |
|---|---|---|
| `health()` | `() -> bool` | GET `{compute_url}/version`，判斷 Rhino.Compute 是否在線 |
| `io_query(gh_path)` | `(str) -> dict` | 讀檔 → base64 → POST `/io` → 回傳原始 JSON；HTTP 非 2xx 或空 body 時 raise `ComputeError`（含狀態碼與 body 前 2000 字） |
| `evaluate(gh_path, trees)` | `(str, list[dict]) -> dict` | base64 → POST `/grasshopper`（`{"algo":..., "pointer":None, "values":[...]}`，timeout 600s）；收集 errors/warnings 附於例外或回傳 |

### 2.2 `core/type_mapping.py`
| 函式 | 職責 |
|---|---|
| `classify(param_type: str) -> str` | /io 的 `ParamType`（Number/Integer/Boolean/String/Curve/Brep/Mesh/Geometry/...）→ 內部 kind：`number` `integer` `boolean` `string` `geometry` |
| `to_json_schema(input_spec) -> dict` | manifest input → MCP inputSchema property（含 description、default、minimum/maximum、幾何參數的 object schema） |
| `net_type_for(kind_or_typename) -> str` | rhino3dm 型別名 → 完整 .NET 名稱（移植 `_NET_TYPE` 表） |

幾何參數的 JSON Schema（AI 二擇一提供）：
```json
{
  "type": "object",
  "properties": {
    "file_3dm": {"type": "string", "description": "Rhino .3dm 檔案絕對路徑"},
    "layer":    {"type": "string", "description": "（選填）只取此圖層的物件"},
    "encoded":  {"type": "array", "items": {"type": "string"},
                 "description": "（替代）rhino3dm JSON 編碼的幾何物件列表"}
  }
}
```

### 2.3 `core/manifest.py` — 工具定義的單一真相
```python
class InputSpec(BaseModel):
    param_name: str          # GH 原名，底線原樣保留（_geometry、context_）
    label: str = ""          # UI 顯示名
    kind: str                # number|integer|boolean|string|geometry
    param_type: str          # /io 原始 ParamType（保留供除錯）
    description: str = ""
    required: bool = True
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    enum_values: list[str] | None = None   # ValueList 選項（/io 有暴露時帶入；UI 可手動補）
    at_least: int = 1        # /io AtLeast/AtMost（item access 資訊）
    at_most: int | None = 1

class OutputSpec(BaseModel):
    param_name: str          # Context Bake NickName（含 RH_OUT: 前綴移除後的名稱）
    kind: str                # number|integer|string|geometry
    description: str = ""
    unit: str = ""

class ToolManifest(BaseModel):
    id: str                  # kebab-case，由檔名自動產生、可改
    display_name: str
    description: str
    gh_file: str             # 絕對路徑
    status: str = "draft"    # draft | registered
    inputs: list[InputSpec]
    outputs: list[OutputSpec]
    created_at: str
    updated_at: str

def manifest_from_io(gh_path: str, io_response: dict) -> ToolManifest: ...
def to_mcp_tool(m: ToolManifest) -> dict:   # {"name","description","inputSchema"}
```
`manifest_from_io` 對 /io 回應做**防禦性解析**：欄位名以 `Name`/`Nickname`/`Description`/`ParamType`/`Default`/`Minimum`/`Maximum`/`AtLeast`/`AtMost` 為主，缺欄位一律容錯（Phase 1 會先錄製真實回應為 fixture，再鎖定欄位名）。

### 2.4 `core/trees.py` — 直接移植 compute_core 已驗證邏輯
`geometry_tree(name, objs)`、`scalar_tree(name, value)`、`string_tree(name, value)`、`encoded_tree(name, encoded_list)`（接受已編碼 JSON 字串直接包 InnerTree，不 decode/re-encode）。
**實作註記（已定案）**：tree 為純 dict `{"ParamName":..., "InnerTree": {"{0}": [...]}}`，不使用 compute_rhino3d 的 DataTree 類別（避免其 stdout/patch 問題）；`compute_client.evaluate` 直接收這些 dict。

### 2.5 `core/executor.py`
```python
def build_trees(manifest, args: dict) -> list:   # 每個 input 依 kind 選 tree builder；
                                                 # geometry: file_3dm→rhino3dm.File3dm 讀取（可 layer 過濾）
                                                 # 或 encoded→encoded_tree；缺 required → ToolArgError
def run_tool(manifest, args: dict) -> ToolResult:
    # build_trees → compute_client.evaluate → results.parse → results.write_result_3dm
    # ToolResult: {outputs: dict, result_3dm: str|None, elapsed_ms: int,
    #              errors: [...], warnings: [...], raw: dict}
```

### 2.6 `core/results.py`
- `parse(res, manifest) -> dict`：移植 `parse_outputs`，改為 list 型輸出（同名多值不覆蓋）；幾何 `rhino3dm.CommonObject.Decode`。
- `write_result_3dm(outputs, manifest, out_dir) -> str | None`：**AttributeUserText 機制**——建 `rhino3dm.File3dm()`，逐一加入幾何輸出；所有 `string` kind 的輸出以 `ObjectAttributes.SetUserString(param_name, value)` 附著到幾何物件上（無幾何輸出時附著到一個原點 Point 上），存至 `generated/results/{tool_id}_{timestamp}.3dm`。純文字絕不只以字串回傳。

### 2.7 `store/tool_store.py`
`list_tools()` / `get(tool_id)` / `save(manifest)` / `delete(tool_id)`；檔名 = `tools/{tool_id}.json`；`save` 時更新 `updated_at`。無快取——每次讀磁碟，讓 MCP server 與 backend 兩個進程天然同步。

### 2.8 `api/routes.py` — REST API 契約
| Method | Path | 職責 |
|---|---|---|
| GET | `/api/health` | HOGER + Rhino.Compute 狀態 |
| POST | `/api/import` | multipart 上傳 .gh（或 body 給絕對路徑）→ 存檔 → `/io` 解析 → 回傳 draft manifest（**不落地**） |
| POST | `/api/tools` | 儲存 manifest（draft→registered 亦走此） |
| GET | `/api/tools` | 工具清單（id、name、status、I/O 數量） |
| GET | `/api/tools/{id}` | 完整 manifest + 即時產生的 MCP Tool Schema 預覽 |
| PUT | `/api/tools/{id}` | 更新（description/default/min/max/label…）|
| DELETE | `/api/tools/{id}` | 刪除 |
| POST | `/api/tools/{id}/run` | 測試執行：body = args → `executor.run_tool` → ToolResult JSON |
| GET | `/api/mcp-config` | 產生 claude_desktop_config.json 片段（stdio + HTTP 兩種）|

### 2.9 `mcp_server/server.py`
- low-level `mcp.server.Server("hoger")`
- `@server.list_tools()`：`tool_store.list_tools()` 中 `status=="registered"` 者 → `types.Tool(name=m.id, description=..., inputSchema=to_mcp_tool(m)["inputSchema"])`
- `@server.call_tool()`：`tool_store.get(name)` → `executor.run_tool` → 回傳 `types.TextContent`（outputs JSON、result_3dm 路徑、elapsed）；例外 → `isError=True`
- stdio 入口：`stdio_main.py`（`mcp.server.stdio.stdio_server`）
- HTTP 入口：`StreamableHTTPSessionManager` 包裝同一個 Server，mount 到 FastAPI `/mcp`

### 2.10 `hops/hops_routes.py`（Phase 6）
- `GET /hops/{tool_id}`：回傳 Hops 元件定義（/io 相容 JSON：Description、Inputs[Name/Nickname/Description/ParamType/Default/Minimum/Maximum]、Outputs）
- `POST /hops/{tool_id}/solve`：接受 Hops solve 請求（`values` 陣列 InnerTree **原樣 passthrough** 給 executor——遵守 skill Rule 3，不 decode/re-encode）
- 效果：Grasshopper 內放一個 Hops 元件、指向 `http://localhost:8600/hops/radiation` 即可使用

---

## 3. 資料流程圖

### 3.1 匯入轉換流程
```
使用者拖放 .gh
   │
   ▼
Web UI ── POST /api/import ──→ FastAPI
                                  │ base64(.gh)
                                  ▼
                        Rhino.Compute POST /io  (localhost:5000)
                                  │ {Inputs:[{Name,ParamType,Default,...}], Outputs:[...]}
                                  ▼
                        manifest_from_io()  →  draft ToolManifest
                                  │
   Web UI 顯示解析結果 ◄──────────┘
   使用者微調 description/default/min-max
   │
   ▼
POST /api/tools (status=registered) ──→ tools/{id}.json 落地
                                          │
                     MCP list_tools 下次呼叫即包含此工具（無需重啟）
```

### 3.2 MCP 執行流程
```
Claude Desktop / Cursor
   │ tools/call {name:"radiation", arguments:{_geometry:{file_3dm:"C:\\model.3dm"}, _grid_size:2}}
   ▼
MCP Server (stdio 或 http://localhost:8600/mcp)
   │ tool_store.get("radiation") → manifest
   ▼
executor.build_trees()
   │  _geometry → File3dm 讀取 → geometry_tree（Encode + .NET type）
   │  _grid_size → scalar_tree（Int32/Double 規則）
   ▼
compute_client.evaluate() ── POST /grasshopper ──→ Rhino.Compute ──→ GH headless 運算
   ▼
results.parse()  →  results.write_result_3dm()
   │                    └─ 字串輸出 → ObjectAttributes.SetUserString（AttributeUserText）
   ▼
MCP TextContent {outputs, result_3dm, elapsed_ms}
```

---

## 4. 分階段實作順序

依賴順序：Phase 1（core 解析）→ Phase 2（執行）→ Phase 3（store+API）→ Phase 4（MCP）→ Phase 5（Web UI）→ Phase 6（Hops + 文件）。每 Phase 結束都有可獨立驗證的成果。

---

### Phase 0：專案骨架

#### Task 0.1: 建立套件結構與環境

**Files:**
- Create: `requirements.txt`, `hoger/__init__.py`, `hoger/config.py`, 各子套件 `__init__.py`, `run_hoger.ps1`, `.gitignore`

- [ ] **Step 1: 建立目錄與 requirements.txt**

```
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
requests>=2.32
rhino3dm>=8.9
compute-rhino3d>=0.12
mcp>=1.9
python-multipart>=0.0.9
pytest>=8.2
httpx>=0.27
```

- [ ] **Step 2: `hoger/config.py`**

```python
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPUTE_URL = os.environ.get("HOGER_COMPUTE_URL", "http://localhost:5000")
HOGER_PORT = int(os.environ.get("HOGER_PORT", "8600"))
TOOLS_DIR = Path(os.environ.get("HOGER_TOOLS_DIR", ROOT / "tools"))
RESULTS_DIR = Path(os.environ.get("HOGER_RESULTS_DIR", ROOT / "generated" / "results"))
GH_FILES_DIR = Path(os.environ.get("HOGER_GH_DIR", ROOT / "gh_files"))
for _d in (TOOLS_DIR, RESULTS_DIR, GH_FILES_DIR):
    _d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 3: 建 venv、安裝依賴、`git init`、首次 commit**

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
git init; git add -A; git commit -m "chore: HOGER scaffolding"
```

---

### Phase 1：核心解析（compute_client + type_mapping + manifest）

#### Task 1.1: compute_client（TDD，fixture 先行）

**Files:**
- Create: `hoger/core/compute_client.py`, `tests/test_compute_client.py`, `tests/fixtures/io_response_sample.json`

- [ ] **Step 1: 錄製真實 /io 回應作為 fixture**（Rhino.Compute 運行中時執行一次；離線則先用手寫樣本，欄位含 `Description/InputNames/OutputNames/Inputs/Outputs`，Input 物件含 `Name/Nickname/Description/ParamType/Default/Minimum/Maximum/AtLeast/AtMost`）

```powershell
# 錄製腳本（scripts/record_io.py）：對現有 radiation_study_hops.gh 打 /io，存 tests/fixtures/io_response_sample.json
.\.venv\Scripts\python scripts\record_io.py "C:\Users\User\Desktop\rhino.compute.test\radiation_study_hops.gh"
```

- [ ] **Step 2: 失敗測試** — `io_query` 對 mock 回應回傳 dict；空 body raise `ComputeError`

```python
def test_io_query_parses_json(monkeypatch):
    def fake_post(url, **kw):
        assert url.endswith("/io")
        return FakeResp(200, json.dumps(SAMPLE_IO))
    monkeypatch.setattr("requests.post", fake_post)
    out = compute_client.io_query(str(SAMPLE_GH))
    assert "Inputs" in out

def test_io_query_empty_body_raises(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp(500, ""))
    with pytest.raises(compute_client.ComputeError):
        compute_client.io_query(str(SAMPLE_GH))
```

- [ ] **Step 3: 實作** `health()` / `io_query()` / `evaluate()`（`evaluate` 移植 `compute_core.evaluate`：base64、timeout 600、errors/warnings 收集進回傳 dict 而非 print）
- [ ] **Step 4: `pytest tests/test_compute_client.py -v` 通過 → commit**

#### Task 1.2: type_mapping（TDD）

**Files:** `hoger/core/type_mapping.py`, `tests/test_type_mapping.py`

- [ ] **Step 1: 失敗測試** — 涵蓋所有 kind 分類與 JSON Schema 產生

```python
@pytest.mark.parametrize("pt,kind", [
    ("Number","number"),("Integer","integer"),("Boolean","boolean"),
    ("String","string"),("Text","string"),("FilePath","string"),
    ("Brep","geometry"),("Mesh","geometry"),("Curve","geometry"),
    ("Geometry","geometry"),("Point","geometry"),("Surface","geometry"),
    ("ValueList","string"),    # 枚舉：kind 為 string，選項存 InputSpec.enum_values
    ("UnknownXyz","string"),   # 未知型別 fallback string 並記 warning
])
def test_classify(pt, kind):
    assert type_mapping.classify(pt) == kind

def test_value_list_schema_has_enum():
    spec = InputSpec(param_name="_mode", kind="string", param_type="ValueList",
                     enum_values=["north","south","east","west"])
    s = type_mapping.to_json_schema(spec)
    assert s["enum"] == ["north","south","east","west"]

def test_number_schema_with_bounds():
    spec = InputSpec(param_name="_grid_size", kind="number", param_type="Number",
                     default=1.0, minimum=0.1, maximum=50.0, description="網格大小")
    s = type_mapping.to_json_schema(spec)
    assert s == {"type":"number","description":"網格大小","default":1.0,
                 "minimum":0.1,"maximum":50.0}

def test_geometry_schema_has_file_and_encoded():
    spec = InputSpec(param_name="_geometry", kind="geometry", param_type="Brep")
    s = type_mapping.to_json_schema(spec)
    assert set(s["properties"]) == {"file_3dm","layer","encoded"}
```

- [ ] **Step 2: 實作 → 測試通過 → commit**

#### Task 1.3: manifest + manifest_from_io（TDD）

**Files:** `hoger/core/manifest.py`, `tests/test_manifest.py`

- [ ] **Step 1: 失敗測試** — 以 fixture 的 /io 回應驗證：
  - 底線命名原樣保留（`_geometry`、`context_`）
  - Default/Minimum/Maximum 正確帶入、無值容錯
  - `to_mcp_tool()` 產出 `{"name","description","inputSchema":{"type":"object","properties":{...},"required":[...]}}`，required 只含 `required=True` 且無 default 的參數
- [ ] **Step 2: 實作（含 kebab-case id 產生：檔名 → 小寫、空白/底線→`-`）→ 通過 → commit**

---

### Phase 2：執行引擎（trees + executor + results）

#### Task 2.1: trees（移植 + TDD 鎖定序列化規則）

**Files:** `hoger/core/trees.py`, `tests/test_trees.py`

- [ ] **Step 1: 失敗測試** — 把 v1.0.3 踩過的坑全部寫成測試鎖住：

```python
def test_bool_is_lowercase_string():
    t = trees.scalar_tree("_run", True)
    assert t["InnerTree"]["{0}"][0] == {"type":"System.Boolean","data":"true"}

def test_whole_float_becomes_int32():
    t = trees.scalar_tree("_month", 18.0)
    assert t["InnerTree"]["{0}"][0] == {"type":"System.Int32","data":18}

def test_true_float_is_double():
    assert trees.scalar_tree("_gs", 1.5)["InnerTree"]["{0}"][0]["type"] == "System.Double"

def test_string_double_encoded():
    t = trees.string_tree("_epw", r"C:\weather\taipei.epw")
    assert t["InnerTree"]["{0}"][0]["data"] == json.dumps(r"C:\weather\taipei.epw")

def test_geometry_uses_net_type_and_encoded_json():
    bbox = rhino3dm.BoundingBox(rhino3dm.Point3d(0,0,0), rhino3dm.Point3d(10,10,10))
    brep = rhino3dm.Brep.CreateFromBox(bbox)
    item = trees.geometry_tree("_geometry", [brep])["InnerTree"]["{0}"][0]
    assert item["type"] == "Rhino.Geometry.Brep"
    assert json.loads(item["data"])   # data 是合法 JSON 字串

def test_encoded_tree_passthrough_no_reencode():
    raw = '{"version":10070,"archive3dm":70,"opennurbs":0,"data":"...abc"}'
    item = trees.encoded_tree("_geometry", [raw])["InnerTree"]["{0}"][0]
    assert item["data"] == raw        # 原樣，不 decode/re-encode
```

- [ ] **Step 2: 從 `compute_core.py` 移植實作 + 新增 `encoded_tree` → 通過 → commit**

#### Task 2.2: results（解析 + AttributeUserText writer，TDD）

**Files:** `hoger/core/results.py`, `tests/test_results.py`, `tests/fixtures/grasshopper_response_sample.json`

- [ ] **Step 1: 失敗測試**
  - `parse`：fixture 回應 → 數值/字串/幾何正確解出，list 型輸出保留多值
  - `write_result_3dm`：字串輸出 `{"report":"總輻射 123 kWh"}` + 一個 mesh 輸出 → 產出 .3dm；用 rhino3dm 讀回，驗證 `obj.Attributes.GetUserString("report") == "總輻射 123 kWh"`
  - 無幾何輸出時：字串附著在原點 Point 物件上，仍可讀回
- [ ] **Step 2: 實作（移植 `parse_outputs` 改 list 型；File3dm + SetUserString）→ 通過 → commit**

#### Task 2.3: executor（TDD）

**Files:** `hoger/core/executor.py`, `tests/test_executor.py`

- [ ] **Step 1: 失敗測試**
  - `build_trees`：manifest（number/bool/string/geometry 各一）+ args → 正確 tree 列表；缺 required 幾何 → `ToolArgError`；有 default 的參數未提供 → 用 default 建 tree
  - geometry args：`{"file_3dm": ...}` → monkeypatch `rhino3dm.File3dm.Read` 驗證 layer 過濾；`{"encoded":[...]}` → 走 `encoded_tree`
  - `run_tool`：mock `compute_client.evaluate` 回 fixture → ToolResult 含 outputs/result_3dm/elapsed_ms；evaluate raise → errors 填入且不 crash
- [ ] **Step 2: 實作 → 通過 → commit**

#### Task 2.4: 端到端煙霧測試（需 Rhino.Compute 在線，pytest marker 隔離）

- [ ] `@pytest.mark.integration`：對現有 `radiation_study_hops.gh` 跑 io_query → manifest → run_tool（以簡單 box .3dm 當輸入），驗證有回傳值。`pytest -m integration` 手動執行；CI/預設跳過。
- [ ] Commit。

---

### Phase 3：工具庫 + 後端 API

#### Task 3.1: tool_store（TDD）

**Files:** `hoger/store/tool_store.py`, `tests/test_tool_store.py`

- [ ] save→get roundtrip、list 排序（updated_at desc）、delete、get 不存在 → `ToolNotFound`；`tmp_path` 隔離 TOOLS_DIR。實作 → 通過 → commit。

#### Task 3.2: FastAPI routes（TDD，`fastapi.testclient`）

**Files:** `hoger/api/app.py`, `hoger/api/routes.py`, `tests/test_api.py`

- [ ] **Step 1: 失敗測試**
  - `POST /api/import`（mock io_query）→ 200 + draft manifest JSON，且 `tools/` 未落地
  - `POST /api/tools` → 落地；`GET /api/tools` 出現；`PUT` 改 description 後 `GET /api/tools/{id}` 的 `mcp_schema` 預覽同步變更
  - `POST /api/tools/{id}/run`（mock executor）→ ToolResult JSON
  - `GET /api/mcp-config` → 含 stdio（`command: python`, `args: ["-m","hoger.mcp_server.stdio_main"]`, `env: {HOGER_TOOLS_DIR: ...}`）與 HTTP（`url: http://localhost:8600/mcp`）兩段
- [ ] **Step 2: 實作。`app.py` 組裝：`include_router(api)` + `app.mount("/", StaticFiles(directory="webui", html=True))`（static mount 必須最後）。通過 → commit**

---

### Phase 4：MCP Server（stdio + Streamable HTTP）

> 實作前用 `gemini -y -p "搜尋 python mcp sdk low-level Server list_tools call_tool StreamableHTTPSessionManager 最新用法"` 確認當前 SDK API（SDK 迭代快，勿憑記憶）。

#### Task 4.1: 動態工具註冊（TDD）

**Files:** `hoger/mcp_server/server.py`, `tests/test_mcp_server.py`

- [ ] **Step 1: 失敗測試**（直接呼叫 handler 函式，不起 transport）
  - `list_tools`：store 有 1 registered + 1 draft → 只回 registered；Tool.inputSchema == `to_mcp_tool` 產物
  - `call_tool`：mock `executor.run_tool` → 回傳 TextContent，內容 JSON 含 outputs 與 result_3dm；未知工具名/執行例外 → isError 回應
- [ ] **Step 2: 實作**

```python
from mcp.server import Server
import mcp.types as types

server = Server("hoger")

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(name=m.id,
                       description=f"{m.display_name} — {m.description}",
                       inputSchema=to_mcp_tool(m)["inputSchema"])
            for m in tool_store.list_tools() if m.status == "registered"]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    m = tool_store.get(name)
    result = await anyio.to_thread.run_sync(lambda: executor.run_tool(m, arguments))
    return [types.TextContent(type="text", text=json.dumps({
        "outputs": result.outputs, "result_3dm": result.result_3dm,
        "elapsed_ms": result.elapsed_ms, "warnings": result.warnings,
    }, ensure_ascii=False, default=str))]
```

- [ ] **Step 3: 通過 → commit**

#### Task 4.2: stdio 入口 + HTTP mount

**Files:** `hoger/mcp_server/stdio_main.py`, Modify: `hoger/api/app.py`

- [ ] stdio：`mcp.server.stdio.stdio_server` 標準樣板；**stderr 打 log、stdout 絕不 print**（會污染 JSON-RPC）
- [ ] HTTP：`StreamableHTTPSessionManager(app=server)` mount 至 FastAPI `/mcp`（lifespan 啟動 session manager）
- [ ] **驗證**：`npx @modelcontextprotocol/inspector python -m hoger.mcp_server.stdio_main` 看到 tools/list；HTTP 模式用 inspector 連 `http://localhost:8600/mcp`
- [ ] Commit。

#### Task 4.3: MCP 設定檔產生器

- [ ] `GET /api/mcp-config` 已於 Task 3.2 完成；此處補 `generated/mcp_config/claude_desktop_config.snippet.json` 落地寫檔 + 測試。Commit。

---

### Phase 5：Web UI（HOGER Dashboard）

> 實作時使用 frontend-design skill。單頁三頁籤：轉換｜工具管理｜測試。所有互動走 `/api/*`。

#### Task 5.1: 骨架 + API client + 健康狀態列

**Files:** `webui/index.html`, `webui/style.css`, `webui/js/app.js`

- [ ] 頁首常駐狀態列：HOGER ●／Rhino.Compute ●（`/api/health`，10s 輪詢）；頁籤路由（hash-based）；`api.js` 統一 fetch + 錯誤 toast。手動驗證後 commit。

#### Task 5.2: 轉換區（convert.js）

- [ ] 拖放/瀏覽 `.gh` → `POST /api/import` → 表格顯示解析出的 Inputs/Outputs（名稱、類型 badge、描述可編輯、default/min/max 可編輯）→「註冊到 MCP」按鈕 → `POST /api/tools`。錯誤情境：Compute 離線 → 顯示明確指引（「請先啟動 Rhino.Compute」）。Commit。

#### Task 5.3: 工具管理區（manager.js）

- [ ] 工具卡片清單（狀態 badge：registered/draft）→ 點選開編輯面板（同 5.2 的欄位編輯元件，DRY 共用）→ 儲存 `PUT /api/tools/{id}` → 右側即時 MCP Tool Schema JSON 預覽（`GET /api/tools/{id}` 的 `mcp_schema`）。刪除需確認。Commit。

#### Task 5.4: 測試區（tester.js — 動態表單）

- [ ] 選工具 → 依 manifest 動態產表單：
  - number/integer + min/max → range slider + number input 連動，顯示範圍與 default
  - boolean → toggle；string → text input；`param_type=="FilePath"` 或名稱含 epw/path → file path input
  - geometry → 「.3dm 路徑 + layer」輸入組 或 encoded JSON textarea（切換）
  - 每欄顯示 param_name、label、description
- [ ] 執行 → `POST /api/tools/{id}/run` → 顯示耗時、格式化輸出表、原始 JSON（collapsible）、result_3dm 路徑（可複製）
- [ ] Commit。

---

### Phase 6：Hops 端點 + 文件 + 收尾

#### Task 6.1: Hops 端點（hops_routes.py，TDD）

- [ ] **測試**：`GET /hops/{id}` 回傳 /io 相容 JSON（Inputs 的 Name=param_name、含 Default/Minimum/Maximum）；`POST /hops/{id}/solve` 收 Hops `values` → InnerTree **原樣 passthrough** 進 executor（新增 `run_tool_raw(manifest, raw_values)` 路徑）→ 回 Hops 格式回應
- [ ] 實作 → GH 內以 Hops 元件連 `http://localhost:8600/hops/{id}` 手動驗證 → commit

#### Task 6.2: 使用文件（docs/USAGE.md + README.md）

- [ ] 內容必含：
  1. **GH 檔案準備規則**（給工具作者）：輸入必須用標準 Hops Get 元件（NickName=參數名）；輸出必須用 Context Bake 或 `RH_OUT:` 群組；文字輸出一律經幾何 + AttributeUserText；注意 compute.geometry 預設 Millimeters（模型 Meters 時幾何會縮小 1000×，Ladybug 靜默回空值）
  2. 啟動順序：Rhino.Compute → `run_hoger.ps1` → 瀏覽器 `http://localhost:8600`
  3. Claude Desktop / Cursor 接入步驟（貼 `/api/mcp-config` 產出）
  4. 疑難排解表（沿用 skill 的「空輸出診斷表」）
- [ ] Commit。

#### Task 6.3: 全量驗證

- [ ] `pytest -v`（全綠）→ `pytest -m integration`（Compute 在線時）→ MCP Inspector 兩種 transport 各跑一次 tools/list + tools/call → Web UI 三區手動走一遍 → 最終 commit。

---

## 5. 風險與對策

| 風險 | 對策 |
|---|---|
| `/io` 回應欄位名隨 Rhino.Compute 版本變動 | Phase 1 先錄製真實回應為 fixture 鎖欄位；`manifest_from_io` 防禦性解析 + 未知 ParamType fallback string |
| MCP SDK API 迭代快 | Task 4.1 動手前用 Gemini 查最新 SDK 文件；handler 測試不綁 transport，transport 層薄 |
| 幾何 decode/re-encode 損壞（skill Rule 3） | Hops solve 路徑一律 passthrough（`encoded_tree`）；只有 file_3dm 路徑才 Encode（v1.0.3 已驗證此路可行） |
| 單位不一致（Millimeters 預設） | 文件明載 + `run_tool` 回傳 `modelunits` 供 UI 顯示警示 |
| 長時運算阻塞 | evaluate timeout 600s；FastAPI sync endpoint 自動進 threadpool；UI 顯示 spinner + 耗時 |
| stdout 污染 stdio JSON-RPC | stdio 模式全域 logging → stderr；core 層禁 print（compute_core 的 print 改 logging） |
