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

Env:

| Var | Default | Meaning |
| --- | --- | --- |
| `RANDOM_ENGINE_ADDR` | `:8091` | Listen address |

## BFF dual-run (Python)

Optional cutover — default **off** (Python SQLite pick remains primary):

| Env | Default | Meaning |
| --- | --- | --- |
| `RANDOM_ENGINE_URL` | empty | e.g. `http://127.0.0.1:8091` |
| `RANDOM_ENGINE_ENABLED` | `0` | set `1` to try engine first on `/random` |
| `RANDOM_ENGINE_TIMEOUT_MS` | `800` | pick timeout; on fail → Python fallback |

Admin:

- `GET /admin/api/maintenance/random-engine` — health
- `POST /admin/api/maintenance/random-engine/snapshot` — push full enabled index from SQLite

After starting the engine, push a snapshot before enabling the flag, or picks will fall through to Python.

## Implemented

1. In-memory index sorted by `random_key` (ring sample)
2. Filters aligned with Python `random_pick` (r18, tags, geometry, popularity, fail cooldown, …)
3. `strategy=random` and `strategy=quality` (weighted / best)
4. `POST /v1/admin/snapshot` full replace
5. `POST /v1/admin/events` incremental rebuild
6. BFF feature flag + admin snapshot push

## Non-goals (this service)

- Pixiv OAuth / hydrate
- Residential proxies
- Streaming image bytes (CF Worker / local `/i/`)
