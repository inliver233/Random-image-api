# Codex Review Remediation Plan

> Branch: `dev`  
> Baseline: `4c07a5482dda2ff7810a61ae0054ad195c8ec852`  
> Audit source: root `codexreview.md` (ignored by `/*.md`, read directly)  
> Rule: an item is marked `completed` only after code/history revalidation, a regression test, relevant integration checks, independent review, and a recorded commit.

## Working rules

- Never switch to, commit to, rewrite, or push `main`.
- Preserve pre-existing untracked `.workflow/**`, `backend/nul`, `nul`, `sh.exe.stackdump`, `新建 文本文档.txt`, `review出现偏差.txt`, and `任务全面详细要求.txt`.
- Stop rather than overwrite a pre-existing tracked modification.
- Stage only the files for the current independently verified change.
- PostgreSQL, Redis, Compose, Worker runtime, deployment, and benchmark items remain pending until their real environment evidence is captured; documentation or offline SQL is not sufficient.

## Status legend

- `pending`: audit claim mapped but not independently reproduced yet.
- `in-progress`: current code/history/tests have been inspected and remediation work has started.
- `blocked`: implementation or validation requires unavailable external state; exact blocker and runnable commands must be recorded.
- `completed`: fix and required evidence are present in the listed commit.

## Dependency order

1. **P0 release invariants:** B1, B8, B4, H12, B5, B6, B7, pximg redirect boundary checks, backend/frontend gates, H20.
2. **PostgreSQL data plane:** B2, B3, H5, H6, M4, M5, H4, M6, H7, M8, M9.
3. **Engine correctness/performance:** H1, H2, H3, M18, M19, then durable outbox/sequence/recovery and canary evidence.
4. **Edge/SLA:** H8-H11, H13-H16, M10-M11, M20.
5. **Admin/modularity/quality:** H17-H19, M1-M3, M7, M12-M17, M21.

## Audit item matrix

The root-cause text below is the audit hypothesis to revalidate, not completion evidence.

| ID | Status | Root cause to revalidate | Planned implementation | Primary files | Required tests/evidence | Depends on | Commit |
|---|---|---|---|---|---|---|---|
| B1 | in-progress | Production alias handling diverges between defaults and `is_prod`; known example credentials are accepted. | Centralize production detection; reject known insecure secrets/passwords; cover `prod` and `production`. | `backend/app/core/config.py`, `deploy/.env.example`, config tests | Focused pytest; production startup matrix; full backend gate | none | pending |
| B2 | pending | Alembic rewrites async PG URL to a sync driver not installed in the image. | Use Alembic async online mode or an explicitly installed supported sync driver. | `backend/alembic/env.py`, requirements, Dockerfile, migrations | Clean PostgreSQL 16 `alembic upgrade head`; image startup | B1 | pending |
| B3 | pending | PG job candidate select lacks row locking and revalidation. | Dialect-specific `FOR UPDATE SKIP LOCKED` claim in one transaction. | `backend/app/jobs/claim.py`, queue tests | Two real PG connections with barrier; no duplicate claim | B2 | pending |
| B4 | pending | Hydrate token-exhaustion path overwrites a recoverable error with permanent no-token state. | Separate globally unavailable tokens from per-attempt exclusions and propagate the last defer. | `backend/app/jobs/handlers/hydrate_metadata.py`, executor tests | Failing rate-limit/network/proxy-required tests become deferred, plus permanent no-token case | none | pending |
| B5 | pending | Conflict upsert mutates immutable import ownership; rollback keys off that field and omits Engine events. | Preserve creator ownership; record import membership/change ownership; rollback only owned changes and publish Engine status/delete events. | image upsert/mark, imports API, models/migrations, Engine sync | Duplicate-import/rollback DB tests; Engine convergence tests | B6 protocol guard first | pending |
| B6 | pending | Partial/old snapshots can atomically replace the live index and readiness only checks non-empty state; no durable ordering/recovery. | Immediately reject partial formal snapshots; add snapshot completeness metadata, sequence/watermark, outbox/replay, atomic commit, recovery readiness. | maintenance API, Engine sync/client/pick, Go engine | Partial snapshot rejection; stale snapshot ordering; restart/replay; rollback/delete/status/fail-cooldown convergence | B5 for producer coverage | pending |
| B7 | pending | Runtime CF API pool accepts arbitrary HTTP(S) targets and later sends credentials to them. | Restrict to verified deploy records/approved Cloudflare domains, HTTPS, public IPs, redirect/DNS checks, and authenticated challenge before enable. | CF registry/admin/proxy/OAuth/hydrate | SSRF matrix including private/link-local/DNS/redirect; credential target tests | none | pending |
| B8 | pending | Redaction filter is attached to the root logger rather than actual handlers/Uvicorn loggers. | Install handler-level redaction and configure Uvicorn access/error loggers. | logging config, app startup | Real configured child logger/access-log tests for API key, Bearer, refresh token, proxy password | none | pending |
| H1 | pending | Go pick scans every image while holding read lock. | Immutable snapshot pointer plus bitmap/posting intersections and candidate-only scoring. | `services/random-engine/**` | Golden filters; 10k/100k/621k/1M benchmarks; race test | B6 protocol | pending |
| H2 | pending | Every event rebuilds and sorts the entire index under write lock; sync is gated by URL rather than a distinct flag. | Add `RANDOM_ENGINE_SYNC_ENABLED`; asynchronous outbox consumer; incremental/COW batch updates and atomic swap. | backend Engine sync/config/deploy, Go engine | Import/hydrate non-blocking tests; event throughput and race benchmark | B6 | pending |
| H3 | pending | Snapshot pages accumulate into one Python list and one large JSON body. | Versioned begin/chunk/commit or efficient binary artifact with row count/hash. | Engine sync/client, Go snapshot transport | Interrupted chunk, hash/count mismatch, resume/atomicity, 621k memory report | B6 | pending |
| H4 | pending | Random-key index order does not match default filter/order clauses. | Add dialect-specific high-frequency/partial indexes and verify plans. | image model/migrations/random pick | SQLite and PG `EXPLAIN` assertions at representative scale | B2 | pending |
| H5 | pending | Direct raw SQL uses `%s` for SQLAlchemy asyncpg, which requires `$n` or named SQLAlchemy binds. | Replace hot-path raw marker construction with `sa.text` named binds or one proven adapter. | upsert, health, summary, totals and shared helpers | Live PG heartbeat/summary/totals/upsert tests | B2 | pending |
| H6 | pending | Retry predicate unwraps only `OperationalError`, missing common `DBAPIError` SQLSTATE wrappers. | Split SQLite busy retry from PG transaction retry; inspect `40001`/`40P01` and bounded backoff. | DB session/retry helpers | Wrapped asyncpg serialization/deadlock unit tests plus live contention | B2 | pending |
| H7 | pending | Legacy migration is partial, row-by-row, non-resumable, and does not preserve/validate relationships. | Build staged COPY ETL for images/imports/tags/links/tokens/proxies/bindings with mappings, checkpoints, sequence reset and validation. | `scripts/legacy/**`, tracked migration docs/reports | Generated/real 621k migration; counts/hashes/orphans; resume injection | B2, M4, M5 | pending |
| H8 | pending | Same-origin semantics are implemented by relaying all bytes through Python and sharing its control-plane pool. | Prefer same-origin CF route/service binding; if interim relay remains, split data/control pools and publish capacity limits. | delivery/http clients, deploy/edge | TTFB/bytes/FD/load comparison and rollback | H9-H11 | pending |
| H9 | pending | Candidate resolver exists but production delivery uses one signed URL and records no online base outcome. | Iterate verified candidates within a global deadline and feed cooldown/circuit outcomes. | image edge/delivery | Multi-base fault-injection tests | B7, H10 | pending |
| H10 | pending | Worker/gate/storage errors are treated as image content failures. | Return structured failure classes; only trusted origin content failures mutate image health. | BFF delivery, img-worker | Fail-count invariants for Worker/R2/secret/rate-limit failures | H9 | pending |
| H11 | pending | Cold misses and HEAD buffer/download complete objects without size/content controls or singleflight. | Stream/tee bounded GETs, metadata-only HEAD, limits and MIME/magic/dimension validation, singleflight. | `edge/img-worker/**` | Runtime tests for HEAD, large object, MIME/magic, Range, concurrent cold miss | H10 | pending |
| H12 | pending | Proxy URI key omits endpoint fields and invalidation misses it; client cache freezes timeout and closes leased clients. | Version/full-field URI keys and unified invalidation; request-scoped timeout; safe client lease/idle eviction. | HTTP client/proxy routing | Focused cache mutation/DB switch/timeout/concurrency tests; full backend suite | none | pending |
| H13 | pending | Worker gate and Pixiv business statuses share codes; layered retries lack operation deadline and can repeat OAuth POST. | Structured gate codes, capped candidates/retries, one deadline, idempotency-aware retry policy. | API Worker, CF proxy, selector, hydrate/OAuth | Runtime status matrix, timeout budget, POST no-replay tests | B7 | pending |
| H14 | pending | R2 enqueue is synchronous serial work and 2xx hides partial failures. | Durable async queue/job with idempotent items, bounded concurrency, partial result/retry semantics. | prewarm, handlers, img-worker | Partial failure, retry, backpressure and non-blocking job tests | H11 | pending |
| H15 | pending | CF external effects and local pool persistence are non-atomic; backend lacks cross-kind identity enforcement. | Durable idempotent deployment state machine, intent/complete phases, drain-delete, server uniqueness and probe/challenge. | CF admin/deploy/persistence/models | Crash/retry state-machine tests; same-name rejection; probe-before-enable | B7 | pending |
| H16 | pending | Metrics/circuits/overlays are process-local and healthz is used as readiness. | Split `/livez` and `/readyz`; aggregate/scrape processes; publish one effective-config snapshot and instance identity. | health/metrics/deploy/status | API+worker+replica aggregation; dependency fault readiness tests | B2, H9 | pending |
| H17 | pending | SPA fallback treats `api-keys` as an API path. | Match only `api` or `api/`; generate deep-link coverage for every nav route. | admin UI fallback, frontend routes/tests | Dist-backed deep-link tests | frontend gate | pending |
| H18 | pending | Multi-key settings update creates and commits one session per key. | Batch upsert in one Unit of Work and invalidate only after commit. | settings API/runtime settings | Rollback-on-Nth-error and success atomicity tests | M1/UoW direction | pending |
| H19 | pending | Paginated pool refresh reinitializes dirty local state; falsy coercion changes weight zero to one. | One-time initialization/revisioned merge and explicit finite-number parsing. | ProxyPools page/API | Multi-page draft and weight=0 UI/API tests | frontend gate | pending |
| H20 | pending | CI omits dev, Go, live PG, image builds, Worker runtime, coverage; current gates are red. | Restore all existing suites, listen to dev, add Go/PG/coverage/image/runtime jobs incrementally. | `.github/workflows/**`, Dockerfiles/scripts | CI-equivalent local commands and workflow review | preceding P0 fixes | pending |
| M1 | pending | Ports expose `AsyncSession`/ORM and do not own persistence boundaries. | Business DTO/query ports plus explicit UnitOfWork; migrate call sites incrementally. | catalog/tag/random ports and consumers | Contract tests with alternate fake implementation | H18, PG foundation | pending |
| M2 | pending | `memory` is an alias for the same DB queue and labels hide the real dialect. | Rename to `DatabaseJobQueue`, report dialect honestly, or implement a real external backend via outbox. | jobs queue/contracts/status | Factory/backend honesty and integration tests | B3 | pending |
| M3 | pending | Import jobs reference a local relative payload file. | BlobStore port with content hash/object key; local and object-storage implementations. | import API/jobs/storage | API/worker separate-filesystem test, resume/dedup | M1 | pending |
| M4 | pending | ORM partial unique index lacks PG predicate and Alembic ignores metadata. | Add PG predicate; wire metadata; add schema drift check. | jobs model, Alembic env | PG schema comparison/autogenerate no-op | B2 | pending |
| M5 | pending | Migration 0019 creates a unique partial index without deterministic cleanup. | Dedupe/cancel older active jobs before index creation; add preflight. | migration 0019 or follow-up | Upgrade fixture with duplicates on SQLite and PG | B2, M4 | pending |
| M6 | pending | PG tag/author search falls back to unindexed `%LIKE%`. | Preserve case-insensitive semantics and add pg_trgm GIN or supported FTS. | tag/author queries and migrations | PG semantic parity and query plans at scale | B2 | pending |
| M7 | pending | Admin image list aggregates the complete image-tag table before paging. | Page image ids first, then aggregate tags only for that page. | admin list query | SQL/result parity and plan/perf test | B2 optional | pending |
| M8 | in-progress | Production permits default SQLite and known example secrets. | Reject SQLite in production unless explicit safe override; reject known placeholders. | config/deploy/docs/tests | prod matrix including override; startup evidence | B1 | pending |
| M9 | pending | SQLite defaults allow excessive concurrent writers. | Conservative SQLite write budget and explicit PG requirement for high concurrency. | config/worker/deploy | concurrency/busy-rate test and docs | M8 | pending |
| M10 | pending | Each API replica overwrites a shared random-total value. | Use metrics as source or atomic deltas/per-instance shards. | totals persistence/status | Two-instance monotonicity test | B2/Redis decision | pending |
| M11 | pending | Key cooldown/circuit/emergency states are isolated per process. | Share critical revisions/state or explicitly surface per-instance degraded semantics. | Engine/CF/Redis runtime state | Multi-replica consistency/failover tests | H16 | pending |
| M12 | pending | Dashboard merges image/API pool status and renders partial failure as healthy. | Independent domain status with unknown/loading/error states and no false green. | Dashboard page/tests | Partial CF outage UI tests | H16 | pending |
| M13 | pending | Proxy attach defaults to pool id 1 and partial import errors are hidden. | No default until enabled pools load; validate selection; expose structured partial errors. | Proxies page/tests | Non-1 pool and partial-success tests | frontend gate | pending |
| M14 | pending | All routes are statically bundled and hidden panels continue polling. | Route-level `React.lazy`; query `enabled` by active panel; bundle budget. | App/routes/pages/query hooks | Route/deep-link tests, hidden polling tests, chunk report | H17 | pending |
| M15 | pending | Several Admin pages combine unrelated state, requests and views in one component. | Split by meaningful feature boundaries without broad cosmetic churn. | Admin pages/features | Feature-level tests and unchanged flows | H17-H19, M12-M14 | pending |
| M16 | pending | Hydrate/import/app/Go/HTML builders remain giant units. | Split domain workflows and staticize HTML/JS assets incrementally. | hydrate/import/main/Go/wtf/status/docs | Existing regression suites plus new unit boundaries | P0-P3 stabilization | pending |
| M17 | pending | Broad catches and silent passes hide failed invariants. | Narrow exception boundaries; structured degraded/retry states; fail loud for consistency. | backend hotspots | Fault-injection tests and static trend report | per-module fixes | pending |
| M18 | pending | Engine OpenAPI types/fields differ from actual Python/Go DTOs. | Generate/validate one truthful schema and golden vectors. | Engine contract/client/Go DTO | Schema validation and round-trip tests | B6, H3 | pending |
| M19 | pending | Python/Go seeded selection and NULL/date/filter boundaries diverge. | Define routing/seed contract and share golden vectors/PRNG where required. | Python pick/Engine filters/tests | Cross-process vectors including seed, NULL, dates, quality | H1, M18 | pending |
| M20 | pending | Runtime settings store CF/Image Edge secrets as plaintext JSON. | Secret reference or field-level encryption with rotation/versioning. | runtime settings/CF/image edge/crypto | DB-at-rest assertions, rotation and migration tests | B7, H15 | pending |
| M21 | pending | README/contracts/cutover docs and tracked-source assumptions have drifted. | Update only after implementations stabilize; keep benchmark/acceptance evidence under tracked docs. | README/contracts/scripts/docs | Link/checklist/schema consistency check | all relevant items | pending |

## Cross-cutting requirements not represented by one audit ID

| Requirement | Status | Mapping / evidence plan |
|---|---|---|
| Strict pximg hostname boundary and per-hop redirect validation | pending | Revalidate image/API worker and Python redirect helpers; add deceptive suffix, credentials, private IP, DNS rebinding and every-hop tests. Closely related to B7/H11/H13. |
| Rollback/delete/status/fail-cooldown Engine synchronization | pending | Covered by B5/B6; enumerate every producer and require ordered outbox convergence tests. |
| Separate Engine sync and traffic flags | pending | Covered by H2; defaults must keep both false until complete snapshot readiness. |
| Production canary 5→25→50→100 with rollback | blocked | Requires deployed production-like environment after B6/H1-H3/M18-M19; save commands and results under `docs/benchmarks/`. |
| Main/dev and pre/post performance comparison | pending | Build reproducible 5k/100k/621k/1M matrix and preserve raw results under tracked docs. |

## Validation log

| Date | Scope | Command/evidence | Result |
|---|---|---|---|
| 2026-07-13 | Initial repository guard | `git branch --show-current`; `git rev-parse HEAD`; `git status --short --branch` | On `dev` at baseline; only the listed pre-existing untracked files were present. |

## Commit log

| Commit | Audit IDs | Verification |
|---|---|---|
| pending | — | — |
