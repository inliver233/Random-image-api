from __future__ import annotations

import httpx

from app.core.failover import (
    OutboundErrorKind,
    classify_httpx_exception,
    classify_pixiv_rate_limit,
    pixiv_rate_limit_backoff_seconds,
    proxy_override_ttl_seconds,
    should_backoff_token,
    should_override_proxy,
)


def test_classify_httpx_proxy_error_proxy_auth() -> None:
    exc = httpx.ProxyError("407 Proxy Authentication Required")
    assert classify_httpx_exception(exc) == OutboundErrorKind.PROXY_AUTH


def test_classify_httpx_proxy_error_proxy_connect() -> None:
    exc = httpx.ProxyError("Proxy connect failed")
    assert classify_httpx_exception(exc) == OutboundErrorKind.PROXY_CONNECT


def test_classify_pixiv_rate_limit_403_body_match() -> None:
    kind = classify_pixiv_rate_limit(status_code=403, body_text="Rate Limit")
    assert kind == OutboundErrorKind.PIXIV_RATE_LIMIT


def test_failover_decisions() -> None:
    assert should_override_proxy(OutboundErrorKind.PROXY_CONNECT) is True
    assert should_override_proxy(OutboundErrorKind.PROXY_AUTH) is True
    assert should_override_proxy(OutboundErrorKind.PIXIV_RATE_LIMIT) is False

    assert should_backoff_token(OutboundErrorKind.PIXIV_RATE_LIMIT) is True
    assert should_backoff_token(OutboundErrorKind.PROXY_CONNECT) is False


def test_proxy_override_ttl_schedule() -> None:
    assert proxy_override_ttl_seconds(attempt=0) == 0
    assert proxy_override_ttl_seconds(attempt=1) == 20 * 60
    assert proxy_override_ttl_seconds(attempt=2) == 60 * 60


def test_pixiv_rate_limit_backoff_schedule() -> None:
    assert pixiv_rate_limit_backoff_seconds(attempt=0) == 0
    assert pixiv_rate_limit_backoff_seconds(attempt=1) == 60
    assert pixiv_rate_limit_backoff_seconds(attempt=2) == 5 * 60

