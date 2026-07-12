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
        assert limiter.active_backend == "redis"
        # First allow creates local fallback bucket; second is limited.
        assert await limiter.allow(42) is True
        assert await limiter.allow(42) is False
        # Runtime honesty: status consumers must see memory while fail-open.
        assert limiter.active_backend == "memory"
        assert limiter.backend == "redis"  # configured label unchanged
        await limiter.aclose()

    asyncio.run(_run())


def test_redis_limiter_budgeted_when_eval_hangs() -> None:
    """Hung Redis EVAL must not stall the public auth path past the hard timeout."""
    import time as _time

    from app.core import api_keys as api_keys_mod

    class _HungClient:
        async def eval(self, *_a, **_k):  # type: ignore[no-untyped-def]
            await asyncio.sleep(5.0)
            return 1

        async def aclose(self) -> None:
            return None

    async def _run() -> None:
        limiter = RedisApiKeyRateLimiter(
            rpm=60,
            burst=1,
            redis_url="redis://127.0.0.1:6379/0",
        )
        # Inject a connected-looking client that never answers EVAL.
        limiter._client = _HungClient()
        t0 = _time.monotonic()
        assert await limiter.allow(7) is True  # fail-open to memory (first token)
        elapsed = _time.monotonic() - t0
        # Hard budget is ~0.15s; leave headroom for scheduling.
        assert elapsed < 1.0, f"allow hung too long: {elapsed:.3f}s"
        assert elapsed >= float(api_keys_mod._REDIS_RL_CALL_TIMEOUT_S) * 0.5
        assert limiter.active_backend == "memory"
        # Second call uses memory fallback bucket (burst=1) → limited.
        assert await limiter.allow(7) is False
        await limiter.aclose()

    asyncio.run(_run())
