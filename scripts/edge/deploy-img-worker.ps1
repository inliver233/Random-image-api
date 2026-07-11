# Deploy Cloudflare image edge Worker (edge/img-worker).
# Requires: Node.js + npm, Cloudflare account login (`npx wrangler login`).
# Secrets are never committed — put via wrangler secret.

param(
  [switch]$SkipSecrets,
  [string]$Name = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$worker = Join-Path $root "edge\img-worker"
if (-not (Test-Path $worker)) {
  throw "Worker dir not found: $worker"
}

Push-Location $worker
try {
  if (-not (Test-Path "node_modules")) {
    npm install
  }

  if (-not $SkipSecrets) {
    Write-Host "Put IMAGE_EDGE_SECRET (paste secret, Enter). Skip with -SkipSecrets if already set."
    npx wrangler secret put IMAGE_EDGE_SECRET
  }

  $deployArgs = @("deploy")
  if ($Name) { $deployArgs += @("--name", $Name) }
  npx wrangler @deployArgs

  Write-Host ""
  Write-Host "Next:"
  Write-Host "  1) Bind custom domain img.<your-domain> in CF dashboard (or routes in wrangler.toml)"
  Write-Host "  2) Set backend env (deploy/.env):"
  Write-Host "       IMAGE_EDGE_ENABLED=true"
  Write-Host "       IMAGE_EDGE_BASE_URLS=https://<worker-or-custom-host>"
  Write-Host "       IMAGE_EDGE_SECRET=<same secret>"
  Write-Host "  3) Probe: python scripts/edge/probe-img-edge.py --base-url https://... --secret ... --path /img-original/..."
  Write-Host "  4) Optional R2: uncomment [[r2_buckets]] in wrangler.toml, recreate bucket, redeploy"
}
finally {
  Pop-Location
}
