# HOGER

> **[🇹🇼 繁體中文版說明 (Traditional Chinese README)](README.md)** | **[🤖 AI Agent Setup Guide (AGENTS.md)](AGENTS.md)**

Automatically convert any Grasshopper (`.gh`) parametric definition into **Hops endpoints** and **MCP (Model Context Protocol) tools** via Rhino.Compute. Enable AI Agents (Antigravity, Claude Desktop, Cursor, etc.) and Grasshopper to invoke your parametric geometry definitions headlessly.

---

## 🚀 AI-Ready Instant Setup (For AI Agents & Users)

Simply paste this GitHub URL into your AI Assistant (Antigravity, Claude Desktop, Cursor, Cline, Windsurf):
> *"Please install and set up this Grasshopper MCP tool repository: https://github.com/bcshih/hoger"*

Your AI Agent will read **[AGENTS.md](AGENTS.md)** and automatically:
1. Clone the repository and set up an isolated Python virtual environment (`.venv`).
2. Verify your **Rhino 7** or **Rhino 8** Grasshopper SDK & `Rhino.Compute` service.
3. Place a Windows Desktop shortcut named **"🧩 HOGER MCP 工具管理後台.lnk"** for managing tools.
4. Safely configure your AI IDE's `mcp_config.json` without overriding existing tools.

---

## 📐 Architecture

```
                       Drag & Drop / Specify .gh Path
                                │
                                ▼
                    ┌───────────────────────┐
                    │   HOGER Web UI (SPA)   │  http://localhost:8600
                    │ Convert │ Manage │ Test│
                    └───────────┬───────────┘
                                │ /api/*
                                ▼
                    ┌───────────────────────┐
                    │  HOGER Backend (API)   │  tools/*.json Tool Store
                    └───────────┬───────────┘
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              /hops/{id}    /mcp (HTTP)   stdio
             (Grasshopper   (Cursor etc.) (Claude Desktop)
              Hops Comp.)
                    │           │           │
                    └───────────┼───────────┘
                                ▼
                    Rhino.Compute (compute.geometry, localhost:5000)
                    [Compatible with Rhino 7 & Rhino 8]
                                │
                                ▼
                         Headless GH Computation
```

---

## ⚡ Quick Start

1. **Start Rhino.Compute**: Launch Rhino and start `compute.geometry.exe` listening on `http://localhost:5000` (supports both **Rhino 7** and **Rhino 8**).
2. **Launch HOGER**:
   - Option A (One-Click): Double-click **`start_hoger.bat`** or the desktop shortcut.
   - Option B (Smart Setup): Run PowerShell script `.\setup.ps1` to initialize environment.
3. **Open Browser**: Navigate to `http://localhost:8600`. In the "Convert" tab, drag and drop any `.gh` file. Select candidate inputs/outputs, name your parameters, and click **Convert**. HOGER automatically marks parameter groups (`RH_IN:` / `RH_OUT:`) with automatic `.bak` backups.
4. **Connect AI Client**: Use the "Tool Manager" tab or copy the snippet from `GET /api/mcp-config` to connect Claude Desktop, Cursor, or Antigravity.

---

## 🛡️ Industrial Safeguards & System Rules

- **Zero-Collision Updates**: Run **`一鍵升級HOGER.bat`** (or `git pull`) anytime to upgrade code. User-created tools in `tools/*.json` are 100% preserved.
- **Strict Parameter Naming**: Parameter names (`param_name`) are sanitized to `^[A-Za-z0-9_]+$` to prevent AI LLM hallucination and HTTP routing errors. Use the `description` field for rich documentation.
- **Tail-End Geometry Output Rule**: An output candidate must be an explicit data/geometry parameter component at the very end of your GH definition with **no downstream wired connections**.
- **String & Numeric Output Separation**: String outputs are bound directly to 3D models via Rhino `AttributeUserText`. Numeric outputs (`number`, `integer`, `boolean`) are returned cleanly in JSON output payloads.
- **Rhino 7 & Rhino 8 Compatible**: Full serialization compatibility across Rhino versions. Notice that Rhino.Compute defaults to **Millimeters**; verify model scale if modeling in Meters.

---

## 🧪 Testing

```powershell
.\.venv\Scripts\pytest                  # Unit tests
.\.venv\Scripts\pytest -m integration   # Integration tests (requires live Rhino.Compute)
```
