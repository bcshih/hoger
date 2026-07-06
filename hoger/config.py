import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPUTE_URL = os.environ.get("HOGER_COMPUTE_URL", "http://localhost:5000")
HOGER_PORT = int(os.environ.get("HOGER_PORT", "8600"))
TOOLS_DIR = Path(os.environ.get("HOGER_TOOLS_DIR", ROOT / "tools"))
RESULTS_DIR = Path(os.environ.get("HOGER_RESULTS_DIR", ROOT / "generated" / "results"))
GH_FILES_DIR = Path(os.environ.get("HOGER_GH_DIR", ROOT / "gh_files"))
GHIO_DLL = os.environ.get("HOGER_GHIO_DLL", r"C:\Program Files\Rhino 8\Plug-ins\Grasshopper\GH_IO.dll")
for _d in (TOOLS_DIR, RESULTS_DIR, GH_FILES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── AI 深度解讀（task v3-B） ────────────────────────────────────────
#
# LLM_PROVIDER 選項：gemini-cli（預設，走本機 `gemini` CLI，零設定）｜
# gemini-api｜anthropic｜openai｜ollama。LLM_MODEL 留空時各 provider 用
# 自己的預設值（見 hoger/core/llm.py 的 _DEFAULT_MODELS）。
LLM_PROVIDER = os.environ.get("HOGER_LLM_PROVIDER", "gemini-cli")
LLM_MODEL = os.environ.get("HOGER_LLM_MODEL") or None
GEMINI_API_KEY = os.environ.get("HOGER_GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("HOGER_ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("HOGER_OPENAI_API_KEY", "")
OLLAMA_URL = os.environ.get("HOGER_OLLAMA_URL", "http://localhost:11434")
LLM_TIMEOUT = int(os.environ.get("HOGER_LLM_TIMEOUT", "120"))
