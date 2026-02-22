$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

if (-not (Test-Path "frontend\\package.json")) {
  Write-Host "[test_frontend] frontend/package.json not found -> skip"
  exit 0
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm not found but frontend/package.json exists"
}

Write-Host "[test_frontend] npm -C frontend run lint (if present)"
try { npm -C frontend run lint } catch { Write-Host "[test_frontend] lint missing or failed: $($_.Exception.Message)"; throw }

Write-Host "[test_frontend] npm -C frontend run typecheck (if present)"
try { npm -C frontend run typecheck } catch { Write-Host "[test_frontend] typecheck missing or failed: $($_.Exception.Message)"; throw }

Write-Host "[test_frontend] npm -C frontend test (if present)"
if (-not $env:VITEST_MAX_THREADS) { $env:VITEST_MAX_THREADS = "1" }
if (-not $env:VITEST_MIN_THREADS) { $env:VITEST_MIN_THREADS = "1" }
Write-Host "[test_frontend] vitest threads: min=$($env:VITEST_MIN_THREADS) max=$($env:VITEST_MAX_THREADS)"
try { npm -C frontend test } catch { Write-Host "[test_frontend] test missing or failed: $($_.Exception.Message)"; throw }

Write-Host "[test_frontend] npm -C frontend run build (if present)"
if (-not $env:VITE_BUILD_MINIFY) { $env:VITE_BUILD_MINIFY = "false" }
Write-Host "[test_frontend] vite build minify=$($env:VITE_BUILD_MINIFY)"
try { npm -C frontend run build } catch { Write-Host "[test_frontend] build missing or failed: $($_.Exception.Message)"; throw }

Write-Host "[test_frontend] OK"
