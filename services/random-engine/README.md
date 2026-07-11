# Random Engine (Go skeleton)

Internal high-performance **pick** service for Random-image-api.

- Contract: [`../../contracts/random-engine.openapi.yaml`](../../contracts/random-engine.openapi.yaml)
- Plan: [`../../模块化换栈与CF方案.md`](../../模块化换栈与CF方案.md) §4 / Phase 3

## Goals

| Mode | Target (local index) |
| --- | --- |
| `strategy=random` | P99 ≤ ~2ms filter+sample |
| `strategy=quality` K≤32 | P99 ≤ ~5ms |
| No image I/O | metadata only |

## Run (skeleton)

```bash
cd services/random-engine
go run ./cmd/random-engine
# GET  http://127.0.0.1:8091/healthz
# POST http://127.0.0.1:8091/v1/pick
```

Skeleton accepts picks against an **empty index** (`NO_MATCH`) until control plane posts a snapshot.

## Next implementation steps

1. Columnar arrays + roaring (or bitset) filters  
2. `POST /v1/admin/snapshot` full load  
3. `POST /v1/admin/events` incremental  
4. Dual-run harness vs Python `random_pick` + quality scoring  
5. BFF feature flag `RANDOM_ENGINE_URL`

## Non-goals (this service)

- Pixiv OAuth / hydrate  
- Residential proxies  
- Streaming image bytes (CF Worker / local `/i/`)
