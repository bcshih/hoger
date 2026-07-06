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
