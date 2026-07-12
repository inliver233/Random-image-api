# CF Worker pool cutover checklist

Ops-only. **Default path (Admin FE / deploy API):** one-page deploy → auto register + persist secret + **auto-enable business** (`cf_pool.*.enabled` runtime overlay, OR with env flags). Advanced: set `enable_business=false` to upload script only without flipping business semantics.

## Goals

- Hydrate / OAuth / illust detail: `via_cf` ≫ residential
- Public images: `edge_redirect` ≫ `local_stream_residential`
- Residential: emergency-only when CF ready (`RESIDENTIAL_EGRESS_EMERGENCY_ONLY=true`, default)
- Origin for img-worker: **`i.pximg.net`** (not open whole-site proxy)
- Multi-base: sticky pick + process-local **exponential** cooldown on failed bases (API + image pools; base 30s × 2^(streak-1), cap 300s). Inspect via `GET /admin/api/cf-workers/pool` → `base_cooldown`
- Cold catalog (mostly `x_restrict` NULL): set `default_r18_strict=false` or hydrate before expecting default `/random` (see `scripts/legacy/legacy-migrate-checklist.md`)

## Product path (recommended)

1. Admin → **CF Worker** page (`/admin/cf-worker`)
2. Fill CF API token + account_id + worker_name; kind = `image` or `api`
3. Leave secrets empty unless rotating — deploy reuses env/runtime or **auto-generates** once (`generated_secret` in response only)
4. Keep **部署后启用业务** on (default) → register + runtime enable without process restart
5. Probe healthz; accept ready tags on pool status
6. Residential stays emergency-only; Proxies page is legacy/emergency only

## API egress pool (`edge/api-worker`) — advanced / env

1. Deploy ≥2 bases (wrangler or Admin):
   - `.\scripts\edge\deploy-api-worker.ps1 -Name ria-api-a`
   - Or `POST /admin/api/cf-workers/deploy` with CF API token (default `enable_business=true`)
2. Probe matrix:
   ```text
   python scripts/edge/probe-api-proxy.py --bases https://a,https://b --secret $SECRET --healthz --proxy-path --out api-proxy-matrix.json
   # Or admin:
   # POST /admin/api/cf-workers/probe  {"kind":"all"}
   # POST /admin/api/cf-workers/probe  {"kind":"api","base_urls":["https://a","https://b"]}
   ```
   Expect `service_ok` + `secret_configured` on each base / admin probe `ok` + `service`.
3. Membership:
   - Env: `CF_API_PROXY_BASE_URLS` + `CF_API_PROXY_SECRET` + optional `CF_API_PROXY_ENABLED=true`
   - And/or Admin register / deploy (runtime overlay `cf_pool.api.*`)
4. Inspect: `GET /admin/api/cf-workers/pool`, `GET /admin/api/maintenance/cf-api-proxy`
5. Enable (if not already via deploy): env `CF_API_PROXY_ENABLED=true` **or** runtime `cf_pool.api.enabled`
6. Accept: `new_pixiv_pixiv_api_egress_total{via="cf"}` dominates
7. Rollback: env false + clear runtime enable / Admin CF Worker 页对 runtime 成员「注销」或 `POST …/unregister`

## Image edge pool (`edge/img-worker`) — advanced / env

1. Deploy ≥1 (better ≥2) bases with shared `IMAGE_EDGE_SECRET`
2. Probe with known pximg path / admin `{"kind":"image"}`
3. Membership: `IMAGE_EDGE_BASE_URLS` and/or register `kind=image`
4. Inspect: pool + `GET /admin/api/maintenance/image-edge`
5. Enable: `IMAGE_EDGE_ENABLED=true` **or** runtime `cf_pool.image.enabled`
6. Accept: `new_pixiv_image_delivery_total{path="edge_redirect"}` dominates
7. Rollback: disable flags / clear runtime enable

## Pre-prod / prod metrics acceptance (mainline A gate)

After deploy api+image and light traffic (token refresh + `/random` image open):

```text
# scrape BFF /metrics (or Prometheus)
# API egress: CF should dominate when pool ready
new_pixiv_pixiv_api_egress_total{via="cf",result="ok"}
new_pixiv_pixiv_api_egress_total{via="residential",result="ok"}

# Image delivery: edge_redirect ≫ local_stream_residential
new_pixiv_image_delivery_total{path="edge_redirect"}
new_pixiv_image_delivery_total{path="local_stream_residential"}
```

Pass criteria (ops judgment, not unit-testable without real CF):

- Pool UI: both sides `business_enabled` / `ready` after deploy
- `via=cf` ≫ `via=residential` for hydrate/token refresh
- `edge_redirect` ≫ `local_stream_residential` for public images
- Residential force switch off unless intentional emergency
- Fail: still mostly residential/local while pool shows ready → secret/sign/register mismatch (re-probe)

## Random Engine (dual-run · D7)

See [`engine-traffic-cutover.md`](./engine-traffic-cutover.md) for traffic% ramp, metrics, and rollback.

- Compose starts `random-engine`; **api/worker still need** `RANDOM_ENGINE_URL` (+ secret) — not auto-wired.
- Prod: `RANDOM_ENGINE_SECRET` **required** when `RANDOM_ENGINE_ENABLED=true` (settings load fails closed).
- Snapshot before raising traffic; watch `empty_index` / circuit.
- CF Admin deploy does **not** flip Engine flags.

## Ports & host map (D5 · twelve-factor)

| Surface | Host default | In-container / notes |
| --- | --- | --- |
| Public BFF / Admin | `23222` → `8000` | `uvicorn` on api service |
| Random Engine | `127.0.0.1:8091` → `8091` | loopback-only host bind; BFF uses `http://random-engine:8091` |
| Postgres (profile) | `5432` (if published) | `postgresql+asyncpg://ria:ria@postgres:5432/random_image` |
| Redis (profile) | `6379` (if published) | `redis://redis:6379/0` for RL/dedup |
| CF Workers | workers.dev / custom | not compose ports; secret via deploy/env overlay |

Do not publish Engine beyond loopback without a secret. SQLite file lives on `./data` volume (dev-only).

## Production data plane (mainline B)

- Production: **Postgres** for catalog + purgeable jobs; **Redis** for shared RL/dedup when multi-instance.
- SQLite = **dev-only** (not SLA path).
- Hot pick: Go Random Engine; image bytes: CF img-worker → `i.pximg.net`.
- R2 optional non-P0 (Admin deploy never attaches R2 bindings).
- Terminal jobs purge: Admin → 维护工具 → 任务清理, or `POST /admin/api/maintenance/jobs/cleanup`.
- **PG cutover is not auto:** dual-dialect helpers exist (upsert/claim/pool); historical alembic still has SQLite `strftime` + FTS5 — see `scripts/legacy/pg-cutover-inventory.md` before flipping `DATABASE_URL`.

## Notes

- Admin deploy uses **this repo’s** workers only — not ds2api open reverse proxy; ships `pure.js` with main module.
- Never store CF API tokens in runtime_settings / logs. Generated business secrets may live in runtime overlay; response echoes generated secret **once**.
- Failover: demote/retry on Worker gate errors (`None`/5xx/401/403/429), not Pixiv business 4xx (e.g. invalid_grant).
- BFF process-local base cooldown (~30s after hard fail).
- Multi-process pool: runtime overlay reloads from `runtime_settings` on ~5s TTL; force-refresh on admin pool/probe/maintenance status.
- Docker/Admin deploy: image copies `edge/*/src`; root resolution prefers `EDGE_WORKER_ROOT`/`REPO_ROOT`.
- Admin FE: **CF Worker** for daily deploy; Maintenance advanced card for Engine/R2/register polish.
- Emergency residential: FE force Switch or `POST /admin/api/cf-workers/egress-policy` `{"force_residential_emergency":true}` (process-local). Durable: `RESIDENTIAL_EGRESS_EMERGENCY_ONLY` env.
