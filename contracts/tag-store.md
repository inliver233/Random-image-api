# Tag Store Port

Status: **Phase 4 readiness** (SQLite default; Postgres dialect label only)

Implementation:

- Protocol: `TagStore` in `backend/app/db/tag_store.py`
- Default: `SqliteTagStore` → `tags_get` / `tags_list` / `tags_links`
- Postgres label: `PostgresTagStore` (same helpers today)
- Dialect map: `tag_backend_from_database_url` → `db.dialect.backend_from_database_url`
- Wire-up: `app.state.tag_store = build_tag_store(database_url=...)` in `main.py`
- Worker: `build_default_dispatcher` injects one store into import/hydrate handlers

## Scope (intentionally separate from CatalogStore)

| Method | Role |
| --- | --- |
| `get_tag_names_for_image` | Single-image tag names (ordered) |
| `map_tag_names_by_image_ids` | Engine serialize: image_id → tag name list |
| `image_has_any_tag` | `/i` opportunistic hydrate when tags missing |
| `list_tags` | Public `GET /tags` cursor list (+ FTS when available) |
| `ensure_tags_by_names` | Import: insert missing Tag by name (no translation) |
| `upsert_tags_with_translations` | Hydrate: ensure Tag + optional translated_name update |
| `link_image_tags` | Insert `(image_id, tag_id)` pairs; ignore conflicts |
| `replace_image_tags` | Hydrate: delete links for image ids then attach tag set |
| `delete_image_tags_for_image_ids` | Admin delete: clear links before image rows |
| `clear_all_image_tags` / `clear_all_tags` | Admin clear catalog (+ optional Tag wipe) |

Adoption:

- Public `GET /tags` → `tag_store.list_tags`
- Public `GET /images/{id}` + `/random?format=json` → `tag_store.get_tag_names_for_image`
- Admin image delete / bulk-delete / clear → link/tag clears via TagStore
- Import tag attach → `ensure_tags_by_names` + `link_image_tags`
- Hydrate tag replace → `upsert_tags_with_translations` + `replace_image_tags`
- Engine snapshot/events tag names → `map_tag_names_by_image_ids` (callers may inject process `TagStore` via `tag_store=` on load/snapshot/upsert publish helpers)
- Worker heal_url → forwards shared `TagStore` into hydrate_metadata (same port as import/hydrate)
- Admin inline import → `app.state.tag_store` into import_images
- `/i` hydrate check → `image_has_any_tag`

**Not included:** Catalog image rows (CatalogStore), random_pick filter subqueries, authors list.

## Ops

`/healthz` → `modules.tags.backend` = `sqlite` | `postgres` | other.

Admin `GET /admin/api/maintenance/modular-ports` → `tags.backend`.
