# Postgres cutover inventory (PG-CUTOVER scaffolding)

**Status:** inventory + dual-dialect notes only.  
**Does not** flip production defaults, rewrite history migrations, or claim cutover done.  
Gate: mainline A CF metrics green first (`scripts/edge/cutover-checklist.md` pre-prod section).

Living product order: **CF egress productization → then deep PG**. SQLite remains **dev-only**.

## Already dual-dialect (keep using)

| Area | Location | Notes |
| --- | --- | --- |
| Backend label | `app/db/dialect.py` | `sqlite` \| `postgres` \| `other` from `DATABASE_URL` |
| Engine pools | `app/db/engine.py` | SQLite: WAL/busy/pool; PG: `PG_POOL_*` + `pool_pre_ping` |
| Contention retry | `app/db/session.py` | `with_sqlite_busy_retry` also matches PG deadlock/serialize |
| UTC text now | `app/db/utc_text_now.py` | `UtcNow` + `utc_iso_now_*`; shared by models, alembic, upserts |
| Upsert / time SQL | `app/db/images_upsert.py` | `insert_for_dialect`, `now_expr_for_dialect` → utc helper, `adapt_driver_sql_named_binds` |
| Catalog / tag ports | `app/db/catalog.py`, `tag_store.py` | Protocol + `backend` label; handlers stay store-bound |
| Job claim raw SQL | `app/jobs/claim.py` | Named binds adapted for asyncpg `$n` |
| Alembic async strip | `backend/alembic/env.py` | Strips `+aiosqlite` / `+asyncpg` / `+psycopg` |
| Compose profile | `deploy/docker-compose.yml` | `--profile postgres`; `deploy/.env.example` documents URL |
| Legacy import | `scripts/legacy/migrate_legacy_catalog.py` | Path C `import-pg` + verify against target URL |

Unit coverage for dialect helpers: `backend/tests/test_images_upsert.py`.

## Alembic chain (head)

```
20260210_0001 … 0014  → tables (imports → admin_audit)
20260211_0015         → api_keys
20260211_0016         → FTS5 virtual tables + triggers (SQLite-only body)
20260215_0017         → image popularity fields
20260218_0018         → images.illust_type
20260711_0019         → partial unique index (jobs opportunistic hydrate)  ← head
```

### SQLite-only migration SQL

| Revision | Issue | PG behavior today | Cutover action (later) |
| --- | --- | --- | --- |
| `0001`–`0015` (+ models) | Was hard-coded SQLite `strftime` server defaults | **Dual-dialect:** alembic uses `utc_iso_now_server_default(_dialect_name())`; ORM models use portable `UtcNow()` (`app/db/utc_text_now.py`) | Dry-run `alembic upgrade head` on empty Postgres; keep string timestamps |
| `0016` FTS | `CREATE VIRTUAL TABLE … USING fts5`, SQLite triggers, `sqlite`-style rowid FTS | **Upgrade/downgrade no-op on non-SQLite** (`_is_sqlite` gate) + `_try_create_fts5` still best-effort | Leave optional; runtime already falls back to `LIKE`. Later: GIN/`to_tsvector` or `pg_trgm` under TAGS-1 |
| `0019` partial unique | `CREATE UNIQUE INDEX … WHERE status IN ('pending','running')` | **Supported** on modern Postgres | Keep; verify on target PG version in dry-run |

**Do not** invent a parallel migration history for greenfield PG without an explicit dual-dialect rewrite plan. Preferred ops paths:

1. **Dev / single-host:** stay SQLite + optional later ETL.  
2. **Prod greenfield:** `DATABASE_URL=postgresql+asyncpg://…` only after dual-default migrations (or squash) exist.  
3. **Large gallery:** SQLite SSOT → `copy-sqlite` / CSV / `import-pg` into empty target already migrated (`legacy-migrate-checklist.md`).

## Runtime SQLite-specific (app)

| Symbol / path | Risk on PG | Mitigation already / needed |
| --- | --- | --- |
| `sqlite_table_exists` → `sqlite_master` | FTS probe throws / false | **Dialect-gated:** non-SQLite returns False (no `sqlite_master`); SQLite still probes; callers also catch `DBAPIError` → LIKE |
| `tags_list` / `authors_list` `MATCH` | N/A without FTS | LIKE fallback |
| `apply_sqlite_pragmas` | N/A | Gated on sqlite URL in `create_engine` |
| `enabled=1` / `status=1` in raw SQL | OK (int columns) | Prefer SQLAlchemy where practical |
| Boolean-as-int columns | PG may prefer `BOOLEAN` | Keep integer flags for cross-dialect parity until explicit model change |
| Timestamps as `Text` ISO strings | Works; no native `timestamptz` | Intentional; Engine + admin JSON stay stringly |
| Import rollback `updated_at` | Was hard-coded SQLite `strftime` | Fixed: `set_status_for_import` uses `now_expr_for_dialect` |
| Jobs queue backend name `sqlite` | Label only | `JOB_QUEUE_BACKEND=redis\|nats` still reserved/fails boot |

## Acceptance criteria for real PG cutover (not this PR)

- [ ] Fresh `alembic upgrade head` against empty Postgres succeeds without manual SQL  
  - *Partial (code):* ORM `CreateTable` dual-compile OK; `alembic upgrade head --sql` offline render has **to_char**, **no strftime/fts5** (`tests/test_alembic_postgres_offline_sql.py`). **Still needs** live empty-DB apply via Docker/`asyncpg`.  
- [ ] Import path (CSV or `import-pg`) + `verify` matches source counts  
- [ ] Hydrate job claim + complete under concurrent workers (no silent lock death)  
- [ ] Public `/random` + admin tags/authors search (LIKE acceptable)  
- [ ] Jobs purge (`cleanup_jobs`) and optional `WORKER_JOBS_PURGE_*` on PG  
- [ ] CF egress still primary; PG is data plane only  
- [ ] Rollback plan: point `DATABASE_URL` back only if dual-write never started (no dual-write in tree today)

## Explicitly deferred (§6.8 deep)

- Full dual-dialect rewrite of historical alembic revisions  
- Production default flip away from SQLite in compose without operator env  
- QUEUE-2 redis/nats job claim  
- TAGS large-catalog GIN / `to_tsvector`  
- 62万-row migrate proof on customer data  
- ENGINE force traffic defaults  

## Related

- Ops topology: `deploy/.env.example`, `scripts/edge/cutover-checklist.md`  
- Legacy catalog: `scripts/legacy/legacy-migrate-checklist.md`  
- Dialect helpers: `backend/app/db/images_upsert.py`, `backend/app/db/dialect.py`
