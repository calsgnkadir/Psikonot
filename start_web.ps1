# PsikoNot — start the demo on Windows (PowerShell).
# Same as the README quick start: demo mode, loopback only, port 8000.
Write-Host "Starting PsikoNot (demo mode)..." -ForegroundColor Cyan

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Installing requirements..." -ForegroundColor Yellow
python -m pip install -r requirements.txt --quiet

Write-Host ""
Write-Host "  App: http://127.0.0.1:8000" -ForegroundColor White
Write-Host "  API: http://127.0.0.1:8000/api/v1/docs" -ForegroundColor White
Write-Host ""
Write-Host "Demo accounts:" -ForegroundColor Yellow
Write-Host "  Practitioner : psk.elif / Practitioner@2026!"
Write-Host "  Secretary    : secretary.ayse / Secretary@2026!"
Write-Host "  Admin        : admin / Admin@2026Secure!"
Write-Host "  KVKK officer : sec.officer / SecOfficer@2026!   (co-signs dual-control requests)"
Write-Host ""

$env:ENVIRONMENT = "development"
$env:VHV_DEMO_MODE = "true"
# Loopback only: the demo accounts' passwords are public, so the demo must not
# be reachable from the local network.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
