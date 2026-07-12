# CF API Proxy Port (Cloudflare Worker egress pool)

Status: **Phase 5 — primary hydrate/OAuth egress (optional flag)**

Implementation:

- Worker: `edge/api-worker`
- Python client: `backend/app/core/cf_api_proxy.py`
- Egress plan (CF-first + residential): `backend/app/core/proxy_selector.py` → `iter_pixiv_api_egress`
- Wiring: hydrate OAuth refresh + illust detail use the shared plan; residential proxy pool is fallback

## URL

```
{base}/p/{host}{path}?query
```

| Part | Meaning |
| --- | --- |
| `base` | Worker base from `CF_API_PROXY_BASE_URLS` (sticky multi-base) |
| `host` | Exact allowlisted hostname (e.g. `app-api.pixiv.net`) |
| `path` | Upstream path starting with `/` |

**Required gate (fail-closed):** request header `X-Proxy-Secret` must match non-empty Worker `PROXY_SECRET` / backend `CF_API_PROXY_SECRET`. Empty Worker secret rejects all `/p/*`.

Optional isolate rate limit: Worker `RATE_LIMIT_RPM` (default 600; `0` disables) + `RATE_LIMIT_BURST`.

Health:

```
GET /healthz|/  →  {"ok":true,"service":"random-image-api-proxy","allowed_hosts":[...],"secret_required":true,"secret_configured":bool,"rate_limit":{...}}
```

## Allowed hosts (Worker default)

- `oauth.secure.pixiv.net`
- `app-api.pixiv.net`
- `public-api.secure.pixiv.net`

Override via Worker `ALLOWED_HOSTS` CSV.

## Backend env

| Env | Role |
| --- | --- |
| `CF_API_PROXY_ENABLED` | `true` to prefer CF egress for Pixiv API |
| `CF_API_PROXY_BASE_URLS` | CSV of worker bases (sticky hash by host+path) |
| `CF_API_PROXY_SECRET` | **Required** for ready (sent as `X-Proxy-Secret`; Worker `PROXY_SECRET` is fail-closed) |

When disabled / not ready, `iter_pixiv_api_egress` yields residential-only attempts via `select_proxy_uri_for_url`.

When **ready**: CF multi-base is always **first**. Under `RESIDENTIAL_EGRESS_EMERGENCY_ONLY=true` (default), residential is still attempted as **last resort after every CF base fails** (TOKEN-2: OAuth test-refresh / hydrate must not hard-502 solely because CF pool was ready). Happy path stays near-zero residential because callers break on the first CF success. `force_residential_emergency` remains the process/admin override (also used for image-origin policy).

### Runtime pool overlay (ops register / deploy)

Env CSV bases merge with runtime-registered bases (`cf_pool.api.base_urls` in `runtime_settings`, process overlay in `cf_pool_overlay`):

| Surface | Notes |
| --- | --- |
| `GET /admin/api/cf-workers/pool` | env + runtime + merged members + egress policy + process-local `base_cooldown` |
| `POST /admin/api/cf-workers/register` | add runtime base (`kind=api\|image`, `base_url`) |
| `POST /admin/api/cf-workers/unregister` | remove runtime base only (does **not** delete CF script; env bases untouched) |
| `POST /admin/api/cf-workers/deploy` | CF API upload of **hardened** `edge/api-worker` or `edge/img-worker`, enable workers.dev, auto-register (default), **default `enable_business=true`** writes runtime secret + enabled overlay (OR with env); never stores CF API token; advanced `enable_business=false` = deploy-only |
| `POST /admin/api/cf-workers/delete-script` | optional hard teardown: CF API DELETE worker script; Token never stored; optional `unregister_pool` when `kind`+`base_url` provided |
| `POST /admin/api/cf-workers/probe` | outbound `GET {base}/healthz` for merged pool (or body `base_urls`); optional `kind=api\|image\|all` (default all), `timeout_s`; `base_urls` override requires `kind=api` or `kind=image` (not `all`); records process-local base cooldown on hard fail — **never** flips enable flags |
| `GET /admin/api/cf-workers/egress-policy` | residential emergency-only + process-local `force_residential_emergency` |
| `POST /admin/api/cf-workers/egress-policy` | body `force_residential_emergency=true\|false` — process-local only; does not change env |

Prod enable gate: `CF_API_PROXY_ENABLED` requires `CF_API_PROXY_SECRET` in prod; bases may be env CSV and/or runtime overlay after boot (env CSV alone is not required).

**Multi-process overlay freshness:** process memory overlay is reloaded from `runtime_settings` on a short TTL (~5s) via `ensure_overlay_fresh` before CF candidate resolution (`iter_pixiv_api_egress`). Admin `GET …/pool` and `POST …/probe` force-refresh so peers see register/deploy without restart. Cooldown maps remain process-local.

## Security rules

- No open `?url=` proxy
- Host allowlist only
- Strip client IP / CF headers at Worker
- Tokens stay on backend; Worker only forwards Authorization if present
- Do not cache API responses on edge

## Process-local base cooldown

After a CF base transport / 5xx failure (or admin probe hard fail), BFF demotes that base for ~30s (process memory only):

- `record_cf_base_outcome(request_url|base, ok=…)` from hydrate OAuth/detail + admin token test-refresh + `POST /admin/api/cf-workers/probe` (`kind=api`)
- `resolve_pixiv_api_cf_candidates` keeps sticky-first among **hot** bases, then appends cooling bases
- Success clears cooldown; not a residential DB blacklist and not shared across processes

## Ops

| Surface | Notes |
| --- | --- |
| `/healthz` → `modules.cf_api_proxy` | `enabled_flag`, `ready`, `base_url_count`, `has_secret` — config only, no outbound worker probe / secrets |
| `/status.json` → `data.cf_api_proxy` | Public subset: `enabled_flag` / `ready` / `base_url_count` (no `has_secret`); `/status` HTML chip mirrors it |
| `GET /admin/api/maintenance/cf-api-proxy` | Full readiness + `missing` list for Dashboard / Maintenance (never returns secret). OpenAPI summary: **CF API proxy readiness status** (parity note with healthz/status.json) |
| `POST /admin/api/tokens/{id}/test-refresh` | Live OAuth probe via `iter_pixiv_api_egress` (CF-first when ready). OpenAPI summary documents egress plan |
| `/metrics` (admin) → modular readiness | `new_pixiv_module_readiness{module="cf_api_proxy",flag="enabled\|ready\|has_secret"}` + `new_pixiv_module_base_url_count{module="cf_api_proxy"}` (local config only) |
| `/metrics` (admin) → egress attempts | `new_pixiv_pixiv_api_egress_total{via="cf\|residential",result="ok\|error"}` — per attempt from hydrate OAuth/detail + admin test-refresh (no secrets/base URLs in labels) |
| Admin test-refresh JSON | Success payload includes `via_cf` (bool) + optional residential `proxy` ids; never CF secret |

Deploy / probe (repo root):

```
.\scripts\edge\deploy-api-worker.ps1
python scripts/edge/probe-api-proxy.py --base-url https://… --healthz
python scripts/edge/probe-api-proxy.py --bases https://a,https://b --secret … --healthz --proxy-path --out api-proxy-matrix.json
```

Flip `CF_API_PROXY_ENABLED=true` only after all bases report `service_ok` and secret matches Worker `PROXY_SECRET`.

Offline pure helpers: `edge/api-worker/src/pure.js` + `test/proxy_vectors.json` (`npm test` in `edge/api-worker`).
