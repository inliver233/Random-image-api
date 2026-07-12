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
   ```
   Expect `service_ok` + secret match on each base.
3. Membership without enable:
   - Env: `CF_API_PROXY_BASE_URLS=…` + `CF_API_PROXY_SECRET=…`
   - And/or `POST /admin/api/cf-workers/register` `{ "kind":"api", "base_url":"https://…" }`
4. Inspect: `GET /admin/api/cf-workers/pool`, `GET /admin/api/maintenance/cf-api-proxy`
5. Enable: `CF_API_PROXY_ENABLED=true`
6. Accept: admin metrics `new_pixiv_pixiv_api_egress_total{via="cf"}` dominates; residential near-zero when CF candidates exist
7. Rollback: `CF_API_PROXY_ENABLED=false` (or unset)

## Image edge pool (`edge/img-worker`)

1. Deploy ≥1 (better ≥2) bases with shared `IMAGE_EDGE_SECRET`
2. Probe matrix with a known pximg path (`--twice --healthz`)
3. Membership: `IMAGE_EDGE_BASE_URLS` and/or register `kind=image`
4. Inspect: pool + `GET /admin/api/maintenance/image-edge`
5. Enable: `IMAGE_EDGE_ENABLED=true`
6. Accept: `new_pixiv_image_delivery_total{path="edge_redirect"}` dominates
7. Rollback: `IMAGE_EDGE_ENABLED=false`

## Notes

- Admin deploy uses **this repo’s** workers only — not ds2api open reverse proxy.
- Never store CF API tokens in runtime_settings / logs.
- Production should also set `RANDOM_ENGINE_SECRET` when dual-run is enabled.
