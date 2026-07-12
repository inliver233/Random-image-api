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

## Security rules

- No open `?url=` proxy
- Host allowlist only
- Strip client IP / CF headers at Worker
- Tokens stay on backend; Worker only forwards Authorization if present
- Do not cache API responses on edge

## Ops

| Surface | Notes |
| --- | --- |
| `/healthz` → `modules.cf_api_proxy` | `enabled_flag`, `ready`, `base_url_count`, `has_secret` — config only, no outbound worker probe / secrets |
| `/status.json` → `data.cf_api_proxy` | Public subset: `enabled_flag` / `ready` / `base_url_count` (no `has_secret`); `/status` HTML chip mirrors it |
| `GET /admin/api/maintenance/cf-api-proxy` | Full readiness + `missing` list for Dashboard / Maintenance (never returns secret). OpenAPI summary: **CF API proxy readiness status** (parity note with healthz/status.json) |
| `/metrics` (admin) → modular readiness | `new_pixiv_module_readiness{module="cf_api_proxy",flag="enabled\|ready"}` + `new_pixiv_module_base_url_count{module="cf_api_proxy"}` (local config only) |

Deploy / probe (repo root):

```
.\scripts\edge\deploy-api-worker.ps1
python scripts/edge/probe-api-proxy.py --base-url https://… --healthz
python scripts/edge/probe-api-proxy.py --bases https://a,https://b --secret … --healthz --proxy-path --out api-proxy-matrix.json
```

Flip `CF_API_PROXY_ENABLED=true` only after all bases report `service_ok` and secret matches Worker `PROXY_SECRET`.

Offline pure helpers: `edge/api-worker/src/pure.js` + `test/proxy_vectors.json` (`npm test` in `edge/api-worker`).
