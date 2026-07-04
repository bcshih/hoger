# HOGER

把任意 Grasshopper（`.gh`）檔案，透過 Rhino.Compute 自動轉換成 **Hops 端點** 與 **MCP 工具**，讓 AI（Claude Desktop、Cursor…）與 Grasshopper 都能無頭（headless）呼叫你的運算定義。

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
2. **啟動 HOGER**：雙擊 `start_hoger.bat`（會自動開伺服器視窗並開瀏覽器），或手動執行：
   ```powershell
   .\run_hoger.ps1
   ```
3. **開啟瀏覽器** `http://localhost:8600`，在「轉換」頁籤選「自動轉換」（預設）並拖放你的 `.gh` 檔案——不需要事先在 GH 檔案裡放任何 Hops 元件或標記。掃描完成後勾選要暴露的輸入/輸出、填參數名，按「開始轉換」；HOGER 會自動在檔案加上 `RH_IN:`/`RH_OUT:` 群組標記（改檔前自動備份 `.bak`），解析出工具定義草稿後即可註冊。
4. **接上 AI**：於「工具管理」或 `GET /api/mcp-config` 取得設定片段，貼進 Claude Desktop / Cursor 的 MCP 設定即可呼叫工具。

完整操作說明（GH 檔案準備規則、Web UI 三區、MCP/Cursor 接入、Hops 元件用法、疑難排解）請見 **[docs/USAGE.md](docs/USAGE.md)**。

## 測試

```powershell
.\.venv\Scripts\pytest              # 單元測試（預設跳過 integration）
.\.venv\Scripts\pytest -m integration   # 需要 Rhino.Compute 在線
```
