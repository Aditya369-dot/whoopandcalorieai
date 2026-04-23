$ErrorActionPreference = "Stop"

$projectDir = "C:\Users\adity\OneDrive\Desktop\Whopper app"
$pythonExe = Join-Path $projectDir ".venv\Scripts\python.exe"
$scriptPath = Join-Path $projectDir "auto_import_mynetdiary.py"

$appDataDir = Join-Path $env:LOCALAPPDATA "WhoopMealAI"
$logDir = Join-Path $appDataDir "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logPath = Join-Path $logDir "daily_import_$timestamp.log"

& $pythonExe $scriptPath --search-dir "$env:USERPROFILE\Downloads" *>&1 |
    Tee-Object -FilePath $logPath
