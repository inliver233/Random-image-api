$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

if (-not (Test-Path "requirements-dev.txt")) {
  throw "requirements-dev.txt not found at repo root"
}

if (-not (Test-Path "backend")) {
  throw "backend/ not found"
}

$venvRoot = Join-Path $repoRoot ".venv"
$venvPath = Join-Path $venvRoot "backend_verify"

if (Test-Path $venvPath) {
  Remove-Item -Recurse -Force $venvPath
}

Write-Host "[verify_backend_install] create venv: $venvPath"
py -3.11 -m venv $venvPath

$python = Join-Path $venvPath "Scripts\\python.exe"

Write-Host "[verify_backend_install] pip install -U pip"
& $python -m pip install -U pip

Write-Host "[verify_backend_install] pip install -r requirements-dev.txt"
& $python -m pip install -r requirements-dev.txt

Write-Host "[verify_backend_install] pytest"
& $python -m pytest -q backend\\tests

Write-Host "[verify_backend_install] OK"

