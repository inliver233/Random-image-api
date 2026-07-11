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
    Write-Host "Optional PREWARM_SECRET (defaults to IMAGE_EDGE_SECRET on Worker if omitted)."
    Write-Host "  npx wrangler secret put PREWARM_SECRET"
    Write-Host "Optional rotation: npx wrangler secret put IMAGE_EDGE_SECRET_PREVIOUS"
  }

  $deployArgs = @("deploy")
  if ($Name) { $deployArgs += @("--name", $Name) }
  npx wrangler @deployArgs

  Write-Host ""
  Write-Host "Cutover checklist (ops; flags stay off until probe passes):"
  Write-Host "  1) Bind custom domain img.<your-domain> in CF dashboard (or routes in wrangler.toml)"
  Write-Host "  2) Probe without BFF: python scripts/edge/probe-img-edge.py --base-url https://... --secret ... --path /img-original/... --twice --healthz"
  Write-Host "  3) Backend env (deploy/.env) — enable only after probe green:"
  Write-Host "       IMAGE_EDGE_ENABLED=true"
  Write-Host "       IMAGE_EDGE_BASE_URLS=https://<worker-or-custom-host>"
  Write-Host "       IMAGE_EDGE_SECRET=<same secret>"
  Write-Host "  4) Optional R2: uncomment [[r2_buckets]] in wrangler.toml, create bucket, redeploy"
  Write-Host "  5) Optional prewarm: R2_PREWARM_ENABLED + R2_PREWARM_URL + secret (paths contract)"
  Write-Host "  6) Admin GET /admin/api/maintenance/image-edge → ready; watch new_pixiv_image_delivery_total"
}
finally {
  Pop-Location
}
