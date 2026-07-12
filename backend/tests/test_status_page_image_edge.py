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
    enabled: str = "false",
    secret: str = "",
    bases: str = "",
) -> Any:
    db_path = tmp_path / "status_image_edge.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "false")
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", enabled)
    monkeypatch.setenv("IMAGE_EDGE_SECRET", secret)
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", bases)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())
    return app


def test_status_json_includes_image_edge_not_ready(tmp_path: Path, monkeypatch) -> None:
    """Public /status.json exposes Image Edge config readiness (no secrets)."""
    app = _seed_app(tmp_path, monkeypatch, enabled="false", secret="", bases="")

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        edge = (body.get("data") or {}).get("image_edge") or {}
        assert edge.get("enabled_flag") is False
        assert edge.get("ready") is False
        assert edge.get("base_url_count") == 0
        # Never leak secrets on public status.
        assert "secret" not in edge
        assert "IMAGE_EDGE_SECRET" not in str(edge)


def test_status_json_image_edge_ready(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        secret="edge-secret-test",
        bases="https://edge-a.example.com,https://edge-b.example.com",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        edge = (resp.json().get("data") or {}).get("image_edge") or {}
        assert edge.get("enabled_flag") is True
        assert edge.get("ready") is True
        assert edge.get("base_url_count") == 2


def test_status_html_shows_image_edge_chip(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        secret="edge-secret-test",
        bases="https://edge-a.example.com",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "image-edge: ready" in text
        assert "bases 1" in text
        assert "image_edge" in text  # DATA payload includes the field
