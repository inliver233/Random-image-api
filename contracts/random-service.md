# Random Service Port

Status: **Phase 4 readiness** (default plan factory)

Implementation:

- Per-request plan: `RandomService` Protocol + `RandomPickContext` in `backend/app/core/random_pick_context.py`
- App factory: `RandomServiceFactory` / `DefaultRandomServiceFactory` / `build_random_service_factory()`
- Wire-up: `app.state.random_service = build_random_service_factory()` in `main.py`
- Routes: `/random` and `/feed` resolve via `resolve_random_service_factory` then `build_context(...)`

## Scope

| Piece | Role |
| --- | --- |
| `RandomServiceFactory.build_context` | Build per-request pick plan (defaults + dedup + quality) |
| `RandomService.pick` | Single pick (Go dual-run + Python fallback) |
| `RandomService.try_engine_batch` | `/feed` batch engine path |

Catalog + RecentDedup + **RandomPickPort** (Python SQL ring) remain separate ports injected into the plan / pick path.

## Dual-run sticky skip

`pick_with_strategy(..., skip_engine=True)` forces the Python SQL path (used by `/feed` top-up after one engine batch and by `/random` stream retries after the first dual-run attempt).

Prometheus / debug honesty:

| `engine_status` / observe status | When |
| --- | --- |
| `skipped_traffic` | Engine enabled + URL set, but this request did **not** route to engine (traffic percent / no client) and `skip_engine` is false |
| `skipped_sticky` | `skip_engine=True` (must **not** count as `skipped_traffic`) |
| `skipped_circuit` | Dual-run process circuit open (or concurrent half-open probe blocked); pick fail-opens to Python without calling engine |
| `skipped_not_routed` | Feed `?debug=1` only: batch dual-run not attempted (`try_engine_batch` → `eng_meta is None`); not a Prometheus label |

Process circuit (BFF, not Go): 5 consecutive hard statuses (`unavailable` / `empty_index`) → open ~30s; success resets; soft miss does not trip. Snapshot shape: `{ state, consecutive_failures, open_remaining_s, failure_threshold, open_s }`.

### Public JSON honesty (`?debug=1`)

| Endpoint | Where `engine_status` appears |
| --- | --- |
| `GET /random` (`format=json` / `simple_json`) | `data.debug.engine_status` (per pick) |
| `GET /feed` | Envelope only: `data.debug.engine_status` (+ `batch_count` / `topup_count` / `topup_skip_engine`); per-item debug stays omitted for `/wtf` bandwidth |

When dual-run is not routed for the batch (`try_engine_batch` → `eng_meta is None`), feed debug reports `engine_status=skipped_not_routed`.

## Ops

| Surface | Notes |
| --- | --- |
| `/healthz` → `modules.random_service.backend` | Default factory label: `default` |
| `/status.json` → `data.random_service.backend` | Same public label; `/status` HTML `ports:` chip includes it |
| `/healthz` → `modules.random_engine.circuit` | Process-local dual-run circuit (no outbound probe) |
| `/status.json` → `data.random_engine` | `url_configured` / `enabled` / `traffic_percent` + same process-local `circuit`; HTML chip mirrors it |
| `/metrics` (admin) | Circuit gauges: `new_pixiv_random_engine_circuit_state{state=…}`, `…_open_remaining_seconds`, `…_consecutive_failures` |
| `GET /admin/api/maintenance/modular-ports` | `random_service.backend` |
| `GET /admin/api/maintenance/random-engine` | Full engine readiness + same `circuit` object; open may set `cutover_warning` |

No env switch yet; factory is DI-swappable for tests and future alternate planners.
