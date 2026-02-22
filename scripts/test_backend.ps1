$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

if (-not (Test-Path "backend")) {
  Write-Host "[test_backend] backend/ not found -> skip"
  exit 0
}

Write-Host "[test_backend] python compileall"
py -3.11 -m compileall -q backend

if (Test-Path "backend\\tests") {
  Write-Host "[test_backend] pytest"
  py -3.11 -m pytest -q backend\\tests
} else {
  Write-Host "[test_backend] backend/tests not found -> skip pytest"
}

Write-Host "[test_backend] OK"

