# Recent Dedup Port

Status: **Phase 4** (memory default; optional Redis for multi-instance)

Implementation:

- Protocol: `RecentDedupPort` in `backend/app/core/recent_dedup.py`
- Default: `MemoryRecentDedup` → process-local deques (`record_recent` / `get_recent_lists`)
- Optional: `RedisRecentDedup` → Redis sorted sets (`np:recent:images` / `np:recent:authors`)
- Factory: `build_recent_dedup(backend=..., redis_url=...)` — Redis only when **backend=redis and URL set**
- Wire-up: `app.state.recent_dedup = build_recent_dedup(...)` in `main.py` (shares `REDIS_URL` with API-key RL)

## Env

| Env | Default | Role |
| --- | --- | --- |
| `RECENT_DEDUP_BACKEND` | `memory` | `memory` \| `redis` |
| `REDIS_URL` | empty | Redis URL when backend=`redis` |

## Semantics (must preserve)

1. Best-effort only — never blocks `/random` or raises into the request path
2. Sliding window by `window_s` + hard caps `max_images` / `max_authors`
3. `record` is append-only within the window; prune drops expired/overflow
4. Cross-instance Redis must **fail open** (empty windows / process-local fallback), not 500
5. Call sites are **synchronous** (pick plan + delivery side-effects) — Redis uses the sync client

Module helpers (`record_recent`, `get_recent_lists`, `clear_recent`) remain the process-local source of truth so existing call sites stay behavior-identical without injection.

Public delivery side-effects adopt the port progressively:

- `/random` + `/feed` `schedule_pick_side_effects` / stream path → `recent_dedup.record`
- injected store preferred via `app.state.recent_dedup` + `resolve_recent_dedup`

## Redis details

- Score = unix wall time (not process monotonic) so multi-instance windows align
- Member = image_id / user_id string; re-record updates score (last-seen)
- Missing `redis` package, connect failure, or command error → fail-open to process-local memory
- **Non-blocking pick path:** `get_lists` never waits on Redis RTT (no `fut.result` on the request thread). Warm process cache (~0.5s TTL) returns immediately; cold/expired cache schedules a background refresh and returns process-local memory. `record` dual-writes memory + fire-and-forget Redis via a small thread pool.

## Ops

- `/healthz` → `modules.recent_dedup`:
  - `backend` = active store (`memory` / `redis`)
  - `requested` = `Settings.recent_dedup_backend` (`RECENT_DEDUP_BACKEND`)
  - `using_memory_fallback` = true when redis requested but active is memory (missing `REDIS_URL` / connect fail-open at boot)
- `GET /admin/api/maintenance/modular-ports` → `configured_backend` vs `active_backend` + `using_memory_fallback` (same honesty shape; field names differ for admin UI)
