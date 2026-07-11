# Random Pick Port

Status: **Phase 4 readiness** (SQLite SQL ring default; Postgres dialect label)

Implementation:

- Protocol: `RandomPickPort` in `backend/app/db/random_pick_port.py`
- Default: `SqliteRandomPick` → `db/random_pick.pick_random_image` / `pick_random_images` / `count_pick_candidates`
- Postgres label: `PostgresRandomPick` (same helpers today; dialect-aware divergences later)
- Factory: `build_random_pick(database_url=...)` maps dialect via `catalog_backend_from_database_url` / `resolve_random_pick(...)`
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

Filter clause builders live in `db/pick_filters.py` (shared with `images_list`).
Ring sample SQL stays in `db/random_pick.py`. Port does not reimplement SQL.

## Ops

- `/healthz` → `modules.random_pick.backend` = `sqlite` | `postgres` (from `DATABASE_URL`)
- Admin → `GET /admin/api/maintenance/modular-ports` → `random_pick.backend`

**Not included:** Postgres-specific ring SQL, dual-write, or automatic cutover. Production Postgres still requires Alembic/ops work (same gate as CatalogStore).
