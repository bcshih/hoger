@echo off
REM HOGER One-Click Update Utility / 一鍵平滑升級工具
setlocal
cd /d "%~dp0"

echo =====================================================
echo  🔄 HOGER 一鍵升級工具 (Pull Latest Code ^& Update Deps)
echo =====================================================
echo.
echo [1/2] 正在從 GitHub 提取最新版本程式碼...
git pull
if errorlevel 1 (
    echo [ERROR] Git pull 失敗！請檢查您的網路連線或本地分支衝突。
    pause
    exit /b 1
)

echo.
echo [2/2] 正在執行相依套件與環境升級檢查...
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1

echo.
echo 🎉 恭喜！HOGER 已經無痛升級至最新版本！您的自訂 Grasshopper 工具已完全保留。
pause
endlocal
