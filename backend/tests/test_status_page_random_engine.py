from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.core.random_engine_client import engine_circuit_record, reset_engine_circuit_for_tests
from app.db.models.base import Base
from app.main import create_app


def _seed_app(tmp_path: Path, monkeypatch, *, engine_url: str = "", enabled: str = "false") -> Any:
    db_path = tmp_path / "status_random_engine.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "false")
    monkeypatch.setenv("RANDOM_ENGINE_URL", engine_url)
    monkeypatch.setenv("RANDOM_ENGINE_ENABLED", enabled)
    monkeypatch.setenv("RANDOM_ENGINE_TRAFFIC_PERCENT", "40")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())
    return app


def test_status_json_includes_random_engine_circuit(tmp_path: Path, monkeypatch) -> None:
    """Public /status.json exposes local dual-run circuit (no outbound probe)."""
    reset_engine_circuit_for_tests()
    app = _seed_app(
        tmp_path,
        monkeypatch,
        engine_url="http://engine.test:8080",
        enabled="true",
    )

    with TestClient(app) as client:
        resp = client.get("/status.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        data = body.get("data") or {}
        eng = data.get("random_engine") or {}
        assert eng.get("url_configured") is True
        assert eng.get("enabled") is True
        assert eng.get("traffic_percent") == 40
        circuit = eng.get("circuit") or {}
        assert circuit.get("state") == "closed"
        assert "consecutive_failures" in circuit
        assert "failure_threshold" in circuit
        assert "open_s" in circuit
        assert "open_remaining_s" in circuit


def test_status_json_circuit_open_after_hard_failures(tmp_path: Path, monkeypatch) -> None:
    """Process circuit open is visible on /status.json (same snapshot as /healthz)."""
    reset_engine_circuit_for_tests()
    app = _seed_app(
        tmp_path,
        monkeypatch,
        engine_url="http://engine.test:8080",
        enabled="true",
    )

    # Trip the process circuit (threshold=5 hard failures).
    for _ in range(5):
        engine_circuit_record("unavailable")

    try:
        with TestClient(app) as client:
            resp = client.get("/status.json")
            assert resp.status_code == 200
            eng = (resp.json().get("data") or {}).get("random_engine") or {}
            assert (eng.get("circuit") or {}).get("state") == "open"
            assert float((eng.get("circuit") or {}).get("open_remaining_s") or 0) > 0
    finally:
        reset_engine_circuit_for_tests()


def test_status_html_shows_dual_run_circuit_chip(tmp_path: Path, monkeypatch) -> None:
    """HTML /status surfaces dual-run circuit chip from the same payload."""
    reset_engine_circuit_for_tests()
    app = _seed_app(
        tmp_path,
        monkeypatch,
        engine_url="http://engine.test:8080",
        enabled="true",
    )

    with TestClient(app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        text = resp.text
        assert "dual-run: on" in text
        assert "circuit closed" in text
        assert "random_engine" in text  # DATA payload includes the field
