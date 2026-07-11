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
| `upsert_image_by_illust_page` | Thin catalog write by (illust_id, page_index) — basic fields only |
| `upsert_hydrated_image_page` | Hydrate metadata write + `proxy_path` from returned id (tags stay in handler) |
| `bulk_upsert_import_rows` | Bulk import chunk: CASE-merge nullable metadata + fill empty `proxy_path`; returns ids (tags / Import counters stay in handler) |
| `get_image_by_id` / `get_images_by_ids` | Public delivery + engine hydrate DTO |
| `mark_image_ok` / `mark_image_failure` | Delivery quality feedback |
| `heal_broken_images_for_illust` | After heal hydrate: status `3` → `1` for all pages of an illust; returns healed ids |

Public delivery/get paths adopt the port progressively:

- `GET /images/{id}` and `GET /i/{id}.{ext}` → `catalog.get_image_by_id`
- `/i` + `/random` stream mark_ok / mark_failure → `catalog.mark_image_*`
- Go engine hydrate after pick → `catalog.get_image_by_id` / `get_images_by_ids`
- `hydrate_metadata` page upsert → `catalog.upsert_hydrated_image_page`
- `import_images` chunk image upsert → `catalog.bulk_upsert_import_rows` (tags + Import progress counters remain in handler)
- `heal_url` status recovery → `catalog.heal_broken_images_for_illust`

Helpers remain available for non-public paths; injected store is preferred via `app.state.catalog_store`.

## Ops

`/healthz` → `modules.catalog.backend` = `sqlite` | `postgres` | other dialect name.

**Not included:** schema migration, dual-write, or automatic cutover. Production Postgres still requires Alembic/ops work.
