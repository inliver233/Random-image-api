from __future__ import annotations

from enum import Enum

import httpx


class OutboundErrorKind(str, Enum):
    PROXY_CONNECT = "proxy_connect"
    PROXY_AUTH = "proxy_auth"
    PIXIV_RATE_LIMIT = "pixiv_rate_limit"


def classify_httpx_exception(exc: BaseException) -> OutboundErrorKind | None:
    if isinstance(exc, httpx.ProxyError):
        msg = str(exc).lower()
        if "407" in msg or "proxy authentication" in msg:
            return OutboundErrorKind.PROXY_AUTH
        return OutboundErrorKind.PROXY_CONNECT
    return None


def classify_pixiv_rate_limit(*, status_code: int, body_text: str | None) -> OutboundErrorKind | None:
    if int(status_code) != 403:
        return None
    text = (body_text or "").strip().lower()
    if "rate limit" in text:
        return OutboundErrorKind.PIXIV_RATE_LIMIT
    return None


def should_override_proxy(kind: OutboundErrorKind) -> bool:
    return kind in {OutboundErrorKind.PROXY_CONNECT, OutboundErrorKind.PROXY_AUTH}


def should_backoff_token(kind: OutboundErrorKind) -> bool:
    return kind == OutboundErrorKind.PIXIV_RATE_LIMIT


def proxy_override_ttl_seconds(*, attempt: int) -> int:
    attempt_i = int(attempt)
    if attempt_i <= 0:
        return 0

    schedule = {
        1: 20 * 60,
        2: 60 * 60,
        3: 6 * 60 * 60,
    }
    if attempt_i in schedule:
        return schedule[attempt_i]

    base = 6 * 60 * 60
    seconds = base * (2 ** (attempt_i - 3))
    return min(seconds, 24 * 60 * 60)


def pixiv_rate_limit_backoff_seconds(*, attempt: int) -> int:
    attempt_i = int(attempt)
    if attempt_i <= 0:
        return 0

    schedule = {
        1: 60,
        2: 5 * 60,
        3: 15 * 60,
        4: 60 * 60,
        5: 6 * 60 * 60,
    }
    if attempt_i in schedule:
        return schedule[attempt_i]

    base = 6 * 60 * 60
    seconds = base * (2 ** (attempt_i - 5))
    return min(seconds, 24 * 60 * 60)

