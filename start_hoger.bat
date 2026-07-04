@echo off
REM HOGER one-click launcher: start backend window, wait until ready, open browser.
REM Note: Rhino.Compute (compute.geometry, localhost:5000) is NOT started by this
REM script - start Rhino + compute.geometry yourself. HOGER still runs without it;
REM the Web UI will just show a reminder on import/run.

setlocal
cd /d "%~dp0"

if not defined HOGER_PORT set HOGER_PORT=8600
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Create it first:  python -m venv .venv
    echo Then install deps: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting HOGER backend at http://127.0.0.1:%HOGER_PORT% ...
start "HOGER Server" cmd /k .venv\Scripts\python.exe -m uvicorn hoger.api.app:app --host 127.0.0.1 --port %HOGER_PORT%

echo Waiting for server to become ready ...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 40;$i++){ try{ $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',%HOGER_PORT%); $c.Close(); $ok=$true; break } catch { Start-Sleep -Milliseconds 500 } }; if(-not $ok){ exit 1 }"

if errorlevel 1 (
    echo [WARN] Server is slow to start or failed - check the "HOGER Server" window.
)

start "" "http://127.0.0.1:%HOGER_PORT%"

echo.
echo HOGER is open in your browser.
echo To stop the service, close the "HOGER Server" console window.
timeout /t 5 >nul
endlocal
