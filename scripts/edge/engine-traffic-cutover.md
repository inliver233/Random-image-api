# Random Engine traffic% cutover handbook

Ops-only progressive dual-run for Go `random-engine`. Python SQLite pick remains fail-open fallback.

## Preconditions

1. Engine running (`deploy` compose `random-engine` or host `127.0.0.1:8091`).
2. Snapshot pushed and non-empty:
   - `POST /admin/api/maintenance/random-engine/snapshot`
   - `GET /admin/api/maintenance/random-engine` → `index_empty=false`, `ready_for_traffic` ok
3. Optional filter parity:
   - `POST /admin/api/maintenance/random-engine/compare-filters`
4. **Secrets**
   - Prefer `RANDOM_ENGINE_SECRET` on engine + BFF (same value).
   - **Prod:** if `RANDOM_ENGINE_ENABLED=true`, `RANDOM_ENGINE_SECRET` is **required** at settings load.
5. Circuit closed: `modules.random_engine.circuit.state=closed` on `/healthz` / admin.

## Flags (default-off cutover)

| Env | Role |
| --- | --- |
| `RANDOM_ENGINE_URL` | Base URL (required to enable) |
| `RANDOM_ENGINE_ENABLED` | Master switch (still needs URL) |
| `RANDOM_ENGINE_TRAFFIC_PERCENT` | 0–100 share of eligible picks (default **100** when enabled) |
| `RANDOM_ENGINE_TIMEOUT_MS` | Pick RTT budget (default 800); miss → Python |
| `RANDOM_ENGINE_SECRET` | `X-Engine-Secret` when set |

CF Worker deploy (Admin) is **independent** of Engine flags — it auto-enables CF egress runtime overlay, not `RANDOM_ENGINE_*`.

## Recommended ramp

Do **not** jump 0 → 100 in production without metrics.

| Step | `TRAFFIC_PERCENT` | Accept before next step |
| --- | ---: | --- |
| 0. Warm | `0` (or enabled=false) | Snapshot + healthz green; compare-filters ok |
| 1. Canary | `5`–`10` | `new_pixiv_random_engine_pick_total{status="ok"}` rises; `empty_index` / `unavailable` ~0; circuit stays closed |
| 2. Sample | `25` | Latency histogram acceptable; no sticky open circuit; spot-check `/random?debug=1` `engine_status` |
| 3. Majority | `50`–`75` | Same; feed batch path ok (top-up sticky-skips dual-run; debug `engine_status=skipped_sticky` only, **no** per-item metric spam) |
| 4. Full | `100` | `skipped_traffic` ~0 when enabled; fail-open only on hard errors |

Rollback at any step:

- `RANDOM_ENGINE_TRAFFIC_PERCENT=0` (keep URL warm), or
- `RANDOM_ENGINE_ENABLED=false`

## What to watch

| Signal | Surface |
| --- | --- |
| Pick outcomes | `new_pixiv_random_engine_pick_total{status=…}` — `ok` / `empty_index` / `unavailable` / `no_match` / `skipped_*` |
| Pick RTT | `new_pixiv_random_engine_pick_latency_seconds` |
| Circuit | gauges `new_pixiv_random_engine_circuit_*`; admin `circuit` object |
| Debug | `/random?debug=1` → `data.debug.engine_status`; feed envelope `data.debug` |
| Honesty labels | `skipped_traffic` (pct roll miss — **one** per `/feed` batch decision or per `/random` pick), `skipped_circuit` (open circuit); feed top-up is debug `skipped_sticky` only (not a counter) |

Hard failures (`unavailable`, `empty_index`) open a **process-local** circuit (~5 consecutive → ~30s open). Soft `no_match` does not.

## Do not

- Enable traffic before snapshot (all picks `empty_index` → circuit open → Python).
- Ship prod with engine enabled and **empty** secret (settings load rejects).
- Treat dual-run micro-PRs as cutover progress without this ramp + metrics.
