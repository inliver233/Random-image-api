# Feed and WTF waterfall batching — 2026-07-13

## Scope

Commit `f90b29a` addresses the P-PERF-3 user-perceived waterfall path without changing the H0 `/random` contract or enabling Random Engine traffic.

Baseline `c0c741d` already made `/wtf` consume `/feed`, but a Python fallback feed of 32 items still executed up to 32 sequential pick operations in one database session. A reset could also leave an old consumer able to remove an item from the new global queue, while invalid image metadata and repeated image failures could consume replacement feed items.

The fixed path:

- performs one bounded Python batch call for normal random or quality fallback;
- permits one additional batch call only when non-strict recent dedup returned a partial result and recent rows are needed to fill the response;
- excludes Engine-returned and first-batch IDs from Python/fallback results;
- defines `/feed` sampling honestly as batch-level sampling, not repeated seeded `/random` parity;
- shares one in-flight browser feed request and checks generation immediately before `feedQueue.shift()`;
- retries feed transport acquisition only before an item has been consumed;
- performs one edge-to-local image fallback, then fails that tile without consuming another pick.

## SQLite fallback comparison

Environment:

- Windows 11
- Python 3.11.6
- SQLite/aiosqlite
- 10,000 eligible images
- feed limit 32
- 1 warm-up plus 10 measured runs per implementation

Method:

- Baseline simulation: call the production `pick_one` port 32 times, adding every selected ID to the exclusion set.
- Fixed simulation: call the production `pick_many` port once with `limit=32`.
- Count SQLAlchemy `before_cursor_execute` events and measure wall-clock duration around each session operation.

| Metric | Sequential baseline | Batched path |
|---|---:|---:|
| Mean latency | 106.559 ms | 3.134 ms |
| Maximum latency | 112.936 ms | 3.747 ms |
| SQL statements across 10 runs | 320 | 10 |
| Mean speedup | 1.0× | 34.0× |

This is a local fallback-selection benchmark, not an end-to-end public TTFB or production PostgreSQL result. `pick_random_images` may execute a second wrap-around SQL statement when the selected random-key tail has fewer than the requested rows. The API-level invariant is therefore one bounded batch call, not an inaccurate promise of exactly one SQL statement in every case.

## WTF reset race check

The generated feed-controller JavaScript was executed in Node with a deliberately stale first fetch that ignored abort completion:

1. generation 1 starts `fetchRandomData(1)` and blocks;
2. reset advances to generation 2 and aborts/invalidates the old owner;
3. generation 2 fills the queue with image 200;
4. generation 1 resolves late with image 100.

Observed result:

```json
{"ok":true,"oldResult":"AbortError","queueId":200,"fetchCall":2}
```

The stale consumer returned `AbortError`; it neither enqueued image 100 nor shifted image 200.

## Test evidence

- Python 3.11 compileall: pass.
- Feed, batch-strategy and WTF focused suite: 15 passed.
- Random quality, delivery, recent-dedup and service-factory related suite: 42 passed in the combined run.
- Generated WTF JavaScript: `node --check` passed.
- Independent review round 1 found soft-dedup, sampling-contract and stale-consumer gaps; fixes were applied.
- Independent review round 2 approved the change with no remaining Blocker/High.

## Remaining work

- H10 still needs structured infrastructure/gate/storage versus trusted image-content failure classes; this slice deliberately does not claim it.
- Move the executable WTF race harness into a tracked automated JavaScript test when the giant inline page is staticized.
- Run browser-level fast-scroll tests and production-like end-to-end `/wtf` request/TTFB/error measurements.
- Live PostgreSQL evidence remains blocked until Docker/PostgreSQL is available; this SQLite benchmark does not satisfy B2/B3 or PG cutover criteria.
- `go test -race ./...` remains blocked on this host because the installed GCC lacks 64-bit cgo support; no race result is claimed.
