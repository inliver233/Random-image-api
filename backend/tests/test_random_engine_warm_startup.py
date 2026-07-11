from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config import load_settings
from app.core.random_engine_sync import maybe_warm_engine_snapshot_on_startup
from app.db.engine import create_engine


def test_warm_engine_snapshot_noop_without_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RANDOM_ENGINE_URL", raising=False)
    monkeypatch.delenv("RANDOM_ENGINE_ENABLED", raising=False)
    settings = load_settings()
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "warm.db").as_posix())

    async def _run() -> None:
        out = await maybe_warm_engine_snapshot_on_startup(engine, settings=settings)
        assert out is None
        await engine.dispose()

    asyncio.run(_run())


def test_warm_engine_snapshot_calls_push_when_url_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RANDOM_ENGINE_URL", "http://127.0.0.1:9")
    settings = load_settings()
    engine = create_engine("sqlite+aiosqlite:///" + (tmp_path / "warm2.db").as_posix())
    called: dict[str, Any] = {}

    async def _fake_push(*args: Any, **kwargs: Any) -> dict[str, Any]:
        called["kwargs"] = kwargs
        return {"ok": True, "index_size": 0}

    monkeypatch.setattr("app.core.random_engine_sync.push_engine_snapshot", _fake_push)

    class _Client:
        async def aclose(self) -> None:
            return None

    async def _run() -> None:
        out = await maybe_warm_engine_snapshot_on_startup(
            engine,
            settings=settings,
            client=_Client(),
            timeout_s=1.0,
        )
        assert out == {"ok": True, "index_size": 0}
        assert called.get("kwargs", {}).get("base_url") == "http://127.0.0.1:9"
        await engine.dispose()

    asyncio.run(_run())
