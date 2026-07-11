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

## Ops

- `/healthz` → `modules.random_service.backend` (default: `default`)
- Admin → `GET /admin/api/maintenance/modular-ports` → `random_service.backend`

No env switch yet; factory is DI-swappable for tests and future alternate planners.
