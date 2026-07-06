# HOGER Automated Smart Setup & Desktop Shortcut Creator
# 智慧安裝/升級與 Windows 桌面後台捷徑建立腳本

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " 🚀 HOGER MCP Smart Setup & Update Utility" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

# 1. Virtual Environment Check & Setup
$VenvDir = Join-Path $ScriptDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$PipExe = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Host "[1/4] Creating Python virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
    if (-not (Test-Path $PythonExe)) {
        Write-Error "Failed to create virtual environment. Please ensure Python 3.10+ is installed and in PATH."
        exit 1
    }
} else {
    Write-Host "[1/4] Virtual environment detected. Updating dependencies..." -ForegroundColor Green
}

# 2. Install / Upgrade Dependencies
Write-Host "[2/4] Installing / Upgrading dependencies from requirements.txt..." -ForegroundColor Yellow
& $PythonExe -m pip install --upgrade pip --quiet
& $PipExe install -r requirements.txt --quiet
Write-Host "      Dependencies successfully updated!" -ForegroundColor Green

# 3. Check Rhino.Compute Status & DLL Compatibility
Write-Host "[3/4] Checking Rhino.Compute & GH_IO.dll environment..." -ForegroundColor Yellow
$Rhino8Dll = "C:\Program Files\Rhino 8\Plug-ins\Grasshopper\GH_IO.dll"
$Rhino7Dll = "C:\Program Files\Rhino 7\Plug-ins\Grasshopper\GH_IO.dll"

if (Test-Path $Rhino8Dll) {
    Write-Host "      Found Rhino 8 Grasshopper SDK at: $Rhino8Dll" -ForegroundColor Green
} elseif (Test-Path $Rhino7Dll) {
    Write-Host "      Found Rhino 7 Grasshopper SDK at: $Rhino7Dll" -ForegroundColor Green
    Write-Host "      (Tip: Set environment variable HOGER_GHIO_DLL='$Rhino7Dll' if not auto-detected)" -ForegroundColor Cyan
} else {
    Write-Host "      [WARN] Could not find default Rhino 7 or Rhino 8 GH_IO.dll path. Please ensure Rhino is installed." -ForegroundColor Yellow
}

# Check if localhost:5000 is listening
try {
    $client = New-Object System.Net.Sockets.TcpClient
    $client.Connect("127.0.0.1", 5000)
    $client.Close()
    Write-Host "      Rhino.Compute (compute.geometry.exe) is RUNNING on port 5000!" -ForegroundColor Green
} catch {
    Write-Host "      [NOTE] Rhino.Compute is currently offline on port 5000. Please start compute.geometry.exe before running geometric tests." -ForegroundColor Yellow
}

# 4. Create Windows Desktop Shortcut (.lnk)
Write-Host "[4/4] Creating Windows Desktop Shortcut..." -ForegroundColor Yellow
try {
    $DesktopPath = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $DesktopPath "🧩 HOGER MCP 工具管理後台.lnk"
    $TargetBat = Join-Path $ScriptDir "start_hoger.bat"
    
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetBat
    $Shortcut.WorkingDirectory = $ScriptDir
    $Shortcut.Description = "HOGER Grasshopper AI MCP Tool Management Web UI"
    $Shortcut.IconLocation = "$TargetBat, 0"
    $Shortcut.Save()
    Write-Host "      Successfully created shortcut on Desktop: 🧩 HOGER MCP 工具管理後台.lnk" -ForegroundColor Green
} catch {
    Write-Host "      [WARN] Could not create desktop shortcut automatically: $_" -ForegroundColor Yellow
}

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " 🎉 Setup Completed Successfully!" -ForegroundColor Green
Write-Host "    To start HOGER manually, double-click start_hoger.bat or use the Desktop shortcut." -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
