$env:PYTHONIOENCODING = "utf-8"
# HOGER_PORT 同時驅動監聽埠與 /api/mcp-config 產生的 HTTP 設定片段（未設定時預設 8600）
$port = if ($env:HOGER_PORT) { $env:HOGER_PORT } else { 8600 }
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn hoger.api.app:app --host 127.0.0.1 --port $port
