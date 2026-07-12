# CF Worker pool cutover checklist

Ops-only. Deploy/register **never** flips `CF_API_PROXY_ENABLED` / `IMAGE_EDGE_ENABLED`.

## Goals

- Hydrate / OAuth / illust detail: `via_cf` ≫ residential
- Public images: `edge_redirect` ≫ `local_stream_residential`
- Residential: emergency-only when CF ready (`RESIDENTIAL_EGRESS_EMERGENCY_ONLY=true`, default)

## API egress pool (`edge/api-worker`)

1. Deploy ≥2 bases (wrangler or Admin):
   - `.\scripts\edge\deploy-api-worker.ps1 -Name ria-api-a`
   - `.\scripts\edge\deploy-api-worker.ps1 -Name ria-api-b`
   - Or `POST /admin/api/cf-workers/deploy` with CF API token + `proxy_secret` (hardened script only)
2. Probe matrix (no flag flip):
   ```text
   python scripts/edge/probe-api-proxy.py --bases https://a,https://b --secret $SECRET --healthz --proxy-path --out api-proxy-matrix.json
   # Or admin (merged pool / override base_urls; records process-local cooldown on hard fail):
   # POST /admin/api/cf-workers/probe  {"kind":"all"}
   # POST /admin/api/cf-workers/probe  {"kind":"api","base_urls":["https://a","https://b"]}
   # POST /admin/api/cf-workers/probe  {"kind":"image","base_urls":["https://img-a"]}
   # Note: base_urls override requires kind=api|image (not kind=all) — otherwise 400.
   ```
   Expect `service_ok` + `secret_configured` (not false) on each base (CLI summary `ready_for_cf_api_proxy_flag`) /
   admin probe `ok` + `service` (api `secret_configured=false` → not ok, error `secret_not_configured`).
3. Membership without enable:
   - Env: `CF_API_PROXY_BASE_URLS=…` + `CF_API_PROXY_SECRET=…`
   - And/or `POST /admin/api/cf-workers/register` `{ "kind":"api", "base_url":"https://…" }`
     (Admin FE: Maintenance → CF Worker 池 → 注册进池 / 注销 runtime)
4. Inspect: `GET /admin/api/cf-workers/pool`, `GET /admin/api/maintenance/cf-api-proxy`, optional re-`probe`
5. Enable: `CF_API_PROXY_ENABLED=true`
6. Accept: admin metrics `new_pixiv_pixiv_api_egress_total{via="cf"}` dominates; residential near-zero when CF candidates exist
7. Rollback: `CF_API_PROXY_ENABLED=false` (or unset)

## Image edge pool (`edge/img-worker`)

1. Deploy ≥1 (better ≥2) bases with shared `IMAGE_EDGE_SECRET`
   - Admin deploy multipart includes `index` + `pure.js` (ES module import)
2. Probe matrix with a known pximg path (`--twice --healthz`); admin `POST /admin/api/cf-workers/probe` `{"kind":"image"}` for pool healthz
3. Membership: `IMAGE_EDGE_BASE_URLS` and/or register `kind=image` (FE form same card)
4. Inspect: pool + `GET /admin/api/maintenance/image-edge` (`base_urls` lists env∪overlay even when flag off)
5. Enable: `IMAGE_EDGE_ENABLED=true`
6. Accept: `new_pixiv_image_delivery_total{path="edge_redirect"}` dominates
7. Rollback: `IMAGE_EDGE_ENABLED=false`

## Random Engine (dual-run)

See [`engine-traffic-cutover.md`](./engine-traffic-cutover.md) for traffic% ramp, metrics, and rollback.

- Prod: `RANDOM_ENGINE_SECRET` **required** when `RANDOM_ENGINE_ENABLED=true` (settings load fails closed).
- Snapshot before raising traffic; watch `empty_index` / circuit.

## Notes

- Admin deploy uses **this repo’s** workers only — not ds2api open reverse proxy; ships `pure.js` with main module.
- Never store CF API tokens in runtime_settings / logs.
- Real multi-base CF deploy still needs a CF account (code path ready; flags stay default-off).
- BFF process-local base cooldown (~30s after 5xx/transport or admin probe hard fail) demotes sticky dead members within a process for API + image ordered lists **and public sign/302** (prefer first hot ordered base).
- Multi-process pool membership: runtime overlay reloads from `runtime_settings` on ~5s TTL (`ensure_overlay_fresh` / `ensure_image_edge_overlay_fresh` on feed + random JSON/stream + `/i` **before** hydrate readiness) and force-refresh on admin pool/probe/maintenance status. Register/deploy on one BFF is visible on peers without restart; cooldown maps stay process-local.
- Docker/Admin deploy: image copies `edge/*/src` (index.js + pure.js); root resolution prefers `EDGE_WORKER_ROOT`/`REPO_ROOT`, else first of parents[2] (`/app`) / parents[3] (monorepo) that contains edge scripts.
- Engine G1 metrics: `/feed` batch traffic miss → one `skipped_traffic`; top-up sticky-skips dual-run without N× `skipped_sticky` counters.
- Admin FE: Maintenance → **CF Worker 池（成员 + 探针）** shows merged members + egress policy + register/unregister form + healthz probe (no CF token form).
- Emergency residential: FE force Switch or `POST /admin/api/cf-workers/egress-policy` `{"force_residential_emergency":true}` (process-local). Durable policy remains `RESIDENTIAL_EGRESS_EMERGENCY_ONLY` env.
- Admin deploy does **not** attach R2 bucket bindings — use wrangler/dashboard for Mode B/B2 R2 (`r2_binding=false` in deploy response).
