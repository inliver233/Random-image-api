# Img-worker cold-stream benchmark — 2026-07-13

## Scope

This report compares the immediate pre-fix `dev` tip `7dbb127` with `fe7f3cf` for the P-PERF-1/H11 slice. It is a deterministic local Worker-runtime benchmark, not a Cloudflare POP or production-network result.

Environment:

- Windows 11
- Node.js `v24.11.1`
- Intel Core i7-12700H
- 5 sequential samples per GET/HEAD case
- 2 MiB synthetic JPEG with a valid 1×1 SOF header
- first header bytes available immediately; remaining bytes delayed by 120 ms
- Cache miss, R2 off

The baseline Worker awaited `upstream.arrayBuffer()` before returning both GET and HEAD responses. The fixed Worker validates a bounded header before returning GET, streams under client backpressure, and issues metadata-only HEAD upstream.

## Results

| Metric, mean of 5 | `7dbb127` baseline | `fe7f3cf` | Change |
|---|---:|---:|---:|
| GET handler return | 130.20 ms | 5.27 ms | -95.9% |
| GET first byte | 130.29 ms | 5.31 ms | -95.9% |
| GET full transfer | 130.30 ms | 127.07 ms | -2.5% |
| HEAD handler return | 128.91 ms | 0.77 ms | -99.4% |
| Bytes returned by GET | 2,097,152 | 2,097,152 | unchanged |

The expected outcome is lower TTFB rather than an artificial improvement in tail-bound full-transfer time. The fixed handler returned the first bytes about 125 ms before the delayed tail completed.

## Backpressure and stability evidence

The tracked runtime tests additionally prove:

- a paused client does not cause origin to drain to EOF;
- 50 concurrent same-key cold misses create one origin request within an isolate;
- followers wait for a bounded interval and receive `503 Retry-After` rather than re-fetching;
- four stalled cold streams are canceled by idle/total deadlines and the capacity slot recovers;
- known small objects use one preallocated buffer only after the client consumes the stream;
- unknown-length or large objects are stream-only and never written to Cache/R2;
- HEAD uses Cache metadata, `R2.head()`, or upstream HEAD and never downloads the image;
- mismatched length, excessive bytes/dimensions/pixels, MIME/magic mismatch, legacy markers, corrupt R2 objects and prewarm corruption do not become validated cache entries.

Validation command:

```powershell
cd edge/img-worker
node --check src/index.js
node --check src/image_stream.js
node --check src/pure.js
npm test
```

Result: 76/76 tests passed. Independent read-only review approved the stage.

## Remaining evidence

H11 remains `in-progress` pending:

- Miniflare or deployed Worker runtime integration;
- explicit Range behavior verification;
- staging/production Cache/R2/origin TTFB at concurrency 1/10/50/100;
- real POP memory/CPU/bytes metrics and cross-POP behavior.

Operational note: Cache/R2 entries without the current validation marker are deliberately rejected. In `r2_only` mode, re-prewarm verified objects before cutover.
