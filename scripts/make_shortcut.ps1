# Create a desktop shortcut for Stock Advisor
# Must be ASCII only (CP949 parser safety).

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$pythonw    = Join-Path $projectDir "venv\Scripts\pythonw.exe"
$script     = Join-Path $projectDir "main_qt.py"
$icon       = Join-Path $projectDir "assets\icon.ico"
$desktop    = [Environment]::GetFolderPath("Desktop")
$shortcut   = Join-Path $desktop "Stock Advisor.lnk"

if (-not (Test-Path $pythonw)) {
    Write-Error "pythonw.exe not found at: $pythonw. Run setup.bat first."
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "main_qt.py not found: $script"
    exit 1
}
if (-not (Test-Path $icon)) {
    Write-Error "icon not found: $icon"
    exit 1
}

# Target pythonw.exe directly -> no console window ever appears.
$wsh = New-Object -ComObject WScript.Shell
$sc  = $wsh.CreateShortcut($shortcut)
$sc.TargetPath       = $pythonw
$sc.Arguments        = "`"$script`""
$sc.WorkingDirectory = $projectDir
$sc.IconLocation     = "$icon,0"
$sc.Description      = "Stock Advisor - paper trading dashboard"
$sc.WindowStyle      = 1
$sc.Save()

Write-Host "Shortcut created (silent launch): $shortcut"
