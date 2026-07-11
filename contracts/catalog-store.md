# Catalog Store Port

Status: **Phase 4 readiness** (SQLite default; Postgres dialect label only)

Implementation:

- Protocol: `CatalogStore` in `backend/app/db/catalog.py`
- Default: `SqliteCatalogStore` → existing `images_upsert` / `images_get` / `images_mark`
- Postgres label: `PostgresCatalogStore` (same helpers today; dialect-aware divergences later)
- Dialect map: `catalog_backend_from_database_url` → `db.dialect.backend_from_database_url` (shared with TagStore / RandomPickPort)
- Wire-up: `app.state.catalog_store = build_catalog_store(database_url=...)` in `main.py`

## Scope (intentionally narrow)

| Method | Role |
| --- | --- |
| `upsert_image_by_illust_page` | Thin catalog write by (illust_id, page_index) — basic fields only |
| `upsert_hydrated_image_page` | Hydrate metadata write + `proxy_path` from returned id (tags stay in handler) |
| `bulk_upsert_import_rows` | Bulk import chunk: CASE-merge nullable metadata + fill empty `proxy_path`; returns ids (tags / Import counters stay in handler) |
| `get_image_by_id` / `get_images_by_ids` | Public delivery + engine hydrate DTO (status=1) |
| `get_image_by_id_any_status` | Admin/manual resolve by PK (any status) |
| `get_images_by_ids_any_status` | Engine event publish by id (any status; full row) |
| `get_images_by_illust_id` | Engine event publish by illust (any status; page_index order) |
| `list_enabled_images` | Engine full snapshot rows (status=1, id order; optional limit) |
| `map_image_ids_by_illust_page` | Import tag link map: `(illust_id, page_index) → image_id` |
| `get_image_by_illust_page` | Legacy public routes by (illust_id, page_index), status=1 only |
| `list_images` | Public cursor list with filters (status=1); returns `(rows, next_cursor)` |
| `list_admin_images` | Admin cursor list (status=1) + tag_count + optional missing-* filters; returns `((image, tag_count), next_cursor)` (response shaping stays in handler) |
| `list_authors` | Public author cursor list projected from enabled images (`user_id` group + counts; optional `q`/FTS) |
| `mark_image_ok` / `mark_image_failure` | Delivery quality feedback |
| `heal_broken_images_for_illust` | After heal hydrate: status `3` → `1` for all pages of an illust; returns healed ids |
| `set_status_for_import` | Admin import rollback: bulk set status by `created_import_id` |
| `delete_images_by_ids` | Admin hard-delete image rows by id; returns ids that existed (caller owns tags / commit / engine publish) |
| `clear_all_images` | Admin wipe of all image rows; returns rowcount (caller owns tags / commit / empty engine snapshot) |

Public delivery/get paths adopt the port progressively:

- `GET /images` → `catalog.list_images`
- `GET /images/{id}` and `GET /i/{id}.{ext}` → `catalog.get_image_by_id`
- legacy `/{illust}-{page}.{ext}` / `/{illust}.{ext}` → `catalog.get_image_by_illust_page`
- `/i` + `/random` stream mark_ok / mark_failure → `catalog.mark_image_*`
- Go engine hydrate after pick → `catalog.get_image_by_id` / `get_images_by_ids`
- Engine event/snapshot load → `catalog.get_images_by_ids_any_status` / `get_images_by_illust_id` / `list_enabled_images` (tags still joined in `random_engine_sync`)
- `hydrate_metadata` page upsert → `catalog.upsert_hydrated_image_page`
- `import_images` chunk image upsert → `catalog.bulk_upsert_import_rows` + `map_image_ids_by_illust_page` for tag links (Tag/ImageTag + Import counters remain in handler)
- `heal_url` status recovery → `catalog.heal_broken_images_for_illust`
- Admin import rollback → `catalog.set_status_for_import`
- Admin manual hydrate `image_id` → illust resolve → `catalog.get_image_by_id_any_status`
- Admin `DELETE /admin/api/images/{id}` + `POST /admin/api/images/bulk-delete` → `catalog.delete_images_by_ids` (image_tags remain in handler)
- Admin `POST /admin/api/images/clear` → `catalog.clear_all_images` (image_tags / Tag wipe remain in handler)
- Admin `GET /admin/api/images` → `catalog.list_admin_images` (missing-field response shaping remains in handler; tag_count join stays in helper)
- Public `GET /authors` → `catalog.list_authors` (author projection from Image rows)
- Worker `build_default_dispatcher` builds one `CatalogStore` and injects into import/hydrate/heal
- Tag domain is **not** on CatalogStore — see `contracts/tag-store.md`

Helpers remain available for non-public paths; injected store is preferred via `app.state.catalog_store`.

## Ops

`/healthz` → `modules.catalog.backend` = `sqlite` | `postgres` | other dialect name.

**Not included:** schema migration, dual-write, or automatic cutover. Production Postgres still requires Alembic/ops work.
