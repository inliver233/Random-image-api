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
GET /healthz|/  →  {"ok":true,"service":"random-image-edge","dual_secret":bool,"origin_circuit_open":bool,"r2":bool,"r2_mode":"off|read_through|r2_only"}
```

Response headers of interest for ops (HIT rate / upstream diagnostics):

| Header | Meaning |
| --- | --- |
| `X-Edge-Cache` | `HIT` / `MISS` (path-keyed Cache API; ignores exp/sig) |
| `X-Edge-Via` | origin host, emergency mirror host, or `r2` that served bytes |
| `X-Edge-Storage` | `r2` / `r2-prewarm` when bytes came from or were written via R2 path |
| `X-Edge-Circuit` | `origin-open` when soft 403 circuit skips origin |
| `Cache-Control` | `public, max-age=…, immutable` on successful image responses |

BFF counters: Prometheus `new_pixiv_image_delivery_total{path=edge_redirect|edge_unavailable|local_stream|local_i_redirect}`.

## Signing (Python / any language)

```
msg = f"{exp}\n{path}".encode("utf-8")
sig = base64url_nopad(hmac_sha256(secret, msg))
url = f"{base}/u/{exp}/{sig}/{base64url_nopad(path.encode())}"
```

`exp = now + IMAGE_EDGE_SIGN_TTL_SECONDS` (default 604800).

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
BFF hook `R2_PREWARM_URL` posts `{image_ids}` to an external consumer; map ids→paths in that consumer or call `/v1/prewarm` with paths directly.

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

### Secret rotation (zero-downtime)

1. Deploy Worker with `IMAGE_EDGE_SECRET=<new>` and `IMAGE_EDGE_SECRET_PREVIOUS=<old>` (both secrets via `wrangler secret put`).
2. Flip backend `IMAGE_EDGE_SECRET` to `<new>`; keep `IMAGE_EDGE_SECRET_PREVIOUS=<old>` optional (backend still signs only with primary).
3. Wait ≥ max URL TTL (`IMAGE_EDGE_SIGN_TTL_SECONDS`) so old signed URLs expire.
4. Remove `IMAGE_EDGE_SECRET_PREVIOUS` from Worker (and backend).

Worker verify order: primary, then previous (if distinct). `/healthz` reports `dual_secret`.

### Origin soft circuit breaker (Worker)

Isolate-local counter: after `ORIGIN_403_CIRCUIT_THRESHOLD` origin `403`s within `ORIGIN_403_CIRCUIT_WINDOW_MS`, skip origin for `ORIGIN_403_CIRCUIT_OPEN_MS` and try `FALLBACK_MIRROR_HOSTS` only. Response may include `X-Edge-Circuit: origin-open`. `/healthz` reports `origin_circuit_open`.

When disabled or non-pximg `original_url`, public API falls back to local `/i/{id}.{ext}` stream (proxy pool / mirrors).

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

1. `scripts/edge/deploy-img-worker.ps1` (or `npx wrangler deploy` in `edge/img-worker`)
2. Single base probe:
   ```
   python scripts/edge/probe-img-edge.py --base-url … --secret … --path … --twice --healthz
   ```
3. Multi-region status matrix (sticky pool candidates):
   ```
   python scripts/edge/probe-img-edge.py \
     --bases https://edge-a.example.com,https://edge-b.example.com \
     --secret … --path … --twice --healthz \
     --out edge-matrix.json
   ```
   Report includes per-base `probe_1`/`probe_2` headers + `summary.mode_suggestion` and a decision checklist.
4. Choose mode from matrix (do **not** flip `IMAGE_EDGE_ENABLED` until ops sign-off):

| Observation | Prefer |
| --- | --- |
| All bases 200; 2nd request `X-Edge-Cache: HIT`; `X-Edge-Via` ≈ origin | **B** (Cache only) |
| 200 but cold POP / want durable bytes across POPs | **B + R2** `read_through` |
| High origin 403 / `X-Edge-Circuit: origin-open` / mirrors dominate Via | **B2** `r2_only` + prewarm |
| Any base non-200 on known-good path | Fix deploy/secret/path before BFF cutover |

Matrix fields to record: `status`, `elapsed_ms`, `x_edge_cache`, `x_edge_via`, `x_edge_storage`, `x_edge_circuit`, healthz `r2`/`r2_mode`.
