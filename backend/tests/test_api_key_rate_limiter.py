from __future__ import annotations

import asyncio

from app.core.api_keys import (
    ApiKeyRateLimiter,
    RedisApiKeyRateLimiter,
    build_api_key_rate_limiter,
    normalize_rate_limit_backend,
)


def test_normalize_rate_limit_backend() -> None:
    assert normalize_rate_limit_backend("memory") == "memory"
    assert normalize_rate_limit_backend("REDIS") == "redis"
    assert normalize_rate_limit_backend("weird") == "memory"
    assert normalize_rate_limit_backend(None) == "memory"


def test_build_defaults_to_memory() -> None:
    limiter = build_api_key_rate_limiter(rpm=60, burst=10, backend="memory", redis_url="")
    assert isinstance(limiter, ApiKeyRateLimiter)
    assert limiter.backend == "memory"


def test_build_redis_without_url_falls_back_to_memory() -> None:
    limiter = build_api_key_rate_limiter(rpm=60, burst=10, backend="redis", redis_url="")
    assert isinstance(limiter, ApiKeyRateLimiter)
    assert limiter.backend == "memory"


def test_build_redis_with_url_selects_redis_backend() -> None:
    limiter = build_api_key_rate_limiter(
        rpm=60,
        burst=10,
        backend="redis",
        redis_url="redis://127.0.0.1:6379/0",
    )
    assert isinstance(limiter, RedisApiKeyRateLimiter)
    assert limiter.backend == "redis"


def test_memory_token_bucket_limits() -> None:
    async def _run() -> None:
        limiter = ApiKeyRateLimiter(rpm=60, burst=1)
        assert await limiter.allow(1) is True
        assert await limiter.allow(1) is False
        # invalid key id always denied
        assert await limiter.allow(0) is False
        # rpm<=0 disables limiting
        open_limiter = ApiKeyRateLimiter(rpm=0, burst=0)
        assert await open_limiter.allow(1) is True
        assert await open_limiter.allow(1) is True

    asyncio.run(_run())


def test_redis_limiter_falls_back_when_client_unavailable() -> None:
    """Without a live Redis, EVAL path fails open to process-local memory bucket."""

    async def _run() -> None:
        limiter = RedisApiKeyRateLimiter(
            rpm=60,
            burst=1,
            redis_url="redis://127.0.0.1:1/0",  # nothing listening
        )
        # First allow creates local fallback bucket; second is limited.
        assert await limiter.allow(42) is True
        assert await limiter.allow(42) is False
        await limiter.aclose()

    asyncio.run(_run())
