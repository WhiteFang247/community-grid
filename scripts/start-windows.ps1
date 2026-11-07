# Community Grid Windows laptop launcher
# Run from repository root: powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  py -3 -m venv .venv
}
$python = (Resolve-Path ".venv\Scripts\python.exe").Path
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
if (-not $env:GRID_TOKEN) {
  $env:GRID_TOKEN = (& $python -c "import secrets; print(secrets.token_urlsafe(32))").Trim()
}
$token = $env:GRID_TOKEN
$root = (Get-Location).Path
$coordinator = Start-Process -FilePath $python -ArgumentList @("-m","uvicorn","coordinator:app","--host","127.0.0.1","--port","8765") -WorkingDirectory $root -PassThru
$env:GRID_COORDINATOR = "http://127.0.0.1:8765"
$worker = Start-Process -FilePath $python -ArgumentList @("worker.py") -WorkingDirectory $root -PassThru
Write-Host ""
Write-Host "Community Grid running!" -ForegroundColor Green
Write-Host "Dashboard: http://127.0.0.1:8765/dashboard"
Write-Host "Coordinator PID: $($coordinator.Id) | Worker PID: $($worker.Id)"
Write-Host "Access token: $token"
Write-Host "Paste the token into the dashboard, then click Connect."
Write-Host "Press ENTER to stop both processes."
Start-Process "http://127.0.0.1:8765/dashboard"
[void][Console]::ReadLine()
Stop-Process -Id $worker.Id,$coordinator.Id -ErrorAction SilentlyContinue
