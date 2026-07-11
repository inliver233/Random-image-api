from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from app.db.models.proxy_endpoints import ProxyEndpoint

# Shared defaults for probe-driven blacklist (hydrate uses its own env TTL + immediate mode).
PROBE_BLACKLIST_AFTER_FAILURES = 3
PROBE_BLACKLIST_TTL_S = 30 * 60


def proxy_endpoint_ok_values(*, now_iso: str, latency_ms: float | None) -> dict[str, Any]:
    """Column values when a proxy endpoint succeeds (probe or live traffic)."""
    return {
        "last_latency_ms": float(latency_ms) if latency_ms is not None else None,
        "last_ok_at": str(now_iso),
        "success_count": ProxyEndpoint.success_count + 1,
        "last_error": None,
        "blacklisted_until": None,
        "updated_at": str(now_iso),
    }


def proxy_endpoint_fail_values_threshold(
    *,
    now_iso: str,
    latency_ms: float | None,
    error_message: str,
    blacklist_until_iso: str,
    after_failures: int = PROBE_BLACKLIST_AFTER_FAILURES,
) -> dict[str, Any]:
    """
    Probe-style fail: blacklist only after N consecutive failures.
    Preserves existing blacklisted_until when threshold not yet reached.
    """
    threshold = max(1, int(after_failures))
    blacklist_expr = sa.case(
        (
            (ProxyEndpoint.failure_count + 1) >= int(threshold),
            str(blacklist_until_iso),
        ),
        else_=ProxyEndpoint.blacklisted_until,
    )
    return {
        "last_latency_ms": float(latency_ms) if latency_ms is not None else None,
        "last_fail_at": str(now_iso),
        "failure_count": ProxyEndpoint.failure_count + 1,
        "blacklisted_until": blacklist_expr,
        "last_error": str(error_message),
        "updated_at": str(now_iso),
    }


def proxy_endpoint_fail_values_immediate(
    *,
    now_iso: str,
    latency_ms: float | None,
    error_message: str,
    blacklist_until_iso: str | None,
) -> dict[str, Any]:
    """
    Hydrate-style fail: apply blacklist TTL immediately (keep longer existing window).
    When blacklist_until_iso is None, leave blacklisted_until unchanged.
    """
    if blacklist_until_iso:
        blacklist_expr = sa.case(
            (
                sa.and_(
                    ProxyEndpoint.blacklisted_until.isnot(None),
                    ProxyEndpoint.blacklisted_until > str(blacklist_until_iso),
                ),
                ProxyEndpoint.blacklisted_until,
            ),
            else_=str(blacklist_until_iso),
        )
    else:
        blacklist_expr = ProxyEndpoint.blacklisted_until

    return {
        "last_latency_ms": float(latency_ms) if latency_ms is not None else None,
        "last_fail_at": str(now_iso),
        "failure_count": ProxyEndpoint.failure_count + 1,
        "blacklisted_until": blacklist_expr,
        "last_error": str(error_message),
        "updated_at": str(now_iso),
    }
