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
    url: str = "",
    secret: str = "",
    edge_secret: str = "",
) -> Any:
    db_path = tmp_path / "status_r2_prewarm.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "false")
    monkeypatch.setenv("R2_PREWARM_ENABLED", enabled)
    monkeypatch.setenv("R2_PREWARM_URL", url)
    monkeypatch.setenv("R2_PREWARM_SECRET", secret)
    monkeypatch.setenv("IMAGE_EDGE_SECRET", edge_secret)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())
    return app


def test_status_json_includes_r2_prewarm_off(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        r2 = (resp.json().get("data") or {}).get("r2_prewarm") or {}
        assert r2.get("enabled_flag") is False
        assert r2.get("ready") is False
        assert r2.get("url_configured") is False
        # Public surface omits secret_configured / secrets.
        assert "secret" not in r2
        assert "secret_configured" not in r2


def test_status_json_r2_prewarm_ready_with_edge_secret_fallback(tmp_path: Path, monkeypatch) -> None:
    """ready requires flag+url+secret; IMAGE_EDGE_SECRET is an accepted secret source."""
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        url="https://img.example.com",
        secret="",
        edge_secret="edge-secret-for-prewarm",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        r2 = (resp.json().get("data") or {}).get("r2_prewarm") or {}
        assert r2.get("enabled_flag") is True
        assert r2.get("url_configured") is True
        assert r2.get("ready") is True


def test_status_json_r2_prewarm_flag_url_without_secret_not_ready(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        url="https://img.example.com",
        secret="",
        edge_secret="",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        r2 = (resp.json().get("data") or {}).get("r2_prewarm") or {}
        assert r2.get("enabled_flag") is True
        assert r2.get("url_configured") is True
        assert r2.get("ready") is False


def test_status_html_r2_chip_no_secret_when_flag_url_not_ready(tmp_path: Path, monkeypatch) -> None:
    """HTML chip surfaces no-secret when flag+url but not ready (without leaking secrets)."""
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        url="https://img.example.com",
        secret="",
        edge_secret="",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "r2-prewarm: not ready · flag+url · no-secret" in text
        assert "prewarm-secret" not in text


def test_status_html_shows_r2_prewarm_chip(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        url="https://img.example.com",
        secret="prewarm-secret",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "r2-prewarm: ready" in text
        assert "r2_prewarm" in text
