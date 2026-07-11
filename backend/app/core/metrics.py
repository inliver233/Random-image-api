from __future__ import annotations

from collections.abc import Iterable

from prometheus_client import Counter, Gauge, Histogram

RANDOM_RESULTS: tuple[str, ...] = (
    "ok",
    "no_match",
    "upstream_error",
    "bad_request",
    "error",
)

JOB_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "paused",
    "canceled",
    "completed",
    "failed",
    "dlq",
)

PROXY_STATES: tuple[str, ...] = (
    "total",
    "enabled",
    "healthy",
    "unhealthy",
    "blacklisted",
)

RANDOM_REQUESTS_TOTAL = Counter(
    "new_pixiv_random_requests_total",
    "Total /random requests by result.",
    ["result"],
)

RANDOM_NO_MATCH_TOTAL = Counter(
    "new_pixiv_random_no_match_total",
    "Total /random NO_MATCH responses.",
)

RANDOM_OPPORTUNISTIC_HYDRATE_ENQUEUED_TOTAL = Counter(
    "new_pixiv_random_opportunistic_hydrate_enqueued_total",
    "Total opportunistic hydrate_metadata enqueues from /random.",
)

RANDOM_ENGINE_PICK_TOTAL = Counter(
    "new_pixiv_random_engine_pick_total",
    "Go random-engine dual-run pick outcomes (ok / fallback statuses).",
    ["status"],
)

RANDOM_ENGINE_PICK_LATENCY_SECONDS = Histogram(
    "new_pixiv_random_engine_pick_latency_seconds",
    "BFF-observed RTT for Go random-engine /v1/pick (seconds).",
    ["status"],
    buckets=(
        0.001,
        0.002,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
    ),
)

RANDOM_LATENCY_SECONDS = Histogram(
    "new_pixiv_random_latency_seconds",
    "Latency for /random endpoint (seconds).",
    buckets=(
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
    ),
)

# Public image bytes delivery path (edge 302 vs local cascade).
# Labels stay small:
#   edge_redirect | edge_unavailable | local_stream | local_i_redirect
# edge_unavailable = prefer edge but no signed URL, then local path still counted.
IMAGE_DELIVERY_TOTAL = Counter(
    "new_pixiv_image_delivery_total",
    "Public image delivery outcomes by path (edge vs local cascade).",
    ["path"],
)

# Public API key rate-limit decisions (memory or redis backend).
API_KEY_RATE_LIMIT_TOTAL = Counter(
    "new_pixiv_api_key_rate_limit_total",
    "Public API key rate-limit outcomes by result and backend.",
    ["result", "backend"],
)

UPSTREAM_STREAM_ERRORS_TOTAL = Counter(
    "new_pixiv_upstream_stream_errors_total",
    "Total upstream stream failures (stream_url).",
)

JOBS_CLAIM_TOTAL = Counter(
    "new_pixiv_jobs_claim_total",
    "Total jobs claimed by workers.",
)

JOBS_FAILED_TOTAL = Counter(
    "new_pixiv_jobs_failed_total",
    "Total jobs transitioned to failed/dlq status.",
)

TOKEN_REFRESH_FAIL_TOTAL = Counter(
    "new_pixiv_token_refresh_fail_total",
    "Total token refresh failures.",
)

JOBS_STATUS_COUNT = Gauge(
    "new_pixiv_jobs_status_count",
    "Current jobs count by status (from SQLite).",
    ["status"],
)

PROXY_ENDPOINTS_STATE_COUNT = Gauge(
    "new_pixiv_proxy_endpoints_state_count",
    "Current proxy endpoints count by state (from SQLite).",
    ["state"],
)

PROXY_PROBE_LATENCY_MS = Histogram(
    "new_pixiv_proxy_probe_latency_ms",
    "Proxy probe latency (ms).",
    buckets=(
        10.0,
        25.0,
        50.0,
        100.0,
        250.0,
        500.0,
        1000.0,
        2000.0,
        5000.0,
        10000.0,
    ),
)

METRICS_SCRAPE_ERRORS_TOTAL = Counter(
    "new_pixiv_metrics_scrape_errors_total",
    "Total /metrics scrape errors while querying backing dependencies.",
)

METRICS_LAST_SCRAPE_SUCCESS = Gauge(
    "new_pixiv_metrics_last_scrape_success",
    "Last /metrics scrape success (1=ok, 0=error).",
)

IMAGE_DELIVERY_PATHS: tuple[str, ...] = (
    "edge_redirect",
    "edge_unavailable",
    "local_stream",
    "local_i_redirect",
)


def _init_labelsets() -> None:
    for result in RANDOM_RESULTS:
        RANDOM_REQUESTS_TOTAL.labels(result=result).inc(0)
    RANDOM_NO_MATCH_TOTAL.inc(0)
    RANDOM_OPPORTUNISTIC_HYDRATE_ENQUEUED_TOTAL.inc(0)
    for status in (
        "ok",
        "unavailable",
        "not_ok",
        "no_match",
        "empty_index",
        "bad_item",
        "bad_id",
        "db_miss",
        "fallback",
        "skipped_traffic",
    ):
        RANDOM_ENGINE_PICK_TOTAL.labels(status=status).inc(0)
        RANDOM_ENGINE_PICK_LATENCY_SECONDS.labels(status=status).observe(0.0)
    for path in IMAGE_DELIVERY_PATHS:
        IMAGE_DELIVERY_TOTAL.labels(path=path).inc(0)
    for result in ("allowed", "limited"):
        for backend in ("memory", "redis"):
            API_KEY_RATE_LIMIT_TOTAL.labels(result=result, backend=backend).inc(0)
    UPSTREAM_STREAM_ERRORS_TOTAL.inc(0)
    JOBS_CLAIM_TOTAL.inc(0)
    JOBS_FAILED_TOTAL.inc(0)
    TOKEN_REFRESH_FAIL_TOTAL.inc(0)
    for status in JOB_STATUSES:
        JOBS_STATUS_COUNT.labels(status=status).set(0)
    for state in PROXY_STATES:
        PROXY_ENDPOINTS_STATE_COUNT.labels(state=state).set(0)
    METRICS_LAST_SCRAPE_SUCCESS.set(1)


_init_labelsets()


def observe_random_result(*, result: str, duration_s: float | None) -> None:
    result = (result or "").strip()
    if result not in RANDOM_RESULTS:
        result = "error"
    RANDOM_REQUESTS_TOTAL.labels(result=result).inc()
    if result == "no_match":
        RANDOM_NO_MATCH_TOTAL.inc()
    if duration_s is not None and duration_s >= 0:
        RANDOM_LATENCY_SECONDS.observe(duration_s)


def observe_image_delivery(*, path: str) -> None:
    """Count one public image delivery by cascade path (best-effort; never raises)."""
    label = (path or "").strip()
    if label not in IMAGE_DELIVERY_PATHS:
        return
    try:
        IMAGE_DELIVERY_TOTAL.labels(path=label).inc()
    except Exception:
        pass


def observe_random_engine_pick(*, status: str, duration_s: float | None = None) -> None:
    """Record dual-run engine attempt outcome (+ optional /v1/pick RTT histogram)."""
    label = (status or "fallback").strip() or "fallback"
    if len(label) > 64:
        label = label[:64]
    try:
        RANDOM_ENGINE_PICK_TOTAL.labels(status=label).inc()
    except Exception:
        pass
    if duration_s is None:
        return
    try:
        d = float(duration_s)
    except Exception:
        return
    if d < 0:
        return
    try:
        RANDOM_ENGINE_PICK_LATENCY_SECONDS.labels(status=label).observe(d)
    except Exception:
        pass


def observe_api_key_rate_limit(*, result: str, backend: str) -> None:
    """Count one public API key rate-limit decision (best-effort; never raises)."""
    result_label = (result or "allowed").strip() or "allowed"
    if result_label not in {"allowed", "limited"}:
        result_label = "allowed"
    backend_label = (backend or "memory").strip().lower() or "memory"
    if backend_label not in {"memory", "redis"}:
        backend_label = "memory"
    try:
        API_KEY_RATE_LIMIT_TOTAL.labels(result=result_label, backend=backend_label).inc()
    except Exception:
        pass


def set_jobs_status_counts(counts: dict[str, int]) -> None:
    for status in JOB_STATUSES:
        JOBS_STATUS_COUNT.labels(status=status).set(float(int(counts.get(status, 0) or 0)))


def set_proxy_state_counts(counts: dict[str, int]) -> None:
    for state in PROXY_STATES:
        PROXY_ENDPOINTS_STATE_COUNT.labels(state=state).set(float(int(counts.get(state, 0) or 0)))


def ensure_known_keys(keys: Iterable[str], counts: dict[str, int]) -> dict[str, int]:
    out = dict(counts)
    for k in keys:
        out.setdefault(k, 0)
    return out
