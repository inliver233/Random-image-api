# Catalog Store Port

Status: **Phase 4 readiness** (SQLite default; Postgres dialect label only)

Implementation:

- Protocol: `CatalogStore` in `backend/app/db/catalog.py`
- Default: `SqliteCatalogStore` → existing `images_upsert` / `images_get` / `images_mark`
- Postgres label: `PostgresCatalogStore` (same helpers today; dialect-aware divergences later)
- Wire-up: `app.state.catalog_store = build_catalog_store(database_url=...)` in `main.py`

## Scope (intentionally narrow)

| Method | Role |
| --- | --- |
| `upsert_image_by_illust_page` | Import / catalog write by (illust_id, page_index) |
| `get_image_by_id` / `get_images_by_ids` | Public delivery + engine hydrate DTO |
| `mark_image_ok` / `mark_image_failure` | Delivery quality feedback |

Public delivery/get paths adopt the port progressively:

- `GET /images/{id}` and `GET /i/{id}.{ext}` → `catalog.get_image_by_id`
- `/i` + `/random` stream mark_ok / mark_failure → `catalog.mark_image_*`
- Go engine hydrate after pick → `catalog.get_image_by_id` / `get_images_by_ids`

Helpers remain available for non-public paths; injected store is preferred via `app.state.catalog_store`.

## Ops

`/healthz` → `modules.catalog.backend` = `sqlite` | `postgres` | other dialect name.

**Not included:** schema migration, dual-write, or automatic cutover. Production Postgres still requires Alembic/ops work.
