# Job Queue Port

Status: **Phase 4 readiness** (SQLite default; Redis/NATS not wired)

Implementation:

- Protocol: `JobQueuePort` in `backend/app/jobs/queue.py`
- Default: `SqliteJobQueue` → existing `claim_next_job` / `claim_pending_job_by_id` / `renew_job_lock`
- Worker: `_JobScheduler` + `poll_and_execute_jobs` take optional `queue=`
- Factory: `build_job_queue(engine, backend=...)` — unknown backends fall back to sqlite

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

## Ops

`/healthz` → `modules.job_queue.backend` (config-only; always `sqlite` until a real alternate backend ships).
