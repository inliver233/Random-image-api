# Job Queue Port

Status: **Phase 4 readiness** (SQLite default; Redis/NATS not wired)

Implementation:

- Protocol: `JobQueuePort` in `backend/app/jobs/queue.py`
- Default: `SqliteJobQueue` → existing `claim_next_job` / `claim_pending_job_by_id` / `renew_job_lock` + enqueue helpers
- Worker: `_JobScheduler` + `poll_and_execute_jobs` take optional `queue=`
- Public opportunistic hydrate: `jobs/enqueue.py` → `queue.enqueue_opportunistic_hydrate`
- Factory: `build_job_queue(engine, backend=...)` — unknown backends fall back to sqlite

## Methods

| Method | Role |
| --- | --- |
| `claim_next` / `claim_pending_by_id` / `renew_lock` | Worker claim path |
| `enqueue` | Insert pending job row; returns id |
| `enqueue_opportunistic_hydrate` | Dedupe active hydrate_metadata by illust; returns id or None |

## Env

| Env | Default | Role |
| --- | --- | --- |
| `JOB_QUEUE_BACKEND` | `sqlite` | `sqlite` (implemented). `redis` / `nats` reserved → sqlite until implemented |

## Semantics (must preserve on any backend)

1. Exclusive claim → `status=running` + `locked_by` + `locked_at`
2. Priority DESC, id ASC among eligible rows
3. Reclaim when lock TTL expired (running/failed/pending with expired lock)
4. `renew_lock` only for same `worker_id` while `running`
5. Job **payload/status persistence** remains on SQLite `jobs` table for admin UI until a full external queue cutover
6. Opportunistic hydrate: at most one `pending|running` row per `(hydrate_metadata, opportunistic_hydrate, illust_id)`

## Ops

`/healthz` → `modules.job_queue.backend` (config-only; always `sqlite` until a real alternate backend ships).
