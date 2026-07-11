from __future__ import annotations

import asyncio

from app.core.runtime_config_cache import RuntimeConfigCache
from app.core.runtime_settings import RuntimeConfig


def test_runtime_config_cache_hits_within_ttl(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_load(engine):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return RuntimeConfig.defaults()

    monkeypatch.setattr("app.core.runtime_config_cache.load_runtime_config", fake_load)

    cache = RuntimeConfigCache(ttl_s=5.0)

    async def _run() -> None:
        engine = object()
        a = await cache.get(engine)  # type: ignore[arg-type]
        b = await cache.get(engine)  # type: ignore[arg-type]
        assert a is b
        assert calls["n"] == 1
        cache.invalidate()
        c = await cache.get(engine)  # type: ignore[arg-type]
        assert calls["n"] == 2
        assert c is not None

    asyncio.run(_run())
