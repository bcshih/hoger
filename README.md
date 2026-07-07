# HOGER

> **[🇺🇸 English README (英文版說明)](README_EN.md)** | **[🤖 AI 代理專用全自動安裝手冊 (AGENTS.md)](AGENTS.md)**

把任意 Grasshopper（`.gh`）檔案，透過 Rhino.Compute 自動轉換成 **Hops 端點** 與 **MCP 工具**，讓 AI（Antigravity、Claude Desktop、Cursor…）與 Grasshopper 都能無頭（headless）呼叫你的運算定義。

---

## 🤖 AI 代理一鍵全自動安裝與無痛升級 (AI-Ready Repository)

您可以直接把本 GitHub 專案網址貼給您的 AI 助理（Antigravity、Claude Desktop、Cursor、Cline、Windsurf）：
> *「請幫我安裝並綁定這套 Grasshopper AI MCP 工具庫：https://github.com/bcshih/hoger」*

您的 AI 助理將會自動閱讀 **[AGENTS.md](AGENTS.md)** 指南，並幫您在背景完成：
1. 自動下載專案並建立 100% 隔離的 Python 虛擬環境 (`.venv`)。
2. 自動檢驗電腦上的 **Rhino 7** 或 **Rhino 8** Grasshopper SDK 與 `Rhino.Compute` 連線狀態。
3. 自動在您的 Windows 桌面上建立 **「🧩 HOGER MCP 工具管理後台.lnk」** 捷徑。
4. 以「非破壞性安全合併」的方式將設定注入您的 AI 軟體（絕不覆蓋原有其他工具）！
5. **未來升級**：隨時執行 **`一鍵升級HOGER.bat`** 即可同步最新功能，您自行產生的 Grasshopper 工具 100% 被保留！

## 架構

```
                         拖放 / 指定路徑 .gh
                                │
                                ▼
                    ┌───────────────────────┐
                    │   HOGER Web UI (SPA)   │  http://localhost:8600
                    │  轉換 │ 工具管理 │ 測試 │
                    └───────────┬───────────┘
                                │ /api/*
                                ▼
                    ┌───────────────────────┐
                    │  HOGER 後端 (FastAPI)  │  tools/*.json 工具庫
                    └───────────┬───────────┘
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              /hops/{id}    /mcp (HTTP)   stdio
             (Grasshopper   (Cursor 等)  (Claude Desktop)
              Hops 元件)
                    │           │           │
                    └───────────┼───────────┘
                                ▼
                    Rhino.Compute（compute.geometry，localhost:5000）
                                │
                                ▼
                         GH 檔案 headless 運算
```

核心層（`hoger/core/`：compute client、type mapping、executor、results）被三種介面共用：FastAPI 後端（供 Web UI）、MCP Server（stdio + Streamable HTTP，動態註冊工具）、Hops 端點（Grasshopper 可直連）。工具定義存成 `tools/*.json`，無資料庫。

## 快速開始

1. **啟動 Rhino.Compute**（compute.geometry，監聽 `localhost:5000`）——通常是開啟 Rhino 並啟動 compute.geometry.exe，或既有的 Rhino.Compute 服務。
2. **啟動 HOGER**：雙擊 `start_hoger.bat`（會自動開伺服器視窗並開瀏覽器），或使用 **`start_hoger_with_gemini.bat`**（可一鍵綁定 Google Gemini API 金鑰與模型型號進行 AI 深度解讀）；也可手動執行：
   ```powershell
   .\run_hoger.ps1
   ```
3. **開啟瀏覽器** `http://localhost:8600`，在「轉換」頁籤選「自動轉換」（預設）並拖放你的 `.gh` 檔案——不需要事先在 GH 檔案裡放任何 Hops 元件或標記。掃描完成後勾選要暴露的輸入/輸出、填參數名，按「開始轉換」；HOGER 會自動在檔案加上 `RH_IN:`/`RH_OUT:` 群組標記（改檔前自動備份 `.bak`），解析出工具定義草稿後即可註冊。
4. **接上 AI**：於「工具管理」或 `GET /api/mcp-config` 取得設定片段，貼進 Claude Desktop / Cursor 的 MCP 設定即可呼叫工具。

完整操作說明（GH 檔案準備規則、Web UI 三區、MCP/Cursor 接入、Hops 元件用法、疑難排解）請見 **[docs/USAGE.md](docs/USAGE.md)**。

---

## ⚠️ 使用限制與開發者規範 (System Limitations & Preparation Rules)

在使用或編寫供 AI 調用的 Grasshopper 檔案時，請遵守以下 6 大核心限制與規範：

1. **輸出最尾端判定規則 (Tail-End Geometry Rule)**：
   - 系統掃描候選輸出時，**只認「毫無下游接線」的資料與幾何參數物件**！
   - 具有運算邏輯的元件（例如 Division、Ladybug 日照分析等）**永遠不會**被自動認作輸出。若要將運算結果暴露給 AI，**最未端必須明確接上一個「幾何/資料參數元件」**（例如拉一個 Geometry、Curve、Brep 或 Panel 放置於最尾端承接）。
2. **命名強制消毒與分離設計 (`^[A-Za-z0-9_]+$`)**：
   - 為杜絕 AI 大模型 (LLM) 產生 Function Calling 幻覺，並防範 HTTP/Hops 網路傳輸時的 URL 路由亂碼，所有 `param_name` 會被強制消毒為純英數與底線。
   - **最佳實務**：請將乾淨的名稱留給變數（如 `building_height_m`），而把豐富的**中文描述、物理意義與數值範圍限制**寫在 `description` 欄位中！AI 會閱讀 `description` 理解語境，並用乾淨的 `param_name` 精準執行。
3. **文字與數值的分離落地原則 (String vs. Numeric Outputs)**：
   - **字串 (`string`)**：一律透過 Rhino `AttributeUserText`（屬性使用者文字）附著在輸出的 `.3dm` 幾何物件上。若該次運算完全無幾何產生，系統會自動於原點建立 `Point(0,0,0)` 承載文字，確保結果絕不遺失。
   - **數值 (`number`/`integer`/`boolean`)**：僅出現在 MCP 回傳的純 JSON `outputs` 字典中，**刻意不寫入 `.3dm` 檔案**。
4. **型別支援邊界 (Type Support Boundaries)**：
   - 目前自動解析涵蓋 17 種主流 Grasshopper 型別：Brep、Point、Geometry、Curve、Surface、Data、Number、Integer、Vector、Rectangle、Mesh、Line、Plane、String、Circle、Box、Boolean。
   - 進階或特殊型別（如 Arc、Colour、Time、Complex、Matrix 等）尚未支援，須在 GH 內部轉為 String、Data 或 Geometry 傳遞。
5. **Rhino.Compute 預設單位陷阱 (Millimeters Trap)**：
   - Rhino.Compute 後台無頭運算預設環境為 **毫米 (Millimeters)**。若 GH 檔案是用 **公尺 (Meters)** 建模（例如物理環境分析），幾何尺度會被縮小 1000 倍，且**通常不會報錯**（只會靜默算出一堆 0 或空值）！請注意測試報告區的 `modelunits` 提示或於定義內自行單位轉換。
6. **雙版本相容與外掛元件限制 (Rhino 7/8 Compatibility & Hops Markers)**：
   - 系統全面支援 **Rhino 7 與 Rhino 8**。但若使用 Rhino 7，請勿於 `.gh` 中使用 Rhino 8 專屬元件（如 ShrinkWrap）。
   - 若不走自動群組，改用手動放 Hops 標記元件，**僅限使用官方原生的 Hops 元件**（如 `Get Number`、`Get Brep`）。嚴禁使用第三方外掛（如 Ladybug）自帶的外觀相似 Get 元件，否則無頭執行時會靜默收不到資料且不報錯！

---

## 測試

```powershell
.\.venv\Scripts\pytest              # 單元測試（預設跳過 integration）
.\.venv\Scripts\pytest -m integration   # 需要 Rhino.Compute 在線
```
