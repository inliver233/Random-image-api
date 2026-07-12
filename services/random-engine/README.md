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

- `GET /admin/api/maintenance/random-engine` — health + traffic_percent / timeout_ms + `index_size` / `index_empty` / `ready_for_traffic` / `cutover_warning` + process dual-run **`circuit`** snapshot (`state` / `consecutive_failures` / `open_remaining_s` / thresholds)
- `POST /admin/api/maintenance/random-engine/snapshot` — push full enabled index from SQLite
- `POST /admin/api/maintenance/random-engine/compare-filters` — SQLite vs engine filter cardinality (statistical dual-run; body optional public-style filters, default r18=0)

Public ops (no secrets, no outbound engine probe):

- `/healthz` → `modules.random_engine` includes the same process-local **`circuit`** snapshot (closed / half_open / open)
- `/status.json` → `data.random_engine` (`url_configured` / `enabled` / `traffic_percent` + **`circuit`**); `/status` HTML chip mirrors it

Engine-internal (ops / BFF):

- `POST /v1/admin/filter-count` — `{ "filters": {…} }` → `{ filtered, index_size, revision }`

After starting the engine, push a snapshot before enabling the flag, or picks will fall through to Python.

### BFF process dual-run circuit

Process-local soft circuit in `random_engine_client` (not the Go service): after **5** consecutive hard dual-run failures (`unavailable` / `empty_index`), the BFF opens for **~30s** and fail-opens picks to Python (`engine_status=skipped_circuit`). Soft misses (`no_match`, etc.) do not trip. Admin UI surfaces open state on Maintenance / Dashboard; open while traffic>0 also sets `cutover_warning` (fail-open to Python).

### Empty index vs filter miss

| Engine `code` | When | BFF `engine_status` metric label |
| --- | --- | --- |
| `INDEX_NOT_READY` | in-memory index size 0 (no snapshot yet) | `empty_index` |
| `NO_MATCH` | filters excluded all candidates | `no_match` |
| `OK` + items | successful pick | `ok` (PickItem DTO delivery; catalog rehydrate only if item incomplete) |

Prometheus:
- `new_pixiv_random_engine_pick_total{status=…}` — watch `empty_index` / `unavailable` before raising `RANDOM_ENGINE_TRAFFIC_PERCENT`
- `new_pixiv_random_engine_pick_latency_seconds{status=…}` — BFF-observed `/v1/pick` RTT (not whole `/random`)

BFF debug meta on engine hit also includes `engine_dto_count` / `engine_rehydrate_count` / `engine_rehydrate` so dual-run can confirm SQLite skip rate.

Public JSON (`?debug=1`):

- `GET /random` → `data.debug.engine_status` (per pick; includes `skipped_circuit` / `skipped_sticky` / `skipped_traffic` / …)
- `GET /feed` → envelope-only `data.debug` (`engine_status`, `batch_count`, `topup_count`, `topup_skip_engine`); per-item debug stays omitted for `/wtf` bandwidth
  - When dual-run is not routed for the batch (`eng_meta is None`), feed debug uses `engine_status=skipped_not_routed` (debug-only label; not a Prometheus series)

Prometheus dual-run skip labels (pre-registered zeros): `skipped_traffic`, `skipped_sticky`, `skipped_circuit` on `new_pixiv_random_engine_pick_total`

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
