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
    db_path = tmp_path / "status_cf_api_proxy.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "false")
    monkeypatch.setenv("CF_API_PROXY_ENABLED", enabled)
    monkeypatch.setenv("CF_API_PROXY_SECRET", secret)
    monkeypatch.setenv("CF_API_PROXY_BASE_URLS", bases)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())
    return app


def test_status_json_includes_cf_api_proxy_not_ready(tmp_path: Path, monkeypatch) -> None:
    """Public /status.json exposes CF API proxy readiness (no secrets)."""
    app = _seed_app(tmp_path, monkeypatch, enabled="false", secret="", bases="")

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        cf = (body.get("data") or {}).get("cf_api_proxy") or {}
        assert cf.get("enabled_flag") is False
        assert cf.get("ready") is False
        assert cf.get("base_url_count") == 0
        assert "secret" not in cf
        assert "CF_API_PROXY_SECRET" not in str(cf)


def test_status_json_cf_api_proxy_flag_on_missing_secret_not_ready(tmp_path: Path, monkeypatch) -> None:
    """Flag + bases without secret is not ready (fail-closed secret)."""
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        secret="",
        bases="https://api-proxy-a.example.com",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        cf = (resp.json().get("data") or {}).get("cf_api_proxy") or {}
        assert cf.get("enabled_flag") is True
        assert cf.get("ready") is False
        assert cf.get("base_url_count") == 1


def test_status_json_cf_api_proxy_ready(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        secret="proxy-secret-test",
        bases="https://api-proxy-a.example.com,https://api-proxy-b.example.com",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        cf = (resp.json().get("data") or {}).get("cf_api_proxy") or {}
        assert cf.get("enabled_flag") is True
        assert cf.get("ready") is True
        assert cf.get("base_url_count") == 2


def test_status_html_shows_cf_api_chip(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(
        tmp_path,
        monkeypatch,
        enabled="true",
        secret="proxy-secret-test",
        bases="https://api-proxy-a.example.com",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "cf-api: ready" in text
        assert "bases 1" in text
        assert "cf_api_proxy" in text
