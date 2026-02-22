$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

Write-Host "== test_all: backend =="
& (Join-Path $PSScriptRoot "test_backend.ps1")

Write-Host "== test_all: frontend =="
& (Join-Path $PSScriptRoot "test_frontend.ps1")

if (Test-Path "deploy\\docker-compose.yml") {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker not found but deploy/docker-compose.yml exists"
  }
  Write-Host "== test_all: docker compose config =="
  docker compose -f deploy/docker-compose.yml config | Out-Null
}

Write-Host "== test_all: OK =="

