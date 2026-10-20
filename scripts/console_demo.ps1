# The review console's demonstration on Windows: synthetic data in memory, no database needed.
#   powershell -ExecutionPolicy Bypass -File scripts\console_demo.ps1            (port 8090)
#   powershell -ExecutionPolicy Bypass -File scripts\console_demo.ps1 -Port 8095
# Optional: set CONSOLE_INSPECTOR_URL (for example http://127.0.0.1:8082) to a running
# `python -m inspector` before starting, so the "inside the model" page shows the real model.
param([int]$Port = 8090)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = ".venv-console\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creating .venv-console (once)..."
    py -3.11 -m venv .venv-console
    & $python -m pip install --disable-pip-version-check -q -r console\requirements.txt
}
Write-Host "Open http://127.0.0.1:$Port  - accounts: supervisor / reviewer / admin, password: demo-password"
& $python -m console demo --port $Port
