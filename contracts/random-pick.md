# Random Pick Port

Status: **Phase 4 readiness** (SQLite SQL ring default)

Implementation:

- Protocol: `RandomPickPort` in `backend/app/db/random_pick_port.py`
- Default: `SqliteRandomPick` → `db/random_pick.pick_random_image` / `pick_random_images`
- Factory: `build_random_pick(database_url=...)` / `resolve_random_pick(...)`
- Wire-up: `app.state.random_pick` in `main.py`
- Strategy: `random_strategy.pick_by_*` accept optional `pick=`
- Service: `pick_with_strategy` / `RandomPickContext.pick` pass port through

## Scope

| Piece | Role |
| --- | --- |
| `pick_one` | Single ring sample by `random_key` + filters |
| `pick_many` | Batch sample for quality scoring |
| `count_candidates` | Same filter clauses as pick_* (dual-run compare-filters) |
| Go engine | **Not** this port — dual-run sits in `RandomService` above |

Filter semantics stay in `db/random_pick.py` (single source). Port does not reimplement SQL.

## Ops

- `/healthz` → `modules.random_pick.backend` (default `sqlite`)
- Admin → `GET /admin/api/maintenance/modular-ports` → `random_pick.backend`
