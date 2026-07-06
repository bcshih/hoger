# HOGER AI Agent Protocol / AI 代理專用安裝與升級協議

> **[English & 繁體中文對照版 / Bilingual Edition]**
> This document is written specifically for AI Agents (Antigravity, Claude Desktop, Cursor, Roo Code / Cline, Windsurf, etc.). When a user pastes the GitHub URL of this repository (`https://github.com/bcshih/hoger`) into an AI chat prompt asking to install, configure, or update HOGER, the AI Agent MUST strictly follow the instructions in this document.
> 本文件專為 AI 代理（Antigravity、Claude Desktop、Cursor、Roo Code / Cline、Windsurf 等）撰寫。當使用者將本 GitHub 倉庫網址（`https://github.com/bcshih/hoger`）貼入對話要求安裝、綁定 MCP 或進行升級時，AI 代理必須嚴格遵守本文檔的標準程序執行。

---

## 🌐 Part 1: Automated Installation Protocol (全自動安裝 SOP)

When the user asks you to install or connect HOGER:
當使用者要求您安裝或對接 HOGER 時，請依序執行以下 4 個步驟：

### Step 1: Clone or Locate the Repository (取得專案來源)
- **EN**: If the repository is not yet on the user's local filesystem, clone it to a standard directory (e.g., `%USERPROFILE%\Desktop\Hoger` or `C:\Hoger` on Windows).
- **CH**: 若專案尚未存在於用戶本機電腦中，請將其 `git clone` 至標準目錄（如 Windows 上的 `%USERPROFILE%\Desktop\Hoger` 或 `C:\Hoger`）。

### Step 2: Run Automated Setup Script (執行環境與桌面捷徑建置腳本)
- **EN**: Execute the PowerShell setup script to initialize the isolated virtual environment (`.venv`), install dependencies, verify Rhino.Compute availability, and create the Windows Desktop shortcut.
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
  ```
- **CH**: 執行 PowerShell 智慧安裝腳本，自動建立隔離虛擬環境 (`.venv`)、升級相依套件、檢查 Rhino.Compute 連線狀態，並於 Windows 桌面建立管理後台捷徑。

### Step 3: Identify & Safely Configure MCP Client (跨平台 MCP 設定自動注入)
- **EN**: Identify which AI client/IDE you are currently running in. Locate your corresponding configuration file from the mapping table below.
- **CH**: 請判斷您目前所在的宿主 AI 軟體/IDE，並從下方對照表找到對應的 MCP 設定檔路徑：

| AI Client / Platform | Windows Default Config Path | Automated Action / 代理執行策略 |
| :--- | :--- | :--- |
| **Antigravity** | `%USERPROFILE%\.gemini\config\mcp_config.json` | Direct safe merge write / 後台非破壞性安全寫入 |
| **Claude Desktop** | `%APPDATA%\Claude\claude_desktop_config.json` | Direct safe merge write / 後台非破壞性安全寫入 |
| **Cursor** | `%APPDATA%\Cursor\User\globalStorage\rooveteran.cursor-tutor\mcp.json` (or `.cursor/mcp.json`) | Check & merge write / 檢查存在性後非破壞性寫入 |
| **Roo Code / Cline** | `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` | Direct safe merge write / 後台非破壞性安全寫入 |
| **Windsurf** | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` | Direct safe merge write / 後台非破壞性安全寫入 |
| **GUI-Only Clients** | *Closed GUI / No direct file access* | Display standard Snippet in chat for user to copy-paste / 於對話印出 Snippet 供複製 |

#### 🔒 Safe JSON Merge Rules (非破壞性 JSON 寫入鐵律):
1. **Always Backup (必先備份)**: Create a timestamped backup file (e.g., `mcp_config.json.bak_20260706`) before modifying any file.
2. **Never Overwrite (絕不覆蓋)**: Parse the JSON dictionary. Only add or update the `"hoger"` block inside `"mcpServers"`. **DO NOT delete or overwrite existing servers** (e.g., Google Drive, GitHub, Slack).
3. **Universal MCP Configuration Snippet (通用注入配置單元)**:
   ```json
   {
     "command": "<ABSOLUTE_PATH_TO_REPO>/.venv/Scripts/python.exe",
     "args": ["-m", "hoger.mcp_server.stdio_main"],
     "cwd": "<ABSOLUTE_PATH_TO_REPO>",
     "env": {
       "HOGER_COMPUTE_URL": "http://localhost:5000"
     }
   }
   ```

### Step 4: Verify & Notify User (驗證與用戶通知)
- **EN**: Inform the user that HOGER is connected. Remind them that a desktop shortcut named **"🧩 HOGER MCP 工具管理後台.lnk"** has been created for managing tools via Web UI (`http://localhost:8600`).
- **CH**: 向用戶回報安裝與綁定成功！提醒用戶桌面上已建立 **「🧩 HOGER MCP 工具管理後台.lnk」** 捷徑，可隨時雙擊開啟 Web UI (`http://localhost:8600`) 管理與新增 Grasshopper 工具。

---

## 🔄 Part 2: Smooth Zero-Collision Update Protocol (無痛平滑升級 SOP)

When the user asks to update HOGER to the latest version (e.g., "Update HOGER", "幫我更新 HOGER"):
當使用者要求更新 HOGER 時，請依序執行以下指令：

```powershell
git pull
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

### 🛡️ Update Safeguards (升級安全承諾):
1. **User Tools Protected (用戶工具零衝突)**: User-created `.gh` files and `.json` tools inside `tools/` are preserved. Upgrading via `git pull` will NEVER delete or override user-created parametric definitions.
2. **Instant Tool Refresh (零快取即時生效)**: The backend (`hoger.mcp_server.server`) scans disk in real time. Newly registered or updated tools are immediately available to the AI without restarting the client window.

---

## 🛠️ Part 3: Environment Prerequisites & System Constraints (環境相依與系統限制)

AI Agents must be aware of these technical boundaries when guiding users or debugging tool calls:
AI 代理在調用工具或為用戶排錯時，必須熟知以下系統邊界：

1. **Rhino 7 & Rhino 8 Compatibility (雙版本完美相容)**:
   - HOGER supports both **Rhino 7** and **Rhino 8**.
   - Requires `compute.geometry.exe` running on `http://localhost:5000` (Rhino.Compute service).
   - If using Rhino 7, ensure `.gh` definitions do not use Rhino 8 exclusive components (e.g., ShrinkWrap, Model Object).
2. **Strict Naming Regex (`^[A-Za-z0-9_]+$`) (參數命名強制消毒)**:
   - All tool parameter names (`param_name`) are sanitized to English letters, numbers, and underscores only. This eliminates LLM hallucination and HTTP/URL routing crashes.
   - Use the `description` field for rich Chinese explanations, units, and guidelines.
3. **Tail-End Geometry Output Rule (最尾端輸出判定規則)**:
   - Outputs must be data/geometry parameter components with **no downstream wired connections**.
   - Components with calculation logic (like Division or Ladybug analysis components) cannot act as outputs directly; a dedicated Geometry/Data parameter component must be wired at the very tail end.
4. **String & Numeric Output Separation (文字與數值不對稱落地)**:
   - `string` outputs are attached directly onto 3D geometry via Rhino `AttributeUserText`. If no geometry exists, an origin point `Point(0,0,0)` is automatically created to carry the text.
   - `number` / `integer` / `boolean` outputs are returned cleanly in the JSON `outputs` payload and are NOT written into the `.3dm` model file.
5. **Rhino.Compute Millimeters Trap (模型尺度警示)**:
   - Rhino.Compute defaults to Millimeters. If `.gh` definitions are modeled in Meters, geometry coordinates may scale down by 1000x silently. Always verify `modelunits` in test results.
