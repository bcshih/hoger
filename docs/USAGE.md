# HOGER 使用手冊

HOGER 把任意 Grasshopper（`.gh`）檔案自動轉換成兩種可被外部呼叫的介面：

- **Hops 端點**（`http://localhost:8600/hops/<tool-id>`）：Grasshopper 內用 Hops 元件直連。
- **MCP 工具**（stdio 或 Streamable HTTP）：Claude Desktop、Cursor 等 AI 用戶端呼叫。

底層都是同一份 `tools/*.json` 工具定義，執行時都會呼叫 Rhino.Compute。

---

## 1. 系統需求與啟動順序

### 1.1 啟動順序

1. **Rhino + Rhino.Compute**（compute.geometry）必須先啟動，監聽 `http://localhost:5000`（HOGER 預設值）。這通常是開啟 Rhino 後啟動 compute.geometry.exe，或既有的常駐 Rhino.Compute 服務；HOGER 本身不負責啟動它，只會呼叫它的 `/io`、`/grasshopper`、`/version` 端點。
2. **啟動 HOGER 後端**：
   ```powershell
   .\run_hoger.ps1
   ```
   實際上等同於：
   ```powershell
   .venv\Scripts\python.exe -m uvicorn hoger.api.app:app --host 127.0.0.1 --port 8600
   ```
3. **瀏覽器開啟** `http://localhost:8600`——會看到 Web UI（三個頁籤：轉換／工具管理／測試），頁首有 HOGER 與 Rhino.Compute 兩個狀態燈號（每 10 秒輪詢 `/api/health`）。

> HOGER 未偵測到 Rhino.Compute 時，Web UI 會在對應操作（匯入、執行測試）顯示明確提示：「請先啟動 compute.geometry（localhost:5000）」，而不是讓請求默默失敗。

### 1.2 環境變數

由 `hoger/config.py` 讀取，皆有預設值，可用系統環境變數覆寫：

| 環境變數 | 預設值 | 用途 |
|---|---|---|
| `HOGER_COMPUTE_URL` | `http://localhost:5000` | Rhino.Compute 的位址 |
| `HOGER_PORT` | `8600` | 供 `GET /api/mcp-config` 產生 HTTP 傳輸設定片段時使用的埠號 |
| `HOGER_TOOLS_DIR` | `<專案根>\tools` | 工具定義（`*.json`）存放目錄 |
| `HOGER_RESULTS_DIR` | `<專案根>\generated\results` | 執行結果 `.3dm` 輸出目錄 |
| `HOGER_GH_DIR` | `<專案根>\gh_files` | 上傳的 `.gh` 檔案存放目錄 |

**注意**：`run_hoger.ps1` 目前把 uvicorn 的 `--port` **寫死為 8600**，並不會讀取 `HOGER_PORT`。若要換一個實際監聽埠，除了設定 `HOGER_PORT`（讓 `/api/mcp-config` 產生的 HTTP 設定片段正確），還要同步修改 `run_hoger.ps1` 裡的 `--port` 參數（或改用你自己的 uvicorn 指令），兩邊沒有自動同步。

---

## 2. GH 檔案準備規則（給工具作者，最重要章節）

HOGER 是靠 Rhino.Compute 的 `/io` 端點解析 GH 檔案的輸入/輸出，而 `/io` 只認得特定元件類型。用一般的 GH 輸入/輸出參數或其他外掛的元件，HOGER **看不到**、AI 呼叫時也**拿不到資料**。

### 2.1 輸入端：必須用標準 Hops Get 元件

- 在 Grasshopper 的 **Params → Util**（Hops 圖示）分類下，放置對應型別的 `Get` 元件（`Get Number`、`Get Boolean`、`Get String`、`Get Brep`……）。
- 把該元件的 **NickName** 改成你要暴露的參數名（HOGER 會原樣保留底線，例如 `_geometry`、`_grid_size`、`context_`）。這個 NickName 就是之後 AI/Hops 呼叫時要填的 key。
- **插件提供的替代 Get 元件不會被辨識**——例如 Ladybug 的「Get EPW File Path」「Get Boolean」等，即使外觀類似，也不是 Rhino.Compute 掃描時認得的 `IGH_ContextualParameter` 實作。只有 Hops 外掛自帶的 Get 元件可以。混用會導致該輸入完全收不到資料，而且**不會報錯**——下游元件只是靜靜地拿到空值。

### 2.2 輸出端：必須用 Context Bake 或 `RH_OUT:` 群組

- 每個要暴露的輸出，接一個 **Context Bake** 元件（或 Context Print），NickName 就是輸出參數名；或者把產生輸出的元件放進一個命名為 `RH_OUT:<名稱>` 的 GH 群組（Group）。
- 一般的 GH 輸出參數（面板、單純的 Param 元件）**不會被暴露**，`/io` 解析不到，工具的 `outputs` 清單裡就不會有它。

### 2.3 文字輸出：一律經幾何 + AttributeUserText

HOGER 的設計是「執行結果一律落地成 `.3dm`」，**不會把字串當作獨立資料直接回傳**（雖然 JSON 回應裡也看得到字串值，但正式的資料通道是 `.3dm`）：

- 字串（`kind == string`）輸出會用 `rhino3dm.ObjectAttributes.SetUserString(param_name, value)` 附著在結果 `.3dm` 檔的幾何物件上（即 AttributeUserText 機制）。
- 若這次執行**沒有任何幾何輸出**，HOGER 會建立一個原點 `Point(0,0,0)` 物件，把所有字串 UserText 都附著在這個點上，確保文字結果一定讀得到、不會遺失。
- 所以：只要你的 GH 檔案有字串輸出，接到 Context Bake（或 `RH_OUT:` 群組）即可，其餘由 HOGER 自動處理，不需要額外設計。
- 提醒：`number` / `integer` / `boolean` 型別的輸出**不會**寫進 `.3dm`，只會出現在執行結果的 JSON `outputs` 裡——這是刻意設計（數值用 JSON 回傳就足夠，`.3dm` 只承載幾何與依附其上的文字）。

### 2.4 單位陷阱：compute.geometry 預設 Millimeters

- Rhino.Compute（compute.geometry）執行 GH 定義時預設模型單位是 **Millimeters**。如果你的 GH 檔案是用 **Meters** 建模，幾何座標會被誤讀成毫米，等於整個模型「縮小」1000 倍。
- 這種錯誤**通常不會報錯**——尤其像 Ladybug 這類分析元件，遇到尺度錯誤的幾何常常是靜默回傳空值或 0，不會丟例外。
- HOGER 的因應方式：執行結果會帶回 Rhino.Compute 回應中的 `modelunits` 欄位（`ToolResult.modelunits`），並且**只要不是 `"Meters"`**，Web UI 的測試頁籤會在結果區顯示醒目的橘色提示：「模型單位注意：模型單位為 XXX，請確認幾何尺度」。
- 若你的模型是 Meters，務必在送入 Rhino.Compute 前確認幾何尺度是否需要換算，或在 GH 檔案內部自行處理單位轉換。

---

## 3. Web UI 三區操作

瀏覽器開啟 `http://localhost:8600` 後有三個頁籤：**轉換 → 工具管理 → 測試**。

### 3.1 轉換（Import & Convert）

三階段流程：

1. **匯入**：兩種方式可切換——「檔案上傳」（拖放 `.gh` 檔案到框內，或按「瀏覽檔案」選檔；檔案會被存到 `gh_files/` 目錄）或「本機路徑」（直接輸入 HOGER 伺服器可存取的 `.gh` 絕對路徑，不會複製檔案）。送出後呼叫 `POST /api/import`，內部會打 Rhino.Compute 的 `/io` 端點解析。Rhino.Compute 離線時會顯示「請先啟動 compute.geometry（localhost:5000）」提示。
2. **檢視／編輯草稿**：解析成功後顯示草稿工具定義，可編輯 `id`（限小寫字母/數字/連字號，格式 `^[a-z0-9-]+$`）、`display_name`、`description`，以及每個輸入/輸出的 description、default、minimum、maximum 等欄位。
3. **註冊**：按「註冊到 MCP」送出 `POST /api/tools`，可選擇存成 `draft`（草稿，暫不對外開放）或 `registered`（已註冊——會立即出現在 MCP 工具清單與 `/hops/{id}`，不需要重啟服務，因為工具庫每次都直接讀磁碟）。

### 3.2 工具管理（Tool Manager）

- 左側是卡片式工具清單（`GET /api/tools`），每張卡片顯示名稱、狀態徽章（已註冊／草稿）、輸入輸出數量、更新時間。
- 點卡片開右側編輯面板（`GET /api/tools/{id}`），可編輯 display_name/description、每個輸入輸出的欄位，以及工具狀態（draft / registered）；`id` 與各參數的 `param_name` 唯讀，因為改名會破壞既有的 MCP/Hops 引用。
- 編輯面板旁即時顯示 **MCP Tool Schema 預覽**（本地即時重算，未儲存也看得到；儲存後的權威版本仍以後端 `to_mcp_tool()` 算出的為準）。
- 有未儲存變更時切換工具或重新整理清單，會跳出確認對話框（`確定要繼續嗎？`）避免誤丟修改。
- 刪除工具（`DELETE /api/tools/{id}`）前會要求二次確認（「確定要刪除工具『XXX』嗎？此操作無法復原」）。

### 3.3 測試（Test Harness）

- **選工具**：下拉選單只列出可執行的工具；`draft` 狀態的工具會顯示但停用，並註明「草稿，請先在工具管理區註冊」。
- **動態表單**：依選定工具的 manifest 自動產生輸入欄位：
  - `number`/`integer`：若同時有 `minimum` 與 `maximum`，顯示滑桿 + 數字輸入雙向連動；否則只有純數字輸入框。
  - `boolean`：toggle 開關。
  - `string`：若有 `enum_values` 顯示下拉選單；否則是文字輸入框（若參數名包含 `epw`/`path`/`file`，或 `param_type == "FilePath"`，會顯示路徑提示 placeholder）。
  - `geometry`：可切換「.3dm 檔案路徑」（含選填的圖層名稱）或「encoded JSON」（貼上 rhino3dm 編碼字串的 JSON 陣列，或每行一筆）兩種輸入模式。
  - 每個欄位都會顯示 `param_name`、型別徽章、必填標記與說明文字。
- **debug 模式**：勾選「debug 模式（回應含 raw）」後，執行請求會帶上 `?debug=true` 查詢參數，回應會多出 `raw` 欄位（Rhino.Compute 的原始回應，或失敗時的 `error_status_code`/`error_body`），方便疑難排解。
- **執行**：按「執行測試」呼叫 `POST /api/tools/{id}/run`（逾時設定 620 秒，對應後端 Rhino.Compute 呼叫的 600 秒逾時）。若 Rhino.Compute 目前離線，按鈕會被停用並顯示提示。
- **結果檢視**：顯示成功/失敗徽章、耗時（秒）、模型單位（`modelunits`，非 Meters 時有橘色警示）、錯誤／警告訊息列表、輸出表格（幾何型別顯示「N 個物件（已寫入/未寫入 3dm）」；其餘型別顯示值，超過 10 筆的陣列可展開）、**結果 `.3dm` 路徑**（附「複製路徑」按鈕）、以及可展開的完整原始 JSON。

---

## 4. 接入 Claude Desktop（stdio）

HOGER 提供一鍵取得設定片段的端點：

```
GET http://localhost:8600/api/mcp-config
```

呼叫後，除了回傳 JSON，也會同步把設定片段寫到 `generated/mcp_config/`（每次查詢都會刷新，確保與目前 `HOGER_COMPUTE_URL`／`HOGER_PORT`／專案路徑同步）：

- `generated/mcp_config/claude_desktop_config.snippet.json` — stdio 設定片段
- `generated/mcp_config/http_client_config.snippet.json` — HTTP 設定片段

### 4.1 stdio 設定片段實際內容

以本機環境（`C:\Users\User\Desktop\Hoger`）為例，`claude_desktop_config.snippet.json` 內容如下（路徑會依你實際的專案根目錄自動產生）：

```json
{
  "mcpServers": {
    "hoger": {
      "command": "C:\\Users\\User\\Desktop\\Hoger\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "hoger.mcp_server.stdio_main"
      ],
      "cwd": "C:\\Users\\User\\Desktop\\Hoger",
      "env": {
        "HOGER_COMPUTE_URL": "http://localhost:5000"
      }
    }
  }
}
```

把 `mcpServers` 底下的 `"hoger": {...}` 這個區塊，貼進 Claude Desktop 設定檔（`claude_desktop_config.json`，一般在 `%APPDATA%\Claude\claude_desktop_config.json`）的 `mcpServers` 物件裡即可（若原本已有其他 MCP server，用合併方式加進去，不要整份覆蓋）。

存檔後重新啟動 Claude Desktop，新增的工具（狀態為 `registered` 的）就會出現在可用工具清單中。stdio 入口是 `python -m hoger.mcp_server.stdio_main`：所有 log 導向 stderr，stdout 完全保留給 JSON-RPC 通訊，因此不需要另外啟動 HOGER 的 uvicorn 服務——Claude Desktop 會直接透過這個指令啟動一個獨立的 Python 行程。但仍然需要 Rhino.Compute 在線，否則實際執行工具時會失敗。

### 4.2 也可以直接讀取產生的檔案

若不想呼叫 API，也可以直接開啟專案內已存在的 `generated/mcp_config/claude_desktop_config.snippet.json`（首次執行過 `/api/mcp-config` 或啟動過 HOGER 後就會存在），內容與上方範例一致。

---

## 5. 接入 Cursor / 其他 HTTP MCP 客戶端

HOGER 的 MCP Server 同時以 **Streamable HTTP** 掛載在 FastAPI 的 `/mcp` 路徑下，不需要另外啟動行程——只要 HOGER 後端（`run_hoger.ps1`）正在執行即可。

`http_client_config.snippet.json` 內容：

```json
{
  "mcpServers": {
    "hoger": {
      "url": "http://localhost:8600/mcp"
    }
  }
}
```

把這個 URL 填進支援 Streamable HTTP 傳輸的 MCP 客戶端（例如 Cursor）設定中。

**注意**：這個模式下 HOGER 服務（`run_hoger.ps1` 啟動的 uvicorn）必須維持運行中，因為 `/mcp` 是掛載在同一個 FastAPI app 上，不是獨立行程。

---

## 6. AI 呼叫工具時的參數格式

不論透過 stdio 或 HTTP，MCP 的 `tools/call` 參數（`arguments`）都是依 manifest 的 `inputs` 動態產生 JSON Schema，鍵名就是 GH 檔案裡的參數名（例如 `_geometry`、`_grid_size`）。各型別的填法：

- **數值**（`number`/`integer`）、**布林**（`boolean`）、**字串**（`string`）：直接給對應的 JSON 值，例如 `{"_grid_size": 2, "_run": true}`。
- **幾何**（`kind == "geometry"`）：是一個物件，二擇一提供：
  - `{"file_3dm": "C:\\path\\model.3dm", "layer": "選填圖層名"}` — 從指定 `.3dm` 檔案讀取幾何（`layer` 可省略；省略時取檔案內所有物件）。若指定的 `layer` 在檔案中不存在，或 required 參數讀不到任何物件，會回傳參數錯誤。
  - `{"encoded": ["<rhino3dm JSON 編碼字串>", "..."]}` — 直接提供已用 rhino3dm 編碼（`.Encode()` 後 `json.dumps()`）的幾何物件字串陣列，供既有工具鏈（例如 C# 外掛）串接使用。

範例（呼叫一個名為 `radiation-study` 的工具）：

```json
{
  "name": "radiation-study",
  "arguments": {
    "_geometry": {"file_3dm": "C:\\models\\site.3dm", "layer": "roofs"},
    "context_": {"file_3dm": "C:\\models\\site.3dm", "layer": "context"},
    "_grid_size": 2.0,
    "_run": true
  }
}
```

回傳的 `TextContent` 是一段 JSON 字串，內容包含：

```json
{
  "outputs": { "...": "..." },
  "result_3dm": "C:\\...\\generated\\results\\radiation-study_20260704_120000_000000.3dm",
  "elapsed_ms": 1234,
  "warnings": [],
  "errors": [],
  "modelunits": "Millimeters"
}
```

其中幾何型別的輸出在 `outputs` 裡不是幾何本身，而是 `{"count": N, "in_3dm": true}`；實際幾何要從 `result_3dm` 指向的檔案讀取。字串輸出則以 `AttributeUserText` 形式附著在該 `.3dm` 檔的幾何（或原點 Point）物件上——AI 若要讀取文字結果，需要另外用 rhino3dm（或請使用者在 Rhino 開啟該檔案）讀取 `.3dm` 並用 `GetUserString(<param_name>)` 取值；`outputs` JSON 裡雖然也能看到字串值本身，但正式的持久化資料通道是 `.3dm`。

---

## 7. Grasshopper 內使用（Hops 元件）

除了給 AI 用，任何已 **註冊（registered）** 的工具也能直接在 Grasshopper 裡當一般 Hops 元件使用：

1. 在 Grasshopper 放一個 Hops 元件（Params → Util → Hops）。
2. 雙擊該元件，Path 填入：
   ```
   http://localhost:8600/hops/<tool-id>
   ```
   `<tool-id>` 就是工具管理頁籤裡看到的工具 id（kebab-case，例如 `radiation-study`）。
3. Hops 元件會自動對這個 URL 發 `GET` 取得輸入/輸出定義，並依此長出對應的輸入輸出接頭。
4. 連上幾何/數值後即可像本地元件一樣求值——實際運算仍會轉發到 Rhino.Compute 執行。

**已知限制**：HOGER 的 `GET /hops/{tool_id}` 回應只包含 `Description`/`InputNames`/`OutputNames`/`Inputs`/`Outputs` 幾個欄位，**不含** Rhino.Compute 原生 `/io` 回應會有的 `CacheKey` 與各輸入的 `ResultType` 欄位。多數情況下 Hops 元件不需要這兩個欄位也能正常運作；但如果你的 Hops 版本對定義做了額外檢查、行為異常（例如元件顯示錯誤或無法產生預期的接頭類型），這可能是原因之一，請回報實際 Hops/Rhino 版本以便排查。另外，HOGER 的 Hops solve 是無狀態的一次性求值，不支援 Rhino.Compute 的 `pointer`／CacheKey 快取重算機制。

只有 `status == "registered"` 的工具能透過 `/hops/{tool_id}` 存取；草稿（draft）或不存在的工具一律回傳 404（不區分兩者，避免洩漏工具是否存在的細節）。

---

## 8. 疑難排解

| 症狀 | 可能原因 / 排查方式 |
|---|---|
| 輸出全部是空值，但沒有任何錯誤訊息 | 最常見的原因（依可能性排序）：① 輸入格式不對（幾何要用 `file_3dm`/`encoded`，且需先確認能載入非空物件）；② **模型單位不一致**——GH 檔案是 Meters、Rhino.Compute 卻用 Millimeters 解讀，幾何被縮放 1000 倍後分析元件靜默回空值（見第 2.4 節，可從測試頁籤的 `modelunits` 提示確認）；③ GH 檔案的輸入端 NickName 與呼叫時給的參數名不一致；④ 輸入端用了**非標準的 Hops Get 元件**（例如外掛自帶的替代品，見第 2.1 節）——這種情況 debug 模式的 `raw` 回應通常也看不出異常，因為 Rhino.Compute 本身沒有報錯，只是該輸入完全沒有被填入資料 |
| `POST /api/import`（或工具管理／測試）回傳 502，訊息含「請先啟動 Rhino.Compute」 | Rhino.Compute（compute.geometry，`localhost:5000`）未啟動或無回應。確認 Rhino 已開啟、compute.geometry 服務已啟動；也可以直接檢查 Web UI 頁首的 Rhino.Compute 狀態燈號 |
| 工具在「工具管理」看得到，但沒出現在 Claude Desktop / Cursor 的工具清單 | 檢查該工具狀態是否為 `status: "draft"`——只有 `registered` 的工具才會被 MCP `list_tools` 回傳（`draft` 會被雙重過濾：清單階段就濾掉，即使呼叫端仍握有工具名稱，呼叫時也會被拒絕）。到工具管理頁籤把狀態改成「已註冊」並儲存即可，不需要重啟 HOGER 或 Claude Desktop（工具庫每次都直接讀磁碟） |
| 中文（或其他非 ASCII）檔名的 `.gh` 匯入後，工具 id 變成一串奇怪的英數字 | 這是預期行為：工具 id 由檔名轉成 kebab-case（小寫字母/數字/連字號）產生，非拉丁字元會被移除；若移除後結果為空字串（例如檔名全是中文），會 fallback 成 `tool-` + 檔名的 SHA-1 前 8 碼（例如 `tool-3f2a9c1d`），確保 id 穩定且非空。`display_name` 仍會保留原始中文檔名，可在轉換／工具管理頁籤自行調整 `id`（限 `^[a-z0-9-]+$`） |
| Rhino.Compute 呼叫成功，但工具執行「卡住」很久 | 長時運算是預期行為——Rhino.Compute 呼叫逾時設定為 600 秒，測試頁籤的執行按鈕逾時是 620 秒。真正需要長時間運算的 GH 定義本來就會花較久時間；若持續逾時失敗，檢查 GH 定義本身是否有跑不完的迴圈或過大的網格 |
| Hops 元件在 Grasshopper 內行為異常（非空輸出問題） | 確認元件 Path 是否為 `http://localhost:8600/hops/<tool-id>` 且工具狀態是 `registered`；另見第 7 節「已知限制」——HOGER 的定義回應不含 `CacheKey`/`ResultType`，多數情況無影響，但若懷疑與此有關請回報 |
| debug 模式下 `raw` 顯示 `error_status_code` / `error_body` | 代表這次呼叫在 Rhino.Compute 端就失敗了（而不是 HOGER 本身的問題）——`error_body` 通常會截斷保留前 2000 字，是 Rhino.Compute 回傳的原始錯誤內容，可據此判斷是 GH 定義本身的錯誤還是參數問題 |

---

## 9. 檔案佈局

```
Hoger/
├── tools/                    # 工具定義，一工具一檔：{tool_id}.json
├── gh_files/                 # 透過「檔案上傳」匯入時，.gh 檔案存放於此
├── generated/
│   ├── results/              # 每次執行的結果 .3dm（檔名：{tool_id}_{時間戳}.3dm）
│   └── mcp_config/           # GET /api/mcp-config 產生的設定片段
│       ├── claude_desktop_config.snippet.json
│       └── http_client_config.snippet.json
├── webui/                    # 靜態 SPA（轉換／工具管理／測試 三頁籤）
├── hoger/                    # Python 套件本體（core / store / api / mcp_server / hops）
├── run_hoger.ps1             # 啟動腳本
└── docs/USAGE.md             # 本文件
```

`generated/results/` 與 `generated/mcp_config/*.snippet.json` 已列入 `.gitignore`（皆為執行期產生的檔案，不需版控）；`tools/*.json` 與 `gh_files/` 預設不排除，會隨你建立的工具而變動。
