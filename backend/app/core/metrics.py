from __future__ import annotations

from collections.abc import Iterable
from typing import Any

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
#   local_stream_direct | local_stream_residential | local_stream_mirror
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

# Pixiv API egress plan outcomes (CF multi-base then residential).
# via: cf | residential — result: ok | error (per attempt, not per logical op).
PIXIV_API_EGRESS_TOTAL = Counter(
    "new_pixiv_pixiv_api_egress_total",
    "Pixiv API egress attempts by via (cf|residential) and result (ok|error).",
    ["via", "result"],
)

# R2 prewarm best-effort enqueue outcomes (config skip + chunk HTTP).
R2_PREWARM_TOTAL = Counter(
    "new_pixiv_r2_prewarm_total",
    "R2 prewarm enqueue outcomes by result (ok|failed_chunk|skipped_*|error).",
    ["result"],
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

# Process dual-run circuit (local snapshot on scrape; no outbound engine probe).
# state label: closed | open | half_open — only one is 1 at a time.
RANDOM_ENGINE_CIRCUIT_STATE = Gauge(
    "new_pixiv_random_engine_circuit_state",
    "BFF dual-run circuit state (1=active state, 0=other).",
    ["state"],
)

RANDOM_ENGINE_CIRCUIT_OPEN_REMAINING_SECONDS = Gauge(
    "new_pixiv_random_engine_circuit_open_remaining_seconds",
    "Seconds remaining while dual-run circuit is open (0 when closed/half_open).",
)

RANDOM_ENGINE_CIRCUIT_CONSECUTIVE_FAILURES = Gauge(
    "new_pixiv_random_engine_circuit_consecutive_failures",
    "Consecutive hard dual-run failures toward open threshold.",
)

# Local modular readiness (config snapshot on scrape; no secrets / no outbound probes).
# flag values are 1=true / 0=false. base_url_count is a separate gauge for edge modules.
MODULE_READINESS = Gauge(
    "new_pixiv_module_readiness",
    "Local modular config readiness flags (1=true, 0=false); no secrets or outbound probes.",
    ["module", "flag"],
)

MODULE_BASE_URL_COUNT = Gauge(
    "new_pixiv_module_base_url_count",
    "Configured base URL count for edge modules (local config only).",
    ["module"],
)

CIRCUIT_STATES: tuple[str, ...] = ("closed", "open", "half_open")

# module → readiness flag labels published on scrape (stable for dashboards).
MODULE_READINESS_FLAGS: dict[str, tuple[str, ...]] = {
    "image_edge": ("enabled", "ready", "has_secret"),
    "cf_api_proxy": ("enabled", "ready", "has_secret"),
    "r2_prewarm": ("enabled", "ready", "url_configured", "secret_configured"),
    "api_key_rate_limit": ("required", "using_memory_fallback", "redis_url_configured"),
    "job_queue": ("implemented",),
    "recent_dedup": ("using_memory_fallback",),
}

MODULE_BASE_URL_MODULES: tuple[str, ...] = ("image_edge", "cf_api_proxy")

IMAGE_DELIVERY_PATHS: tuple[str, ...] = (
    "edge_redirect",
    "edge_unavailable",
    "local_stream",
    "local_stream_direct",
    "local_stream_residential",
    "local_stream_mirror",
    "local_i_redirect",
)

PIXIV_API_EGRESS_VIAS: tuple[str, ...] = ("cf", "residential")
PIXIV_API_EGRESS_RESULTS: tuple[str, ...] = ("ok", "error")

R2_PREWARM_RESULTS: tuple[str, ...] = (
    "ok",
    "failed_chunk",
    "skipped_disabled",
    "skipped_no_secret",
    "skipped_no_paths",
    "error",
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
        "skipped_sticky",
        "skipped_circuit",
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
    for via in PIXIV_API_EGRESS_VIAS:
        for result in PIXIV_API_EGRESS_RESULTS:
            PIXIV_API_EGRESS_TOTAL.labels(via=via, result=result).inc(0)
    for result in R2_PREWARM_RESULTS:
        R2_PREWARM_TOTAL.labels(result=result).inc(0)
    for status in JOB_STATUSES:
        JOBS_STATUS_COUNT.labels(status=status).set(0)
    for state in PROXY_STATES:
        PROXY_ENDPOINTS_STATE_COUNT.labels(state=state).set(0)
    for state in CIRCUIT_STATES:
        RANDOM_ENGINE_CIRCUIT_STATE.labels(state=state).set(1.0 if state == "closed" else 0.0)
    RANDOM_ENGINE_CIRCUIT_OPEN_REMAINING_SECONDS.set(0.0)
    RANDOM_ENGINE_CIRCUIT_CONSECUTIVE_FAILURES.set(0.0)
    for module, flags in MODULE_READINESS_FLAGS.items():
        for flag in flags:
            MODULE_READINESS.labels(module=module, flag=flag).set(0.0)
    for module in MODULE_BASE_URL_MODULES:
        MODULE_BASE_URL_COUNT.labels(module=module).set(0.0)
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


def observe_pixiv_api_egress(*, via: str, result: str) -> None:
    """Count one CF/residential Pixiv API egress attempt (best-effort; never raises)."""
    via_label = (via or "").strip().lower() or "residential"
    if via_label not in PIXIV_API_EGRESS_VIAS:
        via_label = "residential"
    result_label = (result or "").strip().lower() or "error"
    if result_label not in PIXIV_API_EGRESS_RESULTS:
        result_label = "error"
    try:
        PIXIV_API_EGRESS_TOTAL.labels(via=via_label, result=result_label).inc()
    except Exception:
        pass


def observe_r2_prewarm(*, result: str) -> None:
    """Count one R2 prewarm enqueue outcome (best-effort; never raises)."""
    label = (result or "").strip().lower() or "error"
    if label not in R2_PREWARM_RESULTS:
        label = "error"
    try:
        R2_PREWARM_TOTAL.labels(result=label).inc()
    except Exception:
        pass


def set_random_engine_circuit_snapshot(snapshot: dict[str, Any] | None) -> None:
    """Publish process dual-run circuit gauges (best-effort; never raises)."""
    try:
        snap = snapshot if isinstance(snapshot, dict) else {}
        state = str(snap.get("state") or "closed").strip().lower() or "closed"
        if state not in CIRCUIT_STATES:
            state = "closed"
        for label in CIRCUIT_STATES:
            RANDOM_ENGINE_CIRCUIT_STATE.labels(state=label).set(1.0 if label == state else 0.0)
        try:
            remaining = float(snap.get("open_remaining_s") or 0.0)
        except Exception:
            remaining = 0.0
        if remaining < 0:
            remaining = 0.0
        RANDOM_ENGINE_CIRCUIT_OPEN_REMAINING_SECONDS.set(remaining)
        try:
            failures = int(snap.get("consecutive_failures") or 0)
        except Exception:
            failures = 0
        if failures < 0:
            failures = 0
        RANDOM_ENGINE_CIRCUIT_CONSECUTIVE_FAILURES.set(float(failures))
    except Exception:
        pass


def _truthy_flag(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return 1.0 if float(value) != 0.0 else 0.0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return 1.0
    return 0.0


def set_modular_readiness_snapshot(snapshot: dict[str, Any] | None) -> None:
    """Publish local modular readiness gauges (best-effort; never raises).

    Expected shape (subset ok)::

        {
          "image_edge": {
              "enabled": bool,
              "ready": bool,
              "has_secret": bool,
              "base_url_count": int,
          },
          "cf_api_proxy": {
              "enabled": bool,
              "ready": bool,
              "has_secret": bool,
              "base_url_count": int,
          },
          "r2_prewarm": {
              "enabled": bool,
              "ready": bool,
              "url_configured": bool,
              "secret_configured": bool,
          },
          "api_key_rate_limit": {
              "required": bool,
              "using_memory_fallback": bool,
              "redis_url_configured": bool,
          },
          "job_queue": {"implemented": bool},
          "recent_dedup": {"using_memory_fallback": bool},
        }
    """
    try:
        snap = snapshot if isinstance(snapshot, dict) else {}
        for module, flags in MODULE_READINESS_FLAGS.items():
            mod = snap.get(module)
            mod_dict = mod if isinstance(mod, dict) else {}
            for flag in flags:
                MODULE_READINESS.labels(module=module, flag=flag).set(
                    _truthy_flag(mod_dict.get(flag))
                )
        for module in MODULE_BASE_URL_MODULES:
            mod = snap.get(module)
            mod_dict = mod if isinstance(mod, dict) else {}
            try:
                count = int(mod_dict.get("base_url_count") or 0)
            except Exception:
                count = 0
            if count < 0:
                count = 0
            MODULE_BASE_URL_COUNT.labels(module=module).set(float(count))
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
