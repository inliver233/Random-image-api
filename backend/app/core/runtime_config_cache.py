from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.runtime_settings import RuntimeConfig, load_runtime_config

_DEFAULT_TTL_S = 2.0


class RuntimeConfigCache:
    """Process-local TTL cache for RuntimeConfig.

    Hot paths (/random, /i/*) previously paid a full runtime_settings table read
    on every request. A short TTL keeps admin updates nearly instant while
    collapsing DB load under concurrency.
    """

    def __init__(self, *, ttl_s: float = _DEFAULT_TTL_S) -> None:
        self._ttl_s = max(0.2, float(ttl_s))
        self._lock = asyncio.Lock()
        self._value: RuntimeConfig | None = None
        self._loaded_at: float = 0.0
        self._engine_id: int | None = None

    def invalidate(self) -> None:
        self._value = None
        self._loaded_at = 0.0

    async def get(self, engine: AsyncEngine, *, force: bool = False) -> RuntimeConfig:
        now = time.monotonic()
        engine_id = id(engine)
        cached = self._value
        if (
            not force
            and cached is not None
            and self._engine_id == engine_id
            and (now - self._loaded_at) < self._ttl_s
        ):
            return cached

        async with self._lock:
            now = time.monotonic()
            cached = self._value
            if (
                not force
                and cached is not None
                and self._engine_id == engine_id
                and (now - self._loaded_at) < self._ttl_s
            ):
                return cached
            value = await load_runtime_config(engine)
            self._value = value
            self._loaded_at = time.monotonic()
            self._engine_id = engine_id
            return value


_GLOBAL_CACHE = RuntimeConfigCache()


def get_runtime_config_cache() -> RuntimeConfigCache:
    return _GLOBAL_CACHE


async def get_cached_runtime_config(engine: AsyncEngine, *, force: bool = False) -> RuntimeConfig:
    return await _GLOBAL_CACHE.get(engine, force=force)


async def resolve_runtime_for_request(request: Any, engine: AsyncEngine, *, force: bool = False) -> RuntimeConfig:
    """Prefer app.state.runtime_config_cache; fall back to process-global TTL cache."""
    cache = getattr(getattr(request, "app", None), "state", None)
    cache = getattr(cache, "runtime_config_cache", None) if cache is not None else None
    if cache is not None:
        return await cache.get(engine, force=force)
    return await get_cached_runtime_config(engine, force=force)


def invalidate_runtime_config_cache() -> None:
    _GLOBAL_CACHE.invalidate()
