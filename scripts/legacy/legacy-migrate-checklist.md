# Legacy catalog migration checklist (F3 / D2)

Official ops path to take over an old production gallery into **Random-image-api**.

## Pick the source of truth first

| Source | What you get | Preferred method |
| --- | --- | --- |
| **Same-schema SQLite** (e.g. random-mage `app.db`, prior new-stack) | Full images/tags/tokens/proxies if present | `copy-sqlite` + same `FIELD_ENCRYPTION_KEY` |
| **Node / Prisma Postgres** (often thin URL shell) | images (+ imports); usually **no** tags/tokens | `import-csv` or `import-pg` |
| **Env-only `REFRESH_TOKENS`** | OAuth secrets not in DB | `import-tokens` after catalog load |

Do **not** assume the Node PG volume is the public gallery — production tests showed a large Python SQLite as the real SSOT.

## Preconditions

1. Target stack built; **empty** catalog (or accepted wipe).
2. `alembic upgrade head` on target `DATABASE_URL`.
3. Production: prefer `postgresql+asyncpg://…` (`--profile postgres`); SQLite is OK for single-host takeover then optional later PG ETL.
4. Backup target + source before write.

## Path A — same-schema SQLite

```bash
# Stop api/worker writers
python scripts/legacy/migrate_legacy_catalog.py copy-sqlite \
  --source-db /path/to/old/app.db \
  --target-db ./data/app.db \
  --force

# Copy encryption material with the DB (or tokens decrypt fail)
#   old field_encryption_key → deploy/.env FIELD_ENCRYPTION_KEY

python scripts/legacy/migrate_legacy_catalog.py verify \
  --target-url "sqlite+aiosqlite:///./data/app.db"
```

## Path B — CSV from legacy Postgres

```bash
# On old PG host (example):
#   \copy images TO 'images.csv' CSV HEADER
#   \copy imports TO 'imports.csv' CSV HEADER

python scripts/legacy/migrate_legacy_catalog.py import-csv \
  --images-csv ./images.csv \
  --imports-csv ./imports.csv \
  --target-url "sqlite+aiosqlite:///./data/app.db"

python scripts/legacy/migrate_legacy_catalog.py verify \
  --target-url "sqlite+aiosqlite:///./data/app.db"
```

## Path C — live PG stream

```bash
python scripts/legacy/migrate_legacy_catalog.py import-pg \
  --source-url "postgresql://user:pass@127.0.0.1:5432/olddb" \
  --target-url "postgresql+asyncpg://ria:ria@127.0.0.1:5432/random_image"
```

## Tokens (TOKEN-IMPORT / D4)

```bash
# Preview without writing (no encryption key required):
python scripts/legacy/migrate_legacy_catalog.py import-tokens \
  --tokens-json ./tokens.json \
  --dry-run

# tokens.json: ["rt_…"] or [{"refresh_token":"…","label":"a"}] or {"tokens":[…]}
python scripts/legacy/migrate_legacy_catalog.py import-tokens \
  --tokens-json ./tokens.json \
  --field-encryption-key "$FIELD_ENCRYPTION_KEY" \
  --target-url "sqlite+aiosqlite:///./data/app.db"

# Or from env REFRESH_TOKENS (JSON array / {tokens:[…]} / comma-separated):
python scripts/legacy/migrate_legacy_catalog.py import-tokens \
  --from-env REFRESH_TOKENS \
  --field-encryption-key "$FIELD_ENCRYPTION_KEY" \
  --target-url "sqlite+aiosqlite:///./data/app.db"
```

- Secrets are never printed on success (masked `***` in DB; dry-run shows only a short prefix).
- Mutually exclusive: `--tokens-json` **or** `--from-env`.
- TOKEN-1 still applies after import: hydrate/refresh need at least one **enabled** row.

## Cold start / r18 (D3)

If verify warns **≥50% `x_restrict` NULL**:

- Admin → Settings: `random.defaults.default_r18_strict = false`, **or**
- Clients use `r18_strict=0` / `r18=2` until hydrate fills metadata.
- Default `r18_strict=1` will **NO_MATCH** on URL-only catalogs.

## Not migrated

- `jobs` history (purge instead; do not copy multi-million job rows)
- pg-boss / Memcached
- CF Worker runtime overlay (redeploy Admin → CF Worker)
- Engine snapshot (push from Maintenance after catalog is live)

## Acceptance

| Check | Expect |
| --- | --- |
| `GET /status.json` gallery totals | Match source images / illust distinct |
| `GET /random?format=json&r18=2` | 200 |
| Default `/random` after D3 policy | 200 (not NO_MATCH) |
| Token test-refresh | via_cf when CF api pool ready |
| `GET /version` | non-empty `git_commit` in release images (D6) |

## Related

- Production topology: README dual-mainline + `deploy/.env.example`
- **PG scaffolding inventory (no cutover claim):** [`pg-cutover-inventory.md`](./pg-cutover-inventory.md)
- Jobs purge: Admin → 维护工具 → 任务清理
- Engine: `scripts/edge/engine-traffic-cutover.md`
- CF: Admin → CF Worker
