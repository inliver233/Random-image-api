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
   Expect `service_ok` + secret match on each base (CLI) / `ok` + `service` on admin probe.
3. Membership without enable:
   - Env: `CF_API_PROXY_BASE_URLS=…` + `CF_API_PROXY_SECRET=…`
   - And/or `POST /admin/api/cf-workers/register` `{ "kind":"api", "base_url":"https://…" }`
4. Inspect: `GET /admin/api/cf-workers/pool`, `GET /admin/api/maintenance/cf-api-proxy`, optional re-`probe`
5. Enable: `CF_API_PROXY_ENABLED=true`
6. Accept: admin metrics `new_pixiv_pixiv_api_egress_total{via="cf"}` dominates; residential near-zero when CF candidates exist
7. Rollback: `CF_API_PROXY_ENABLED=false` (or unset)

## Image edge pool (`edge/img-worker`)

1. Deploy ≥1 (better ≥2) bases with shared `IMAGE_EDGE_SECRET`
2. Probe matrix with a known pximg path (`--twice --healthz`); admin `POST /admin/api/cf-workers/probe` `{"kind":"image"}` for pool healthz
3. Membership: `IMAGE_EDGE_BASE_URLS` and/or register `kind=image`
4. Inspect: pool + `GET /admin/api/maintenance/image-edge`
5. Enable: `IMAGE_EDGE_ENABLED=true`
6. Accept: `new_pixiv_image_delivery_total{path="edge_redirect"}` dominates
7. Rollback: `IMAGE_EDGE_ENABLED=false`

## Random Engine (dual-run)

See [`engine-traffic-cutover.md`](./engine-traffic-cutover.md) for traffic% ramp, metrics, and rollback.

- Prod: `RANDOM_ENGINE_SECRET` **required** when `RANDOM_ENGINE_ENABLED=true` (settings load fails closed).
- Snapshot before raising traffic; watch `empty_index` / circuit.

## Notes

- Admin deploy uses **this repo’s** workers only — not ds2api open reverse proxy.
- Never store CF API tokens in runtime_settings / logs.
- Real multi-base CF deploy still needs a CF account (code path ready; flags stay default-off).
- BFF process-local base cooldown (~30s after 5xx/transport or admin probe hard fail) demotes sticky dead members within a process for API + image ordered lists; public image 302 stays sticky-only.
- Multi-process pool membership: runtime overlay reloads from `runtime_settings` on ~5s TTL (`ensure_overlay_fresh` on API egress + image delivery) and force-refresh on admin pool/probe/maintenance status. Register/deploy on one BFF is visible on peers without restart; cooldown maps stay process-local.
- Admin FE: Maintenance → **CF Worker 池（成员 + 探针）** shows merged members + egress policy + healthz probe (no CF token form).
- Emergency residential: `POST /admin/api/cf-workers/egress-policy` `{"force_residential_emergency":true}` (process-local). Durable policy remains `RESIDENTIAL_EGRESS_EMERGENCY_ONLY` env.
- Admin deploy does **not** attach R2 bucket bindings — use wrangler/dashboard for Mode B/B2 R2.
