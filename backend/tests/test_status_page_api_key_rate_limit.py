from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.main import create_app


def _seed_app(
    tmp_path: Path,
    monkeypatch,
    *,
    required: str = "false",
    backend: str = "memory",
    redis_url: str = "",
) -> Any:
    db_path = tmp_path / "status_api_key_rl.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", required)
    monkeypatch.setenv("PUBLIC_API_KEY_RATE_LIMIT_BACKEND", backend)
    if redis_url:
        monkeypatch.setenv("REDIS_URL", redis_url)
    else:
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.delenv("PUBLIC_API_KEY_REDIS_URL", raising=False)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())
    return app


def test_status_json_includes_api_key_rate_limit_defaults(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        rl = (resp.json().get("data") or {}).get("api_key_rate_limit") or {}
        assert rl.get("required") is False
        assert rl.get("backend") == "memory"
        assert rl.get("requested") == "memory"
        assert rl.get("redis_url_configured") is False
        assert rl.get("using_memory_fallback") is False
        # Public surface must never leak Redis URL / secrets.
        assert "redis://" not in resp.text.lower()
        assert "url" not in rl or "redis_url" not in rl


def test_status_json_api_key_rate_limit_redis_requested(tmp_path: Path, monkeypatch) -> None:
    """Redis backend configured reports requested=redis; active may still be redis until fail-open."""
    app = _seed_app(
        tmp_path,
        monkeypatch,
        required="true",
        backend="redis",
        redis_url="redis://super-secret-host:6379/9",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        rl = (resp.json().get("data") or {}).get("api_key_rate_limit") or {}
        assert rl.get("required") is True
        assert rl.get("requested") == "redis"
        assert rl.get("redis_url_configured") is True
        assert rl.get("backend") in {"memory", "redis"}
        if rl.get("backend") == "memory":
            assert rl.get("using_memory_fallback") is True
        else:
            assert rl.get("using_memory_fallback") is False
        assert "super-secret-host" not in resp.text
        assert "redis://" not in resp.text


def test_status_json_api_key_rate_limit_redis_without_url_fallback(tmp_path: Path, monkeypatch) -> None:
    """backend=redis without URL → active memory + using_memory_fallback."""
    app = _seed_app(
        tmp_path,
        monkeypatch,
        required="true",
        backend="redis",
        redis_url="",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        rl = (resp.json().get("data") or {}).get("api_key_rate_limit") or {}
        assert rl.get("required") is True
        assert rl.get("requested") == "redis"
        assert rl.get("redis_url_configured") is False
        assert rl.get("backend") == "memory"
        assert rl.get("using_memory_fallback") is True


def test_status_html_shows_api_key_rl_chip(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        required="true",
        backend="memory",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "api-key-rl: required" in text
        assert "api_key_rate_limit" in text


def test_status_html_api_key_rl_chip_no_redis_url(tmp_path: Path, monkeypatch) -> None:
    """HTML chip surfaces no-redis-url when redis requested without REDIS_URL."""
    app = _seed_app(
        tmp_path,
        monkeypatch,
        required="true",
        backend="redis",
        redis_url="",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "api-key-rl: required · redis→memory · no-redis-url" in text
        assert "redis://" not in text
