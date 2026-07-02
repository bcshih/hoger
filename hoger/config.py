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
