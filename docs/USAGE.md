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

**注意**：`run_hoger.ps1` 會讀取 `HOGER_PORT`（未設定時預設 8600）作為 uvicorn 監聽埠，與 `/api/mcp-config` 產生的 HTTP 設定片段自動同步——設定一個環境變數即可換埠。若改用自己的 uvicorn 指令啟動，記得讓 `--port` 與 `HOGER_PORT` 一致。

---

## 2. GH 檔案準備規則（給工具作者，最重要章節）

**v2 之後：你的 GH 檔案什麼都不用改。** 不需要放 Hops Get/Context Bake 元件，也不需要學任何 GH 標記語法——正常畫你的定義（滑桿、Toggle、Panel、Value List、一般幾何參數）即可，剩下的交給 HOGER 的「自動轉換」流程處理。本節先講新的主流程，最後保留一節給仍然想手動標記、或檔案已經有 Hops 標記的進階使用情境。

### 2.1 新主流程：自動轉換

1. 開啟 Web UI 的「轉換」頁籤，轉換方式選 **「自動轉換」**（預設，卡片上標「推薦」）。
2. 用「檔案上傳」拖放 `.gh` 檔案，或用「本機路徑」輸入 HOGER 伺服器可存取的絕對路徑。送出後呼叫 `POST /api/scan`——這一步**只讀取檔案，不會修改任何東西**。
3. 掃描完成後進入「候選輸入」/「候選輸出」勾選畫面：每一列是掃描到的一個候選參數，勾選你要暴露的、填一個參數名，按「開始轉換」。
4. HOGER 呼叫 `POST /api/convert`：先把你勾選的物件包成 `RH_IN:<名稱>` / `RH_OUT:<名稱>` 的 GH 群組（Group）寫回你的 `.gh` 檔案（**改檔前一定先自動備份成 `<檔名>.<時間戳>.bak`**，與原檔同目錄），再呼叫 Rhino.Compute 的 `/io` 端點解析出完整的工具定義草稿，直接進入「檢視／編輯草稿」畫面。

這個「群組」是 Grasshopper 原生的物件群組功能（跟你手動選取物件按 Ctrl+G 一樣），**不會**改變被圈進群組的物件本身的行為——滑桿照拉、Toggle 照切、Panel 照看、下游接線照跑，在 Grasshopper 裡打開檔案完全正常，只是畫布上多了一個彩色的分組框（HOGER 產生的輸入群組是橘色、輸出群組是藍色，純粹辨識用，不影響運算）。之後你想再次調整這個 GH 檔案（改滑桿範圍、接新的下游元件……）都不受影響；唯一要注意的是**不要手動更動群組的 NickName**（就是 `RH_IN:xxx`/`RH_OUT:xxx` 這串文字），因為 HOGER 靠這個字串認出注入點。

### 2.2 候選怎麼來的

掃描階段（`/api/scan`）會遞迴走訪整個 GH 定義，找出以下幾類物件當候選：

**候選輸入：**
- **Number Slider**（數字滑桿）——候選列會顯示目前值與 `[最小值 – 最大值]` 範圍。
- **Boolean Toggle**（開關）——顯示目前的 True/False 值。
- **Panel**（面板）——只有當這個 Panel **有接到下游元件**時才算候選輸入（沒有下游接線的 Panel 是輸出候選，見下方）。
- **Value List**（值列表）。
- **懸空的資料物件**：畫布上獨立放置、有接到下游元件、但自己沒有上游來源（Source）的一般參數物件——不限型別，曲線、曲面、實體（Brep）、點、幾何、數字、整數、向量等資料物件都算（例如一個接了線但沒有連任何東西進來的 Curve/Surface/Brep/Point 參數）。這類物件無法從檔案內容直接判斷型別名稱，候選列的「型別」會顯示該物件自己的元件名稱。

**候選輸出：**
- **任何沒有下游接線的資料物件**都算候選輸出，不限型別——Panel（面板）、Curve（曲線）、Surface（曲面）、Brep（實體）、Point（點）、Geometry（幾何）等等，只要是「沒人接線去讀它」的資料，就可能是你想要的輸出。「來自」欄會顯示是哪個元件的哪個輸出腳位餵給它的（`fed_by`）；沒有上游、單純懸在畫布上的資料物件則 `fed_by` 為空。
  - **例外**：Number Slider / Boolean Toggle / Value List 這三種「純輸入用」的控制元件，即使沒有下游接線也**不會**被當成候選輸出（一個沒接任何東西的滑桿只是沒用到的控制項，不是有意義的輸出）。
  - 元件（有自己的計算邏輯、輸入輸出腳位的物件，例如 Division、LB 系列元件）**永遠不會**出現在候選輸入或候選輸出清單中，只有「資料物件」（面板、滑桿、以及各種參數）才會被列為候選；元件自己的輸入腳位仍會透過遞迴掃描解析出接線關係（見下方「接到」/「來自」欄）。
  - 目前已涵蓋可辨識的型別：Brep（實體）、Point（點）、Geometry（幾何）、Curve（曲線）、Surface（曲面）、一般資料（Data）、Number（數字）、Integer（整數）、Vector（向量）、Rectangle（矩形）、**Mesh（網格）、Line（線條）、Plane（平面）、String（文字/字串）、Circle（圓）、Box（方塊）、Boolean（布林）**。其中 Brep/Point/Geometry/Curve/Surface/Data/Number/Integer/Vector/Rectangle 已對真實檔案直接驗證過；Mesh/Line/Plane/String/Circle/Box/Boolean 目前尚未遇到含這些型別的真實檔案，改採可信第三方來源交叉比對已驗證型別的 GUID 後採信加入。其他標準參數型別（例如 Arc、Colour、Time、Complex）尚未涵蓋——遇到這類檔案回報後可以再擴充。

掃描結果的「接到」/「來自」欄位就是幫助你判斷「這個候選到底是什麼」用的——同一個滑桿在複雜定義裡可能有好幾個，靠這欄分辨要選哪一個。已經被標記過的物件（無論是之前用自動轉換標記過，或手動加的 `RH_IN:`/`RH_OUT:` 群組）會在「既有標記」欄顯示目前的標記名稱，並且預設勾選、名稱欄預填舊名稱；重新標記同一個物件會直接改名既有群組，不會疊加出兩個群組。

### 2.3 建議名規則與名稱格式限制

每個候選列的參數名欄位會預填一個建議名，規則依序：

1. 候選若有接線，優先取接線端的腳位名稱（輸入候選取下游元件的輸入腳位名；輸出候選取來源元件的輸出腳位名）。
2. 否則取該物件自己的 Nickname。
3. 兩者都沒有可用文字時，fallback 為元件型別的英文小寫加底線（例如 `number_slider`），同型別有多個則加序號（`number_slider_2`、`number_slider_3`……）。
4. 建議名一律先消毒成只含英數字與底線（`^[A-Za-z0-9_]+$`，移除所有其他字元，含中文、空白、符號）；跟同一批已勾選候選的建議名衝突時自動加 `_2`/`_3`……後綴。

這只是**預填建議**，你可以直接改成任何你喜歡的名字，唯一硬性限制是格式必須符合 `^[A-Za-z0-9_]+$`（僅限英數字與底線，不能有空白、中文或符號）——畫面上格式不符或重複的名稱欄會標紅，「開始轉換」按鈕會停用直到修正。這個名稱就是之後 AI/Hops 呼叫工具時要填的 key（`param_name`）。

### 2.4 預設值行為：檔案狀態即預設值

**沒有勾選的參數，一律用你儲存 `.gh` 檔案當下、滑桿/Toggle 停在的那個值當作預設值**——不需要另外設定。這代表：

- 想改一個輸入的預設值，最簡單的方式就是回 Grasshopper 把對應滑桿拉到你要的位置、存檔，下次轉換（或重新解析已標記的檔案）就會用新值。
- 已標記並轉換過的參數，`/io` 回應裡的 `Default` 就是當時滑桿/Toggle 的值；「檢視／編輯草稿」畫面仍可以手動覆寫 `default`/`minimum`/`maximum`，兩者互不衝突——草稿編輯的是 HOGER 這邊工具定義的欄位，不會回寫到你的 GH 檔案。
- 沒有勾選、也就沒有標記的候選物件，不會出現在工具定義裡，自然也談不上預設值——它們就是 GH 定義內部的固定值或中間狀態，維持原樣運作。

### 2.5 自動描述：給 AI 調用端看的說明文字

轉換完成（或用「直接解析」匯入已標記檔案）後，HOGER 會盡力自動生成兩種說明文字，讓 AI（MCP client）在完全沒看過你的 GH 檔案的情況下，也能大致理解這個工具在做什麼、每個參數的作用：

- **工具描述**（`description` 欄位）：只在你沒有填寫描述時（原本是空字串）才會被自動生成的文字填入，包含轉換來源聲明、定義規模（元件數量、主要元件）、偵測到的已知 Grasshopper 生態系（Ladybug、Honeybee、Karamba3D、Galapagos、Wallacei、Kangaroo 等）、輸入輸出數量與型別統計。你自己填的描述**永遠不會被覆寫**。
- **完整自動文件**（`auto_doc` 欄位）：一份更完整的 Markdown 說明，內容包含工具說明、每個輸入參數的接線語境（餵給哪個元件的哪個輸入）與值域/目前值、每個輸出參數的來源語境（由哪個元件的哪個輸出餵入）、以及呼叫提示（幾何參數格式、未提供參數的預設值行為）。這個欄位**每次轉換都會重新生成**（不看是否已有內容），因為它是獨立的輔助說明，不涉及覆寫使用者輸入的疑慮。`auto_doc` 非空時，會被附加在 MCP 工具的 `description` 後面（見第 6 節）——這是 AI 實際看到、用來判斷怎麼呼叫這個工具的完整內容。
- 個別輸入/輸出參數的 `description` 欄位比照工具描述的規則：只在原本是空字串時才填入自動生成的說明（例如「餵給 LB Sensor Grid 的輸入。目前值 1.0，範圍 0.1–50。」），已有內容的一律保留。

自動描述是**盡力而為**（best-effort）：底層靠重新掃描已標記的 `.gh` 檔案取得元件清單與接線資訊，若掃描失敗（例如 GH_IO 不可用、檔案格式問題），轉換或匯入本身仍會正常成功，只是這兩個欄位維持空字串，不影響核心功能。Web UI 的「檢視／編輯草稿」（轉換頁籤）與工具管理頁面的編輯面板，都可以透過「查看自動生成的完整說明」展開讀取 `auto_doc` 全文（唯讀，僅供參考；要修改請編輯 `description` 欄位本身）。

### 2.6 AI 深度解讀（選用）

2.5 節的自動描述是**規則式**產生的：純粹從掃描到的接線與值域組句子，不理解 GH 定義實際在算什麼。如果想要更貼近語意的說明（例如「這個定義計算街道峽谷內的日照舒適度，`_grid_size` 控制取樣網格密度……」），可以在掃描勾選階段勾選 **「AI 深度解讀」**：轉換時會把這個定義的結構事實（元件清單、每個參數的接線與值域、物件總數——即 `hoger.core.describe.build_graph_digest()` 產生的 digest）送給 LLM，取得工具用途與逐參數描述後**覆蓋**規則式的文字。

**這是選用功能，預設關閉。** 不勾選時，行為與 2.5 節完全相同——HOGER 本身不是 AI Agent，預設不會呼叫任何外部 LLM。

**Provider 設定**（環境變數，`hoger/config.py` 讀取，皆可覆寫）：

| 環境變數 | 預設值 | 說明 |
|---|---|---|
| `HOGER_LLM_PROVIDER` | `gemini-cli` | `gemini-cli`｜`gemini-api`｜`anthropic`｜`openai`｜`ollama` |
| `HOGER_LLM_MODEL` | （各 provider 預設） | 覆寫該 provider 使用的 model id |
| `HOGER_GEMINI_API_KEY` | 空 | `gemini-api` provider 用；[Google AI Studio](https://aistudio.google.com/apikey) 取得 |
| `HOGER_ANTHROPIC_API_KEY` | 空 | `anthropic` provider 用；[Anthropic Console](https://console.anthropic.com/settings/keys) 取得 |
| `HOGER_OPENAI_API_KEY` | 空 | `openai` provider 用；[OpenAI Platform](https://platform.openai.com/api-keys) 取得 |
| `HOGER_OLLAMA_URL` | `http://localhost:11434` | `ollama` provider 用，本機或區網 Ollama 服務位址 |
| `HOGER_LLM_TIMEOUT` | `120`（秒） | 單次 LLM 呼叫逾時 |

各 provider 預設 model：`gemini-api` → `gemini-2.5-flash`；`anthropic` → `claude-haiku-4-5`；`openai` → `gpt-4o-mini`；`ollama` 沒有預設值，必須自行以 `HOGER_LLM_MODEL` 指定（例如 `llama3`）。

**預設值是 `gemini-cli`** 而不是某個 API：如果你的電腦已經裝了 [Gemini CLI](https://github.com/google-gemini/gemini-cli)（`npm install -g @google/gemini-cli` 之類，免費版每天 1000 次額度），HOGER 會直接透過 `gemini -y -p "<prompt>"` 呼叫它，不需要另外申請任何 API key。掃描頁面勾選框旁會顯示目前偵測到的 provider/model；若未偵測到 `gemini` 指令，勾選框會停用並顯示原因（例如「未偵測到 gemini CLI，或設定 HOGER_GEMINI_API_KEY」）。要打包給別人使用、或不想依賴本機 CLI，改設定 `HOGER_LLM_PROVIDER=gemini-api`（或 `anthropic`/`openai`）並提供對應的 API key 即可。

**大檔案 token 消耗提醒**：掃描到的物件數超過 300 時，勾選「AI 深度解讀」並按下「開始轉換」會先跳出確認視窗（「此定義較大（N 個物件），AI 解讀可能消耗大量 token。仍要啟用嗎？」）。選擇取消只會取消這次的 AI 解讀勾選，**不會**擋下轉換本身——轉換照常進行，只是改用規則式描述。

**與規則式描述的關係（fallback）**：AI 深度解讀**永遠是規則式描述之上的疊加層，不是取代**。以下任一情況發生時，會無聲降級回 2.5 節的規則式結果，轉換本身不會失敗：

- 未勾選「AI 深度解讀」：完全不會呼叫 LLM，行為等同 2.5 節。
- 勾選了但目前設定的 provider 不可用（沒裝 CLI、沒設 key）：轉換照常用規則式描述完成，回應多帶一個 `ai_describe_error` 欄位說明原因，Web UI 會跳出警告 toast「AI 解讀失敗，已使用規則式描述：<原因>」。
- Provider 可用但呼叫失敗（逾時、網路錯誤、回應無法解析成合法 JSON）：同上，規則式描述保持不變，同樣帶 `ai_describe_error`。
- 呼叫成功：`description`（工具層級）與能對應到參數名稱的逐參數 `description` 會被 AI 生成的文字覆蓋；`auto_doc` 前面會插入一個「## AI 解讀」章節（工具用途 + 補充說明），後面規則式的結構事實章節（輸入參數表、輸出清單、呼叫提示）原樣保留——AI 解讀不會讓你失去規則式版本記錄的精確接線/值域事實。

### 2.7 進階：手動標記（仍相容）

如果你不想用掃描勾選介面，或者檔案已經用 Hops 元件（`Get Number`/`Get Boolean`/`Get String`/`Get Brep`……＋ Context Bake/Print）手動標記過，轉換方式改選 **「直接解析」**：這條路徑直接呼叫 `POST /api/import`，跳過掃描與標記步驟，用 Rhino.Compute 的 `/io` 端點解析檔案目前的狀態。

手動標記兩種等價做法：

- **v2 群組標記（跟自動轉換寫出來的東西完全一樣）**：在 Grasshopper 裡選取要當輸入的物件（滑桿、Toggle、Panel、Value List、懸空參數……），按 `Ctrl+G` 群組起來，把群組的 NickName 改成 `RH_IN:<名稱>`；輸出同理，群組 NickName 改成 `RH_OUT:<名稱>`。存檔後用「直接解析」匯入即可，效果與自動轉換完全相同（`/io` 對兩者一視同仁）。
- **v1 Hops 元件標記（舊版相容）**：在 **Params → Util**（Hops 圖示）分類下放置對應型別的 `Get` 元件（`Get Number`、`Get Boolean`、`Get String`、`Get Brep`……），NickName 改成參數名（HOGER 會原樣保留底線，例如 `_geometry`、`_grid_size`、`context_`）；輸出則接一個 **Context Bake**（或 Context Print）元件，NickName 就是輸出參數名。**插件提供的替代 Get 元件不會被辨識**——例如 Ladybug 的「Get EPW File Path」「Get Boolean」等，即使外觀類似，也不是 Rhino.Compute 掃描時認得的 `IGH_ContextualParameter` 實作，混用會導致該輸入完全收不到資料，而且**不會報錯**，下游元件只是靜靜地拿到空值。只有 Hops 外掛自帶的 Get 元件可以。

兩種手動標記方式都可以跟自動轉換的群組混用在同一個檔案裡（`/io` 是逐一收集所有符合規則的標記，不管來源）。

### 2.8 文字輸出：一律經幾何 + AttributeUserText

HOGER 的設計是「執行結果一律落地成 `.3dm`」，**不會把字串當作獨立資料直接回傳**（雖然 JSON 回應裡也看得到字串值，但正式的資料通道是 `.3dm`）：

- 字串（`kind == string`）輸出會用 `rhino3dm.ObjectAttributes.SetUserString(param_name, value)` 附著在結果 `.3dm` 檔的幾何物件上（即 AttributeUserText 機制）。
- 若這次執行**沒有任何幾何輸出**，HOGER 會建立一個原點 `Point(0,0,0)` 物件，把所有字串 UserText 都附著在這個點上，確保文字結果一定讀得到、不會遺失。
- 所以：只要你的 GH 檔案有字串輸出（不論是自動轉換勾選的候選、還是手動標記），其餘由 HOGER 自動處理，不需要額外設計。
- 提醒：`number` / `integer` / `boolean` 型別的輸出**不會**寫進 `.3dm`，只會出現在執行結果的 JSON `outputs` 裡——這是刻意設計（數值用 JSON 回傳就足夠，`.3dm` 只承載幾何與依附其上的文字）。

### 2.9 單位陷阱：compute.geometry 預設 Millimeters

- Rhino.Compute（compute.geometry）執行 GH 定義時預設模型單位是 **Millimeters**。如果你的 GH 檔案是用 **Meters** 建模，幾何座標會被誤讀成毫米，等於整個模型「縮小」1000 倍。
- 這種錯誤**通常不會報錯**——尤其像 Ladybug 這類分析元件，遇到尺度錯誤的幾何常常是靜默回傳空值或 0，不會丟例外。
- HOGER 的因應方式：執行結果會帶回 Rhino.Compute 回應中的 `modelunits` 欄位（`ToolResult.modelunits`），並且**只要不是 `"Meters"`**，Web UI 的測試頁籤會在結果區顯示醒目的橘色提示：「模型單位注意：模型單位為 XXX，請確認幾何尺度」。
- 若你的模型是 Meters，務必在送入 Rhino.Compute 前確認幾何尺度是否需要換算，或在 GH 檔案內部自行處理單位轉換。

---

## 3. Web UI 三區操作

瀏覽器開啟 `http://localhost:8600` 後有三個頁籤：**轉換 → 工具管理 → 測試**。

### 3.1 轉換（Import & Convert）

進入頁面先選轉換方式（兩張卡片，可隨時切換）：**自動轉換**（預設，標「推薦」——見第 2 節，任意 `.gh` 皆可）或**直接解析**（標「進階」——檔案已含 `RH_IN:`/`RH_OUT:` 群組或 Hops 標記時使用）。兩種方式都支援「檔案上傳」（拖放或按「瀏覽檔案」；檔案存到 `gh_files/` 目錄）與「本機路徑」（輸入 HOGER 伺服器可存取的絕對路徑，不複製檔案）。

**自動轉換模式（四階段）**：

1. **匯入**：送出後呼叫 `POST /api/scan`（只讀，不動檔案）。若回傳 501（GH_IO.dll 不可用）會顯示說明卡，提示安裝 Rhino 8 或設定 `HOGER_GHIO_DLL`，並建議若檔案已有標記可改用「直接解析」。
2. **掃描勾選**：顯示候選輸入／候選輸出表格（型別、Nickname、目前值/範圍、接線資訊、既有標記），逐列勾選並填參數名（見 2.2–2.3 節）；未勾選的列預設不選中，已有標記的物件預設勾選並預填舊名稱。按「開始轉換」送出 `POST /api/convert`。
   - 若此時 Rhino.Compute 離線：後端已經完成標記與備份（`.bak` 已寫入），只是呼叫 `/io` 解析失敗，回傳 502。畫面會切到專用的「檔案已標記並備份，Rhino.Compute 目前離線」提示卡，顯示備份路徑；等 Compute 上線後按「重新解析」（內部改呼叫 `POST /api/import`，帶著同一個 `gh_path`）即可完成解析，**不需要重新掃描或重新標記**。
3. **檢視／編輯草稿**：解析成功後顯示草稿工具定義，可編輯 `id`（限小寫字母/數字/連字號，格式 `^[a-z0-9-]+$`）、`display_name`、`description`，以及每個輸入/輸出的 description、default、minimum、maximum 等欄位。
4. **註冊**：按「註冊到 MCP」送出 `POST /api/tools`，可選擇存成 `draft`（草稿，暫不對外開放）或 `registered`（已註冊——會立即出現在 MCP 工具清單與 `/hops/{id}`，不需要重啟服務，因為工具庫每次都直接讀磁碟）。

**直接解析模式（三階段，v1 既有流程）**：送出後直接呼叫 `POST /api/import`（打 Rhino.Compute 的 `/io` 端點解析檔案目前狀態），成功後同樣進入「檢視／編輯草稿」→「註冊」。Rhino.Compute 離線時顯示「請先啟動 compute.geometry（localhost:5000）」提示。

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

MCP 工具清單（`tools/list`）裡每個工具的 `description` 欄位，組成方式是 `顯示名稱 — description`，若該工具的 `auto_doc`（見 2.5 節「自動描述」）非空，會再附加完整的自動生成說明於後——這是 AI 實際看到、用來理解這個工具在做什麼、每個參數如何影響輸出的主要依據，總長度上限 4000 字元（超過從尾端截斷）。手動編輯 `description` 欄位不會影響 `auto_doc` 的內容，兩者是分開儲存的欄位。

不論透過 stdio 或 HTTP，MCP 的 `tools/call` 參數（`arguments`）都是依 manifest 的 `inputs` 動態產生 JSON Schema，鍵名就是 GH 檔案裡的參數名（例如 `_geometry`、`_grid_size`）。各型別的填法：

- **數值**（`number`/`integer`）、**布林**（`boolean`）、**字串**（`string`）：直接給對應的 JSON 值，例如 `{"_grid_size": 2, "_run": true}`。
- **幾何**（`kind == "geometry"`）：是一個物件，二擇一提供：
  - `{"file_3dm": "C:\\path\\model.3dm", "layer": "選填圖層名"}` — 從指定 `.3dm` 檔案讀取幾何（`layer` 可省略；省略時取檔案內所有物件）。若指定的 `layer` 在檔案中不存在，或 required 參數讀不到任何物件，會回傳參數錯誤。
  - `{"encoded": ["<rhino3dm JSON 編碼字串>", "..."]}` — 直接提供已用 rhino3dm 編碼（`.Encode()` 後 `json.dumps()`）的幾何物件字串陣列，供既有工具鏈（例如 C# 外掛）串接使用。
  - 兩個 key 同時出現時以 `encoded` 優先；但 `"encoded": null`（JSON 客戶端常對未用欄位送 null）視同未提供，會改走 `file_3dm`；`"encoded": []`（空陣列）則視為「明確給了空幾何」——required 參數會回參數錯誤，選填參數會被略過。

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
| 輸出全部是空值，但沒有任何錯誤訊息 | 最常見的原因（依可能性排序）：① 輸入格式不對（幾何要用 `file_3dm`/`encoded`，且需先確認能載入非空物件）；② **模型單位不一致**——GH 檔案是 Meters、Rhino.Compute 卻用 Millimeters 解讀，幾何被縮放 1000 倍後分析元件靜默回空值（見第 2.8 節，可從測試頁籤的 `modelunits` 提示確認）；③ 自動轉換勾選的候選名稱跟呼叫時給的參數名不一致（或手動標記時 Hops Get 元件的 NickName 對不上）；④ 手動標記時用了**非標準的 Hops Get 元件**（例如外掛自帶的替代品，見第 2.6 節）——這種情況 debug 模式的 `raw` 回應通常也看不出異常，因為 Rhino.Compute 本身沒有報錯，只是該輸入完全沒有被填入資料 |
| 轉換頁籤選「自動轉換」時，匯入階段顯示「掃描功能目前不可用」（501） | 代表 `POST /api/scan`／`POST /api/convert` 依賴的 `GH_IO.dll` 不可用（未安裝 Rhino 8/Grasshopper，或 `HOGER_GHIO_DLL` 環境變數未指到正確路徑）。若你的檔案已經含 `RH_IN:`/`RH_OUT:` 群組或 Hops 標記，可以切到「直接解析」模式繞過掃描，直接呼叫 `/io` 解析；否則需要修好 GH_IO 的可用性才能用自動轉換 |
| 掃描勾選階段按「開始轉換」後顯示「檔案已標記並備份，Rhino.Compute 目前離線」（502） | 標記與備份都已經完成（`.bak` 已寫在原檔同目錄，畫面上有顯示路徑），只是 `POST /api/convert` 呼叫 Rhino.Compute `/io` 解析那一步失敗。啟動 compute.geometry 後直接按畫面上的「重新解析」按鈕（等同對同一個已標記的 `gh_path` 呼叫 `/api/import`）即可完成，**不需要重新掃描或重新勾選標記**——重複執行掃描/轉換也不會疊加出兩個標記群組（見下一列） |
| 標記後想重新掃描同一個檔案，會不會標記兩次、產生重複的群組？ | 不會。`apply_marks()`（`hoger/ghio/marker.py`）有 idempotency 保護：若目標物件已經屬於「恰好一個」HOGER 風格的標記群組（NickName 符合 `^RH_(IN\|OUT):`），會直接把該群組的 NickName 改成新名稱，而不是新增一個群組。但若同一個物件已經**同時屬於兩個以上**標記群組（例如手動在 Grasshopper 裡弄出重疊的群組），`apply_marks()` 會直接拒絕整個標記請求並回傳 400（訊息會指出是哪個物件、屬於幾個群組），因為這種情況下該重新命名哪一個群組是無法自動判斷的歧義狀態——需要先在 Grasshopper 裡手動清理重疊的群組 |
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
