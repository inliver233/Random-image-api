# Image Edge Contract (Cloudflare Worker)

Status: **frozen for Phase 0/1**  
Implementation:

- Worker: `edge/img-worker`
- Python signer: `backend/app/core/image_edge.py`
- Public wiring:
  - `/random` `urls.proxy` via `resolve_public_proxy_url`
  - `/random?format=image` and `redirect=1` → 302 to signed edge when enabled
  - `/i/{id}.{ext}` → 302 to signed edge when enabled (`?local=1` forces origin stream)

## URL

```
GET|HEAD https://{edge-host}/u/{exp}/{sig}/{b64url(path)}
```

| Part | Meaning |
| --- | --- |
| `path` | Original path on `i.pximg.net`, must start with `/` (e.g. `/img-original/img/.../123_p0.jpg`) |
| `exp` | Unix seconds expiry (inclusive until `now >= exp` → 403) |
| `sig` | `base64url(HMAC-SHA256(secret, "{exp}\n{path}"))` **without padding** |
| `b64url(path)` | UTF-8 path bytes, base64url **without padding** |

Health:

```
GET /healthz|/  →  {"ok":true,"service":"random-image-edge","dual_secret":bool,"origin_circuit_open":bool,"r2":bool,"r2_mode":"off|read_through|r2_only","rate_limit":{"enabled":bool,"rpm"?:number,"burst"?:number}}
```

Isolate rate limit (optional): Worker `RATE_LIMIT_RPM` (default 3000; `0` disables) + `RATE_LIMIT_BURST`. Applied after HMAC on **Cache MISS** only (origin/R2 work); Cache HIT is free.

Response headers of interest for ops (HIT rate / upstream diagnostics):

| Header | Meaning |
| --- | --- |
| `X-Edge-Cache` | `HIT` / `MISS` (path-keyed Cache API; ignores exp/sig) |
| `X-Edge-Via` | origin host, emergency mirror host, or `r2` that served bytes |
| `X-Edge-Storage` | `r2` / `r2-prewarm` when bytes came from or were written via R2 path |
| `X-Edge-Circuit` | `origin-open` when soft 403 circuit skips origin |
| `Cache-Control` | `public, max-age=…, immutable` on successful image responses |

BFF counters: Prometheus `new_pixiv_image_delivery_total{path=...}`:

| path | Meaning |
| --- | --- |
| `edge_redirect` | 302 to signed edge URL |
| `edge_unavailable` | prefer edge but no signed URL → local cascade |
| `local_stream` | any local byte stream (legacy aggregate) |
| `local_stream_direct` | local stream without residential proxy |
| `local_stream_residential` | local stream via residential pool |
| `local_stream_mirror` | local stream via pixiv.cat / host mirror |
| `local_i_redirect` | `/i` non-stream redirect path |

BFF R2 prewarm enqueue counters: Prometheus `new_pixiv_r2_prewarm_total{result=...}` (best-effort; never raises):

| result | Meaning |
| --- | --- |
| `ok` | Worker chunk POST accepted (`status < 300`) |
| `failed_chunk` | HTTP ≥300 or transport error on a chunk |
| `skipped_disabled` | flag/URL not ready |
| `skipped_no_secret` | ready URL but no prewarm/edge secret |
| `skipped_no_paths` | no allowlisted paths after resolve |
| `error` | unexpected outer failure |

When Image Edge is **ready** (`IMAGE_EDGE_ENABLED` + secret + base URLs), public local cascade **skips residential pool selection** and fetches origin direct (or mirror if `pixiv_cat`/`proxy` override). With `RESIDENTIAL_EGRESS_EMERGENCY_ONLY=true` (default), residential is emergency-only even on the local cascade; force via `force_residential_emergency` / explicit `allow_residential_proxy`. Env bases merge with runtime-registered members (`cf_pool.image.base_urls` / `POST /admin/api/cf-workers/register|deploy`).

## Signing (Python / any language)

```
msg = f"{exp}\n{path}".encode("utf-8")
sig = base64url_nopad(hmac_sha256(secret, msg))
url = f"{base}/u/{exp}/{sig}/{base64url_nopad(path.encode())}"
```

`exp = now + IMAGE_EDGE_SIGN_TTL_SECONDS` (default 604800).

Frozen cross-language vectors:
- HMAC: `edge/img-worker/test/sign_vectors.json` (Python: `test_sign_image_edge_matches_frozen_worker_vectors`)
- Path allow/deny: `edge/img-worker/test/path_vectors.json` (Python: `test_is_edge_allowed_path_matches_frozen_path_vectors`; Worker: `npm test` in `edge/img-worker`)

## Allowed paths (Worker)

Prefixes (any of):

- `/img-original/`
- `/img-master/`
- `/img-/` (prefix match)
- `/c/`

Extensions: `jpg`, `jpeg`, `png`, `gif`, `webp`

Reject: `..`, `\`, `://`, `@`, query string in path.

## Upstream

1. `caches.default` key = `GET {worker-origin}/pximg{path}` (ignores exp/sig so re-signs share cache)
2. Optional **R2** (Mode B2 / read-through) when Worker binding `R2` is present:
   - Object key: `pximg{path}` (same shape as Cache key path)
   - `R2_MODE=read_through` (default): Cache miss → `R2.get` → origin; origin/mirror 200 → async `R2.put`
   - `R2_MODE=r2_only`: Cache miss → R2 only; **never** hit pximg (requires prewarm)
   - `R2_MODE=off`: ignore binding
   - Response may include `X-Edge-Via: r2` and `X-Edge-Storage: r2`
3. On Cache+R2 miss (or R2 off): `fetch https://{ORIGIN_HOST}{path}` with  
   `Referer: https://www.pixiv.net/`  
   browser-like `User-Agent`
4. Optional emergency mirrors (ordered, unique):
   - `FALLBACK_MIRROR_HOSTS` CSV, then legacy single `FALLBACK_MIRROR_HOST`
   - each host ∈ `{i.pixiv.cat, i.pixiv.re, i.pixiv.nl}` or `*.workers.dev`
5. Success response: `Cache-Control: public, max-age={CACHE_TTL_SECONDS}, immutable`  
   Headers: `X-Edge-Via` = host that returned 200 (origin, mirror, or `r2`)
6. **HEAD warm:** successful HEAD also fills Cache API (and R2 put when enabled) so probes do not leave the POP cold.

### Prewarm (ops)

```
POST https://{edge-host}/v1/prewarm
Header: X-Prewarm-Secret: {PREWARM_SECRET or IMAGE_EDGE_SECRET}
Body: { "paths": ["/img-original/img/.../x_p0.jpg", ...] }   // max 50, allowlisted paths only
```

Fetches each path via the same origin/mirror chain and stores into R2 (+ warms Cache).
If the object is already in R2, Worker still loads it and warms this POP's Cache API
(response fields: `prewarmed`, `failed`, `cache_warmed`, `already_r2`).

BFF adapter (`backend/app/core/r2_prewarm.py`, default off):

| Env | Role |
| --- | --- |
| `R2_PREWARM_ENABLED` | Master flag (also requires URL) |
| `R2_PREWARM_URL` | Worker base (e.g. `https://img.example.com`) |
| `R2_PREWARM_SECRET` / `PREWARM_SECRET` | `X-Prewarm-Secret`; falls back to `IMAGE_EDGE_SECRET` |

After hydrate/import/heal catalog upserts, BFF resolves `image_ids` → `original_url` via CatalogStore, allowlists paths, and POSTs `{ "paths": [...] }` in chunks of 50. Call sites pass `engine=` + optional `catalog=`.

Backend note: pure edge **302** does **not** call `mark_image_ok` (bytes not verified). Local stream path still marks ok/fail.

## Security rules

- **No** open `?url=` proxy
- Shared secret only in Worker secret + backend env (`IMAGE_EDGE_SECRET`)
- Do not put Pixiv OAuth tokens on the edge
- CORS: `Access-Control-Allow-Origin: *` for GET/HEAD/OPTIONS

## Backend env

| Env | Role |
| --- | --- |
| `IMAGE_EDGE_ENABLED` | `true` to prefer edge for public image delivery |
| `IMAGE_EDGE_BASE_URLS` | CSV of edge bases (sticky hash pick; multi-deploy pool) |
| `IMAGE_EDGE_SECRET` | HMAC secret (must match Worker; **sign + verify**) |
| `IMAGE_EDGE_SECRET_PREVIOUS` | Optional previous secret for zero-downtime rotation (**verify only** on Worker; backend never signs with it) |
| `IMAGE_EDGE_SIGN_TTL_SECONDS` | default `604800` |

### Multi-base selection (BFF)

| Helper | Behavior |
| --- | --- |
| `pick_image_edge_base_url(cfg, path)` | Sticky SHA-256 pick: same path → same base (cache locality) |
| `ordered_image_edge_base_urls(cfg, path)` | Sticky first, then remaining bases (deduped); cooling bases demoted to end — same order shape as CF API `resolve_pixiv_api_cf_candidates` |
| `record_image_edge_base_outcome` / `order_image_edge_bases_for_failover` | Process-local ~30s cooldown after probe/egress hard fails (ops ordered lists only; public sticky 302 unchanged) |
| `resolve_image_edge_signed_candidates(settings, original_url)` | Signs once per ordered base; empty when edge not ready / non-pximg |
| Public 302 / `urls.proxy` | Sticky primary only (clients cannot walk a candidate list on a single Location) |
| `POST /admin/api/cf-workers/probe` (`kind=image\|all`) | Outbound `GET {base}/healthz` for image pool; records image-edge base cooldown on hard fail; never enables `IMAGE_EDGE_ENABLED` |

### Secret rotation (zero-downtime)

1. Deploy Worker with `IMAGE_EDGE_SECRET=<new>` and `IMAGE_EDGE_SECRET_PREVIOUS=<old>` (both secrets via `wrangler secret put`).
2. Flip backend `IMAGE_EDGE_SECRET` to `<new>`; keep `IMAGE_EDGE_SECRET_PREVIOUS=<old>` optional (backend still signs only with primary).
3. Wait ≥ max URL TTL (`IMAGE_EDGE_SIGN_TTL_SECONDS`) so old signed URLs expire.
4. Remove `IMAGE_EDGE_SECRET_PREVIOUS` from Worker (and backend).

Worker verify order: primary, then previous (if distinct). `/healthz` reports `dual_secret`.

### Origin soft circuit breaker (Worker)

Isolate-local counter: after `ORIGIN_403_CIRCUIT_THRESHOLD` origin `403`s within `ORIGIN_403_CIRCUIT_WINDOW_MS`, skip origin for `ORIGIN_403_CIRCUIT_OPEN_MS` and try `FALLBACK_MIRROR_HOSTS` only. Response may include `X-Edge-Circuit: origin-open`. `/healthz` reports `origin_circuit_open`.

When disabled or non-pximg `original_url`, public API falls back to local `/i/{id}.{ext}` stream (proxy pool / mirrors).

## Ops

| Surface | Notes |
| --- | --- |
| `/healthz` → `modules.image_edge` | `enabled_flag`, `ready`, `base_url_count`, `has_secret` — config only; `base_url_count` uses raw configured bases even when not ready; no outbound edge probe / secret values |
| `/status.json` → `data.image_edge` | Same public shape (`enabled_flag` / `ready` / `base_url_count`); `/status` HTML chip mirrors it (no secrets / no edge probe) |
| `/status.json` → `data.r2_prewarm` | Public subset: `enabled_flag` / `ready` / `url_configured` (ready = flag+url+secret; no secret fields); HTML chip mirrors it |
| `GET /admin/api/maintenance/image-edge` | Same readiness shape for Dashboard / Maintenance tags (never returns secret). OpenAPI summary: **Image Edge readiness status** (parity note with healthz/status.json) |
| `GET /admin/api/maintenance/r2-prewarm` | BFF prewarm webhook readiness (`ready` requires secret; never returns secret). OpenAPI summary: **R2 prewarm readiness status** |
| Prometheus `new_pixiv_image_delivery_total{path=…}` | `edge_redirect` / `edge_unavailable` / local cascade paths |
| `/metrics` (admin) → modular readiness | `new_pixiv_module_readiness{module="image_edge",flag="enabled\|ready\|has_secret"}` + `module="r2_prewarm"` flags incl. `url_configured`/`secret_configured` + `new_pixiv_module_base_url_count{module="image_edge"}` (local config only) |

Deploy / probe (repo root):

```
.\scripts\edge\deploy-img-worker.ps1   # when present
# Worker: GET /healthz on each IMAGE_EDGE_BASE_URLS host
```

Flip `IMAGE_EDGE_ENABLED=true` only after bases report `service=random-image-edge`, HMAC secret matches, and (if using R2) prewarm/secret readiness is understood. Production flag stays **default-off**.

Escape hatches:
- `?local=1` on `/i/...` or `/random?format=image` forces origin-side stream (admin/debug).
- Explicit `proxy=` / `pixiv_cat=1` / `pximg_mirror_host=` keep local mirror path.

## Mode decision (ops)

| Mode | When | Worker config |
| --- | --- | --- |
| **B direct+Cache** | Worker→pximg success rate acceptable | No R2 binding, or `R2_MODE=off` |
| **B + R2 read-through** | Want cross-POP persistence while still allowing origin | `[[r2_buckets]]` + `R2_MODE=read_through` |
| **B2 R2-only** | 403 rate high; prewarm bytes into R2, Worker serves R2 | `[[r2_buckets]]` + `R2_MODE=r2_only` + prewarm |

POC checklist:

Offline pure helpers in `scripts/edge/probe-img-edge.py` (`sign_url`, `summarize_matrix`) are covered by
`backend/tests/test_probe_img_edge_script.py` (no network; HMAC vs frozen vectors).

1. `scripts/edge/deploy-img-worker.ps1` (or `npx wrangler deploy` in `edge/img-worker`)
2. Bind custom domain `img.<domain>` (dashboard or wrangler routes)
3. Single base probe:
   ```
   python scripts/edge/probe-img-edge.py --base-url … --secret … --path … --twice --healthz
   ```
4. Multi-region status matrix (sticky pool candidates):
   ```
   python scripts/edge/probe-img-edge.py \
     --bases https://edge-a.example.com,https://edge-b.example.com \
     --secret … --path … --twice --healthz \
     --out edge-matrix.json
   ```
   Report includes per-base `probe_1`/`probe_2` headers + `summary.mode_suggestion` and a decision checklist.
5. Choose mode from matrix (do **not** flip `IMAGE_EDGE_ENABLED` until ops sign-off):

| Observation | Prefer |
| --- | --- |
| All bases 200; 2nd request `X-Edge-Cache: HIT`; `X-Edge-Via` ≈ origin | **B** (Cache only) |
| 200 but cold POP / want durable bytes across POPs | **B + R2** `read_through` |
| High origin 403 / `X-Edge-Circuit: origin-open` / mirrors dominate Via | **B2** `r2_only` + prewarm |
| Any base non-200 on known-good path | Fix deploy/secret/path before BFF cutover |

6. Backend env only after probe green: `IMAGE_EDGE_ENABLED` + `IMAGE_EDGE_BASE_URLS` + `IMAGE_EDGE_SECRET`
7. Admin `GET /admin/api/maintenance/image-edge` → ready; watch `new_pixiv_image_delivery_total`

Matrix fields to record: `status`, `elapsed_ms`, `x_edge_cache`, `x_edge_via`, `x_edge_storage`, `x_edge_circuit`, healthz `r2`/`r2_mode`.

Related API egress: `contracts/cf-api-proxy.md` + `scripts/edge/probe-api-proxy.py` (separate Worker).
