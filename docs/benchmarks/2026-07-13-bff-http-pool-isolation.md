# BFF HTTP pool isolation — 2026-07-13

## Scope

Commit `52ebc9b` implements the interim H8/P-PERF-2 architecture while `/random?format=image` keeps the product-required same-origin 200 `edge_stream` behavior.

Before this change, long image relays shared one direct httpcore pool (`100` total / `40` keepalive) with Engine, Cloudflare, Admin, R2 prewarm, hydrate and OAuth requests. Residential image streams also shared the same per-proxy clients and URI pool with hydrate, OAuth and proxy probes.

The fixed process owns four independent capacity domains:

| Plane | Default per-client connections | Keepalive | Proxy URI pool |
|---|---:|---:|---:|
| Control direct | 100 | 40 | n/a |
| Data direct | 160 | 80 | n/a |
| Control proxy | 40 | 16 | 32 clients |
| Data proxy | 16 | 8 | 32 clients |

All values are configurable with the `HTTP_CONTROL_*`, `HTTP_DATA_*`, `HTTP_CONTROL_PROXY_*` and `HTTP_DATA_PROXY_*` variables documented in `deploy/.env.example`.

## Routing boundary

- Data plane: `stream_url`, `/random` image delivery, `/i` and legacy image delivery, including signed img-worker, direct pximg, mirror and residential byte streams.
- Control plane: random-engine calls, feed metadata picks, Admin/Cloudflare operations, R2 prewarm webhook, hydrate, OAuth, proxy probe and EasyProxies control requests.
- Production residential image delivery passes `transport=None` to the data proxy pool; it cannot accidentally reuse the direct transport and bypass proxy construction.
- Per-request timeout, manual redirect validation, active-lease protection and cancellation-safe release remain unchanged.

## Synthetic isolation check

Environment:

- Windows 11
- Python 3.11.6
- httpx MockTransport/custom blocking transport
- 20 sequential samples

Method: start a data-plane request whose transport blocks indefinitely, then issue a control-plane health request and measure its completion before releasing the data request.

| Metric | Result |
|---|---:|
| Control responses | 20/20 HTTP 200 |
| Mean control latency | 0.132 ms |
| p95 control latency | 0.239 ms |
| Maximum control latency | 0.339 ms |

This is a deterministic isolation proof, not a production throughput result. It shows that a blocked data transport does not occupy the control transport/client.

## Test evidence

- Python compileall: pass.
- Client/stream/random focused suite: 31 passed.
- `/random`, `/i`, legacy and opportunistic-hydrate controlled integration set: 20 passed.
- Related stream error/origin-routing set: 13 passed.
- Independent read-only review: approved with no blocker.

The ordinary multi-TestClient pytest process retains a known application thread on this host, so the 20-test integration set used controlled `pytest.main(...)` followed by `os._exit(code)`; no test was skipped.

## Remaining H8 evidence

- Run real concurrent long image streams plus Engine/OAuth/Admin requests and record TTFB, pool wait, open FDs, memory and error rate.
- Validate process/worker counts against host/container FD limits.
- Move the image byte plane to a same-origin Cloudflare route/service binding so Python no longer carries all public image bandwidth.
- Preserve `redirect=1` as the only browser 302 opt-in during that migration.
