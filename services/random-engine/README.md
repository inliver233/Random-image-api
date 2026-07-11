# Random Engine (Go)

Internal high-performance **pick** service for Random-image-api.

- Contract: [`../../contracts/random-engine.openapi.yaml`](../../contracts/random-engine.openapi.yaml)
- Plan: [`../../模块化换栈与CF方案.md`](../../模块化换栈与CF方案.md) §4 / Phase 3

## Goals

| Mode | Target (local index) |
| --- | --- |
| `strategy=random` | P99 ≤ ~2ms filter+sample |
| `strategy=quality` K≤32 | P99 ≤ ~5ms |
| No image I/O | metadata only |

## Run

```bash
cd services/random-engine
go test ./...
go run ./cmd/random-engine
# GET  http://127.0.0.1:8091/healthz
# POST http://127.0.0.1:8091/v1/pick
# POST http://127.0.0.1:8091/v1/admin/snapshot
# POST http://127.0.0.1:8091/v1/admin/events
```

Docker / compose (from repo `deploy/`):

```bash
docker compose up -d --build random-engine
# BFF: RANDOM_ENGINE_URL=http://random-engine:8091  (flag still default off)
```

Env:

| Var | Default | Meaning |
| --- | --- | --- |
| `RANDOM_ENGINE_ADDR` | `:8091` | Listen address |

## BFF dual-run (Python)

Optional cutover — default **off** (Python SQLite pick remains primary):

| Env | Default | Meaning |
| --- | --- | --- |
| `RANDOM_ENGINE_URL` | empty | e.g. `http://127.0.0.1:8091` |
| `RANDOM_ENGINE_ENABLED` | `0` | set `1` to try engine first on `/random` / `/feed` |
| `RANDOM_ENGINE_TIMEOUT_MS` | `800` | pick timeout; on fail → Python fallback |
| `RANDOM_ENGINE_TRAFFIC_PERCENT` | `100` | progressive cutover 0–100 when enabled (100 = all eligible picks) |

Admin:

- `GET /admin/api/maintenance/random-engine` — health + traffic_percent / timeout_ms + `index_size` / `index_empty` / `ready_for_traffic` / `cutover_warning`
- `POST /admin/api/maintenance/random-engine/snapshot` — push full enabled index from SQLite
- `POST /admin/api/maintenance/random-engine/compare-filters` — SQLite vs engine filter cardinality (statistical dual-run; body optional public-style filters, default r18=0)

Engine-internal (ops / BFF):

- `POST /v1/admin/filter-count` — `{ "filters": {…} }` → `{ filtered, index_size, revision }`

After starting the engine, push a snapshot before enabling the flag, or picks will fall through to Python.

### Empty index vs filter miss

| Engine `code` | When | BFF `engine_status` metric label |
| --- | --- | --- |
| `INDEX_NOT_READY` | in-memory index size 0 (no snapshot yet) | `empty_index` |
| `NO_MATCH` | filters excluded all candidates | `no_match` |
| `OK` + items | successful pick | `ok` (then catalog rehydrate) |

Prometheus: `new_pixiv_random_engine_pick_total{status=…}` — watch `empty_index` / `unavailable` before raising `RANDOM_ENGINE_TRAFFIC_PERCENT`.

### Catalog → engine events (best-effort)

When `RANDOM_ENGINE_URL` is set (does **not** require `RANDOM_ENGINE_ENABLED`), control-plane writers publish deltas:

| Writer | Event |
| --- | --- |
| `hydrate_metadata` persist | `image_upserted` per page |
| `import_images` chunk | `image_upserted` for chunk ids |
| `heal_url` status 3→1 | `image_upserted` |
| admin single/bulk delete | `image_deleted` |
| admin clear all | empty snapshot replace |

Failures are logged and never fail the job/API. Python pick remains correct without the engine.

## Implemented

1. In-memory index sorted by `random_key` (ring sample)
2. Filters aligned with Python `random_pick` (r18, tags, geometry, popularity, fail cooldown, …)
3. `strategy=random` and `strategy=quality` (weighted / best; samples hard-capped at 64)
4. `POST /v1/admin/snapshot` full replace
5. `POST /v1/admin/events` incremental rebuild
6. `POST /v1/admin/filter-count` dual-run cardinality
7. BFF feature flag + traffic % cutover + admin snapshot / compare-filters
8. Auto catalog event publish from hydrate/import/heal/admin delete

## Non-goals (this service)

- Pixiv OAuth / hydrate
- Residential proxies
- Streaming image bytes (CF Worker / local `/i/`)
