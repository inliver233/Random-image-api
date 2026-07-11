# Public API Key Rate Limit

Status: **optional Phase 4 hook** (default process-local; Redis off)

Implementation:

- Port: `ApiKeyRateLimiterPort` in `backend/app/core/api_keys.py`
- Memory: `ApiKeyRateLimiter` (token bucket, process-local)
- Redis: `RedisApiKeyRateLimiter` (Lua token bucket; optional)
- Wire-up: `build_api_key_rate_limiter` + `app.state.api_key_limiter` in `main.py`
- Middleware: `require_public_api_key` when `PUBLIC_API_KEY_REQUIRED=true`

## Env

| Env | Default | Role |
| --- | --- | --- |
| `PUBLIC_API_KEY_REQUIRED` | `false` | Enforce `X-API-Key` on public routes |
| `PUBLIC_API_KEY_RPM` | `0` | Tokens refilled per minute (`0` = no limit) |
| `PUBLIC_API_KEY_BURST` | `0` | Bucket capacity (falls back to rpm when unset/`0` with rpm>0) |
| `PUBLIC_API_KEY_RATE_LIMIT_BACKEND` | `memory` | `memory` \| `redis` |
| `REDIS_URL` | empty | Redis URL when backend=`redis` |
| `PUBLIC_API_KEY_REDIS_URL` | empty | Alias if `REDIS_URL` empty |

Redis activates only when **backend=redis and URL set**. Missing `redis` package, connect failure, or EVAL error **fails open** to process-local memory (public API stays up; multi-instance limit soft-degrades).

## Request-path latency (budgeted)

`RedisApiKeyRateLimiter.allow` must not stall public auth:

| Call | Budget | On timeout / error |
| --- | --- | --- |
| Lazy `ping` / client acquire | `asyncio.wait_for` **0.15s** | Fail open → process-local memory bucket |
| Lua `EVAL` token-bucket | `asyncio.wait_for` **0.15s** | Fail open → process-local memory bucket |

Socket connect/read timeouts on the Redis client are also short (~0.2s). Budget exists so a hung Redis never blocks `require_public_api_key` beyond a fixed ceiling.

## Ops surfaces

| Surface | Notes |
| --- | --- |
| `/healthz` → `modules.api_key_rate_limit` | `backend` (active), `requested` (Settings), `using_memory_fallback`, `redis_url_configured`, `required` — no Redis probe / secrets |
| `GET /admin/api/maintenance/api-key-rate-limit` | Active vs configured backend; never returns URL |
| Prometheus `new_pixiv_api_key_rate_limit_total{result,backend}` | `allowed` / `limited` × `memory` / `redis` |

## Semantics

- Invalid / missing key → `401` (`UNAUTHORIZED`) before rate limit.
- Rate limited → `429` (`RATE_LIMITED`).
- Token bucket: capacity=`max(1, burst or rpm)`, refill=`rpm/60` tokens/s, cost=1 per request.
