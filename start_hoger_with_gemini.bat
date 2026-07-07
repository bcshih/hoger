@echo off
REM =====================================================================
REM HOGER MCP - Gemini AI One-Click Launcher
REM Fill in your Gemini settings below (DO NOT add spaces around '=')
REM =====================================================================

setlocal
cd /d "%~dp0"

REM [Setting 1] AI Provider (keep as gemini-api)
set HOGER_LLM_PROVIDER=gemini-api

REM [Setting 2] Gemini Model ID (e.g. gemini-3.5-flash or gemini-2.5-flash)
set HOGER_LLM_MODEL=gemini-3.5-flash

REM [Setting 3] Your Google Gemini API Key (Paste after the '=' sign below)
set HOGER_GEMINI_API_KEY=PASTE_YOUR_AIZASY_KEY_HERE

REM =====================================================================
REM System Launch Commands below (Do not modify)
REM =====================================================================

if not defined HOGER_PORT set HOGER_PORT=8600
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Please run python -m venv .venv and install requirements first.
    pause
    exit /b 1
)

echo =====================================================================
echo Starting HOGER with Gemini AI ...
echo Provider: %HOGER_LLM_PROVIDER%
echo Model:    %HOGER_LLM_MODEL%
echo =====================================================================

start "HOGER Server (%HOGER_LLM_MODEL%)" cmd /k .venv\Scripts\python.exe -m uvicorn hoger.api.app:app --host 127.0.0.1 --port %HOGER_PORT%

echo Waiting for HOGER server to become ready ...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 40;$i++){ try{ $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',%HOGER_PORT%); $c.Close(); $ok=$true; break } catch { Start-Sleep -Milliseconds 500 } }; if(-not $ok){ exit 1 }"

if errorlevel 1 (
    echo [WARN] Server is slow to start or failed - check the "HOGER Server" window.
)

start "" "http://127.0.0.1:%HOGER_PORT%"

echo.
echo =====================================================================
echo HOGER is open in your browser!
echo Current AI Model: %HOGER_LLM_MODEL%
echo To stop the service, close the "HOGER Server" console window.
echo =====================================================================
timeout /t 5 >nul
endlocal
