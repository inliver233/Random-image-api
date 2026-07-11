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
GET /healthz  →  {"ok":true,"service":"random-image-edge"}
```

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

1. `caches.default` key = `GET {origin}/pximg{path}` (ignores exp/sig so re-signs share cache)
2. On miss: `fetch https://{ORIGIN_HOST}{path}` with  
   `Referer: https://www.pixiv.net/`  
   browser-like `User-Agent`
3. Optional `FALLBACK_MIRROR_HOST` ∈ `{i.pixiv.cat, i.pixiv.re, i.pixiv.nl}` or `*.workers.dev`
4. Success response: `Cache-Control: public, max-age={CACHE_TTL_SECONDS}, immutable`

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
| `IMAGE_EDGE_SECRET` | HMAC secret (must match Worker) |
| `IMAGE_EDGE_SIGN_TTL_SECONDS` | default `604800` |

When disabled or non-pximg `original_url`, public API falls back to local `/i/{id}.{ext}` stream (proxy pool / mirrors).

Escape hatches:
- `?local=1` on `/i/...` or `/random?format=image` forces origin-side stream (admin/debug).
- Explicit `proxy=` / `pixiv_cat=1` / `pximg_mirror_host=` keep local mirror path.

## Mode decision (ops)

| Mode | When |
| --- | --- |
| **B direct+Cache** | Worker→pximg success rate acceptable |
| **B2 R2-only** | 403 rate high; prewarm bytes into R2, Worker serves R2 |

POC checklist: deploy Worker → sign one known path → measure multi-region status codes.
