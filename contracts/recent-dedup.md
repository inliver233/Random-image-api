# Recent Dedup Port

Status: **Phase 4 readiness** (memory default; Redis reserved)

Implementation:

- Protocol: `RecentDedupPort` in `backend/app/core/recent_dedup.py`
- Default: `MemoryRecentDedup` → process-local deques (`record_recent` / `get_recent_lists`)
- Factory: `build_recent_dedup(backend=...)` — unknown / `redis` → memory until implemented
- Wire-up: `app.state.recent_dedup = build_recent_dedup(...)` in `main.py`

## Env

| Env | Default | Role |
| --- | --- | --- |
| `RECENT_DEDUP_BACKEND` | `memory` | `memory` implemented. `redis` reserved → memory (fail-open) |

## Semantics (must preserve)

1. Best-effort only — never blocks `/random` or raises into the request path
2. Sliding window by `window_s` + hard caps `max_images` / `max_authors`
3. `record` is append-only within the window; prune drops expired/overflow
4. Cross-instance Redis (future) must fail open to empty windows, not 500

Module helpers (`record_recent`, `get_recent_lists`, `clear_recent`) remain the process-local source of truth so existing call sites stay behavior-identical without injection.

## Ops

`/healthz` → `modules.recent_dedup.backend` = `memory` (config-only; no outbound probe).
