@echo off
REM HOGER Safe Uninstall & Cleanup / 安全移除與復原工具
setlocal
cd /d "%~dp0"

echo =====================================================
echo  🗑️ HOGER 安全移除與環境復原工具 (Safe Uninstall)
echo =====================================================
echo.
echo 此操作將會：
echo 1. 刪除桌面上的「🧩 HOGER MCP 工具管理後台.lnk」捷徑
echo 2. 移除 Python 虛擬環境 (.venv)
echo 3. 提示您如何從 AI 軟體中安全移除 MCP 設定
echo.
set /p confirm="確定要執行解除安裝嗎？(Y/N): "
if /i not "%confirm%"=="Y" (
    echo 取消操作。
    pause
    exit /b 0
)

echo.
echo [1/3] 正在移除桌面捷徑...
if exist "%USERPROFILE%\Desktop\🧩 HOGER MCP 工具管理後台.lnk" (
    del "%USERPROFILE%\Desktop\🧩 HOGER MCP 工具管理後台.lnk"
    echo        已刪除桌面捷徑。
) else (
    echo        桌面捷徑不存在，略過。
)

echo.
echo [2/3] 正在清理本地虛擬環境 (.venv)...
if exist ".venv" (
    rmdir /s /q .venv
    echo        已完成 .venv 虛擬環境卸載。
) else (
    echo        .venv 不存在，略過。
)

echo.
echo [3/3] 💡 MCP 客戶端設定復原指引：
echo 請至您的 AI 軟體設定檔（例如 Antigravity 於 .gemini\config\mcp_config.json
echo 或 Claude Desktop 於 AppData\Roaming\Claude\claude_desktop_config.json），
echo 將 mcpServers 底下的 "hoger" 區塊乾淨刪除即可！
echo (若系統中有自動產生的 .bak 備份檔，您也可直接改名覆蓋復原)
echo.
echo =====================================================
echo  ✅ HOGER 本地環境已安全且乾淨地移除！感謝您的使用。
echo =====================================================
pause
endlocal
