from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.main import create_app


def _seed_app(tmp_path: Path, monkeypatch) -> Any:
    db_path = tmp_path / "status_port_dialects.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "false")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())
    return app


def test_status_json_includes_port_dialect_labels(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        data = resp.json().get("data") or {}
        assert (data.get("catalog") or {}).get("backend") == "sqlite"
        assert (data.get("tags") or {}).get("backend") == "sqlite"
        assert (data.get("random_service") or {}).get("backend") == "default"
        assert (data.get("random_pick") or {}).get("backend") == "sqlite"


def test_status_html_shows_ports_chip(tmp_path: Path, monkeypatch) -> None:
    app = _seed_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "ports: cat sqlite" in text
        assert "tags sqlite" in text
        assert "svc default" in text
        assert "pick sqlite" in text
