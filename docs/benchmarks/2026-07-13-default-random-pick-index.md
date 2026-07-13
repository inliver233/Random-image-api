# Default Python random-pick index — 2026-07-13

## Scope

Commits `70f439c` and `c1ff93a` implement the SQLite/default portion of H4 while Random Engine traffic remains zero.

The default public pick predicates are:

- `status = 1`;
- strict-safe `r18=0` produces `x_restrict = 0`;
- fail cooldown remains a residual `(last_fail_at IS NULL OR last_fail_at <= cutoff)` filter;
- the first ring leg adds `random_key >= r` and orders by `random_key`;
- the wrap leg uses the same filters and orders from the beginning of `random_key`.

The legacy `idx_images_filter(status, x_restrict, orientation, width, height, random_key)` could apply the first two equalities, but the unconstrained middle columns prevented it from satisfying the random-key order. Migration 0022 adds the portable B-tree `idx_images_status_x_random(status, x_restrict, random_key)` and keeps ORM metadata aligned.

## SQLite plan comparison

Baseline plan:

```text
SEARCH images USING COVERING INDEX idx_images_filter (status=? AND x_restrict=?)
USE TEMP B-TREE FOR ORDER BY
```

Fixed plan:

```text
SEARCH images USING COVERING INDEX idx_images_status_x_random
  (status=? AND x_restrict=? AND random_key>?)
```

Tracked tests assert that both the initial bound query and wrap query use the new ordered index and do not emit `TEMP B-TREE FOR ORDER BY`, including the real default fail-cooldown OR predicate.

## 100k-row local comparison

Environment:

- Windows 11
- Python 3.11.6
- SQLite/aiosqlite schema; synchronous sqlite3 measurement
- 100,000 rows, 80% strict-safe (`x_restrict=0`)
- 200 deterministic random thresholds after warm-up
- each query forced to the named old/new index to compare their access paths

| Metric | Legacy filter index | New ordered index |
|---|---:|---:|
| Mean lookup | 5.328 ms | 0.009 ms |
| p95 lookup | 7.016 ms | 0.013 ms |
| Mean speedup | 1.0× | 625.8× |
| Temporary order B-tree | yes | no |

This microbenchmark selects only the ID and isolates index access. It is not an end-to-end `/random` TTFB result, and forced-index results should not be extrapolated directly to PostgreSQL.

## Verification

- Python 3.11 compileall: pass.
- Random-key index, migration, random delivery, quality, feed and batch-strategy related suite: 29 passed.
- Clean SQLite `alembic upgrade head` creates the index; downgrade to 0021 removes it.
- PostgreSQL offline Alembic SQL generation passes and contains portable index DDL.
- Independent review approved the minimal composite index and advised against speculative extra/partial indexes.

## Remaining H4 evidence

- Docker/live PostgreSQL is unavailable on this host. Run PostgreSQL 16 `EXPLAIN (ANALYZE, BUFFERS)` for default bound/wrap queries before completing H4.
- Measure real request share and plans for `r18=2` and `r18_strict=0`; the default index cannot preserve random-key order when `x_restrict` is unconstrained or expressed as `0 OR NULL`.
- Add `(status, random_key)` only if those non-default paths justify the extra storage/write amplification.
- Compare ordinary versus PostgreSQL partial/concurrent index creation, including deployment lock time, before PG cutover.
