$env:PYTHONIOENCODING = "utf-8"
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn hoger.api.app:app --host 127.0.0.1 --port 8600
