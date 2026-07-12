# Job Queue Port

Status: **Phase 4 readiness** (SQLite default; Redis/NATS not wired)

Implementation:

- Protocol: `JobQueuePort` in `backend/app/jobs/queue.py`
- Default: `SqliteJobQueue` → existing `claim_next_job` / `claim_pending_job_by_id` / `renew_job_lock` + enqueue helpers
- Claim raw SQL: `:name` binds go through `adapt_driver_sql_named_binds` (sqlite named style; postgres → `$1..$n` for asyncpg) — still SQLite jobs table until external queue cutover
- Shared insert shape: `new_pending_job` / `enqueue_pending_in_session` (same-txn admin/worker paths)
- Worker: `_JobScheduler` + `poll_and_execute_jobs` take optional `queue=`; `execute_claimed_job` renews via `queue.renew_lock`
- Public opportunistic hydrate: `schedule_hydrate_if_needed` / `schedule_pick_side_effects` / `deliver_random_image_stream` / `deliver_known_image` (`/i` + legacy) accept `job_queue=` from `app.state.job_queue`
- Admin/control-plane enqueue callers:
  - `admin/proxies` probe → `queue.enqueue`
  - `easy_proxies/auto_refresh` → injected `queue=` (worker shares process queue)
  - `admin/imports`, `admin/hydration_runs` → `enqueue_pending_in_session` (coupled txn); inline import execute passes `queue=`
  - `handlers/import_images` bulk hydrate → `new_pending_job` batch add
- Factory: `build_job_queue(engine, backend=...)` — `sqlite`/`memory` only; `redis`/`nats` raise (fail loud)
- Resolve: `resolve_job_queue(queue, engine)` prefers injected port
- Wire-up: `app.state.job_queue` in `main.py`; worker builds one queue for scheduler + auto_refresh

## Methods

| Method | Role |
| --- | --- |
| `claim_next` / `claim_pending_by_id` / `renew_lock` | Worker claim path |
| `enqueue` | Insert pending job row; returns id |
| `enqueue_opportunistic_hydrate` | Dedupe active hydrate_metadata by illust; returns id or None |
| `new_pending_job` / `enqueue_pending_in_session` | Shared helpers for multi-entity SQLite txns |

## Env

| Env | Default | Role |
| --- | --- | --- |
| `JOB_QUEUE_BACKEND` | `sqlite` | Loaded into `Settings.job_queue_backend`. `sqlite` implemented. `memory` is an implemented **alias** of the same SQLite jobs table with active `backend` label `memory`. `redis` / `nats` / any other value are **rejected** at settings/factory (no silent sqlite fallback) |

Wire-up reads settings (not raw `os.environ` at call sites):

- API: `app.state.job_queue = build_job_queue(engine, backend=settings.job_queue_backend)`
- Worker: same via `load_settings().job_queue_backend`
- Worker handlers: `build_default_dispatcher(engine, settings=…)` injects the same Settings + shared catalog/tag ports into import / hydrate / heal / proxy_probe / easy_proxies builders (optional kwargs; `load_settings()` / factory fallback for direct builder tests)
- Admin inline import: same port inject from `app.state` when claiming + executing `import_images` in-process
- `/healthz` + `GET /admin/api/maintenance/modular-ports` → `requested` from settings

## Semantics (must preserve on any backend)

1. Exclusive claim → `status=running` + `locked_by` + `locked_at`
2. Priority DESC, id ASC among eligible rows
3. Reclaim when lock TTL expired (running/failed/pending with expired lock)
4. `renew_lock` only for same `worker_id` while `running`
5. Job **payload/status persistence** remains on SQLite `jobs` table for admin UI until a full external queue cutover
6. Opportunistic hydrate: at most one `pending|running` row per `(hydrate_metadata, opportunistic_hydrate, illust_id)`

## Ops

| Surface | Notes |
| --- | --- |
| `/healthz` → `modules.job_queue` | `backend` (active), `requested` (Settings), `implemented` — redis/nats rejected at settings load (no silent sqlite fallback) |
| `/status.json` → `data.job_queue` | Same public shape; `/status` HTML chip mirrors it |
| `GET /admin/api/maintenance/modular-ports` | Same honesty shape: `job_queue.backend` / `requested` / `implemented` (no `using_sqlite_fallback` field) |
| `/metrics` (admin) → modular readiness | `new_pixiv_module_readiness{module="job_queue",flag="implemented"}` (sqlite/memory only). OpenAPI summary documents scrape-time modular + circuit gauges. Proxy state SQL uses `adapt_driver_sql_named_binds` |
| Admin job CRUD (`/admin/api/jobs*`) | List/detail/retry/cancel/DLQ operate on SQLite `jobs` rows (payload + status for UI). OpenAPI summaries document that claim backends do not replace this table until full external-queue cutover |
| Admin imports / hydration-runs | Create paths use same-txn `enqueue_pending_in_session` (or inline claim via `resolve_job_queue`). OpenAPI summaries call out SQLite job persistence + CatalogStore where relevant |
| `POST /admin/api/proxies/probe` | Enqueues `proxy_probe` via JobQueuePort `queue.enqueue` (returns `job_id`). OpenAPI summary documents claim-backend vs SQLite payload split |
