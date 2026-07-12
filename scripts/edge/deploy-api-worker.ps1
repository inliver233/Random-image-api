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
  Write-Host "Cutover checklist (ops; flags stay off until probe passes):"
  Write-Host "  1) Probe: python scripts/edge/probe-api-proxy.py --base-url https://... --healthz"
  Write-Host "     With secret: ... --secret <PROXY_SECRET> --healthz --proxy-path"
  Write-Host "     Multi-base: ... --bases https://a,https://b --secret ... --healthz --proxy-path --out api-proxy-matrix.json"
  Write-Host "  2) Pool membership (either env CSV and/or runtime register — do not flip enable yet):"
  Write-Host "       Env: CF_API_PROXY_BASE_URLS=https://w1...,https://w2... + CF_API_PROXY_SECRET=<same as PROXY_SECRET>"
  Write-Host "       Or Admin: POST /admin/api/cf-workers/register {kind:api, base_url}"
  Write-Host "       Or one-shot: POST /admin/api/cf-workers/deploy (CF API token; hardened script; auto-register)"
  Write-Host "  3) GET /admin/api/cf-workers/pool + GET /admin/api/maintenance/cf-api-proxy → inspect members/ready"
  Write-Host "  4) Then CF_API_PROXY_ENABLED=true (Residential stays emergency-only by default)"
  Write-Host "  5) Accept: metrics via_cf ≫ residential on hydrate/OAuth; rollback = set ENABLED=false"
  Write-Host "  IMPORTANT: if Worker has PROXY_SECRET, backend MUST send CF_API_PROXY_SECRET or CF returns 403."
}
finally {
  Pop-Location
}
