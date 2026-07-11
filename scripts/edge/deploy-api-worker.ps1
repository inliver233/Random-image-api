# Deploy Cloudflare API egress Worker (edge/api-worker) for hydrate/OAuth.
# Multi-name deploy for egress diversity — pass -Name for each POP alias.

param(
  [switch]$SkipSecrets,
  [string]$Name = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$worker = Join-Path $root "edge\api-worker"
if (-not (Test-Path $worker)) {
  throw "Worker dir not found: $worker"
}

Push-Location $worker
try {
  if (-not (Test-Path "node_modules")) {
    npm install
  }

  if (-not $SkipSecrets) {
    Write-Host "Put PROXY_SECRET (must match backend CF_API_PROXY_SECRET). Skip with -SkipSecrets if set."
    npx wrangler secret put PROXY_SECRET
  }

  $deployArgs = @("deploy")
  if ($Name) { $deployArgs += @("--name", $Name) }
  npx wrangler @deployArgs

  Write-Host ""
  Write-Host "Next:"
  Write-Host "  Set backend CF_API_PROXY_ENABLED=true"
  Write-Host "  CF_API_PROXY_BASE_URLS=https://w1.workers.dev,https://w2.workers.dev"
  Write-Host "  CF_API_PROXY_SECRET=<same as PROXY_SECRET>"
  Write-Host "  IMPORTANT: if Worker has PROXY_SECRET, backend MUST send CF_API_PROXY_SECRET or CF tries 403."
}
finally {
  Pop-Location
}
