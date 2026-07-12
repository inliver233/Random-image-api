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
    job_queue_backend: str = "sqlite",
    recent_dedup_backend: str = "memory",
    redis_url: str = "",
) -> Any:
    db_path = tmp_path / "status_modular_ports.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "false")
    monkeypatch.setenv("JOB_QUEUE_BACKEND", job_queue_backend)
    monkeypatch.setenv("RECENT_DEDUP_BACKEND", recent_dedup_backend)
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


def test_status_json_includes_job_queue_and_recent_dedup_defaults(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        data = resp.json().get("data") or {}
        jq = data.get("job_queue") or {}
        assert jq.get("backend") == "sqlite"
        assert jq.get("requested") == "sqlite"
        assert jq.get("implemented") is True
        assert "using_sqlite_fallback" not in jq

        rd = data.get("recent_dedup") or {}
        assert rd.get("backend") == "memory"
        assert rd.get("requested") == "memory"
        assert rd.get("redis_url_configured") is False
        assert rd.get("using_memory_fallback") is False


def test_status_json_job_queue_memory_alias(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(tmp_path, monkeypatch, job_queue_backend="memory")

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        jq = (resp.json().get("data") or {}).get("job_queue") or {}
        assert jq.get("requested") == "memory"
        assert jq.get("backend") == "memory"
        assert jq.get("implemented") is True


def test_status_json_recent_dedup_redis_without_url_fallback(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        recent_dedup_backend="redis",
        redis_url="",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        rd = (resp.json().get("data") or {}).get("recent_dedup") or {}
        assert rd.get("requested") == "redis"
        assert rd.get("backend") == "memory"
        assert rd.get("redis_url_configured") is False
        assert rd.get("using_memory_fallback") is True
        assert "redis://" not in resp.text.lower()


def test_status_html_shows_modular_port_chips(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "job-queue: sqlite · implemented" in text
        assert "recent-dedup: memory" in text
        assert "job_queue" in text
        assert "recent_dedup" in text


def test_status_html_recent_dedup_chip_no_redis_url(tmp_path: Path, monkeypatch) -> None:
    """HTML chip surfaces no-redis-url when redis requested without REDIS_URL."""
    app = _seed_app(
        tmp_path,
        monkeypatch,
        recent_dedup_backend="redis",
        redis_url="",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "recent-dedup: redis→memory · no-redis-url" in text
        assert "redis://" not in text
