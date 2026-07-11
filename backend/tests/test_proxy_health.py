from __future__ import annotations

from app.core.proxy_health import (
    PROBE_BLACKLIST_AFTER_FAILURES,
    PROBE_BLACKLIST_TTL_S,
    proxy_endpoint_fail_values_immediate,
    proxy_endpoint_fail_values_threshold,
    proxy_endpoint_ok_values,
)


def test_proxy_health_ok_values_shape() -> None:
    vals = proxy_endpoint_ok_values(now_iso="2026-01-01T00:00:00.000Z", latency_ms=12.5)
    assert vals["last_ok_at"] == "2026-01-01T00:00:00.000Z"
    assert vals["last_latency_ms"] == 12.5
    assert vals["last_error"] is None
    assert vals["blacklisted_until"] is None
    assert vals["updated_at"] == "2026-01-01T00:00:00.000Z"


def test_proxy_health_threshold_and_immediate_include_fail_fields() -> None:
    assert PROBE_BLACKLIST_AFTER_FAILURES == 3
    assert PROBE_BLACKLIST_TTL_S == 30 * 60

    thr = proxy_endpoint_fail_values_threshold(
        now_iso="t1",
        latency_ms=1.0,
        error_message="boom",
        blacklist_until_iso="t2",
        after_failures=3,
    )
    assert thr["last_fail_at"] == "t1"
    assert thr["last_error"] == "boom"
    assert thr["last_latency_ms"] == 1.0

    imm = proxy_endpoint_fail_values_immediate(
        now_iso="t3",
        latency_ms=None,
        error_message="x",
        blacklist_until_iso=None,
    )
    assert imm["last_fail_at"] == "t3"
    assert imm["last_error"] == "x"
    assert imm["last_latency_ms"] is None
