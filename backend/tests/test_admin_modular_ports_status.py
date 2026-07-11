from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_modular_ports_status_defaults(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_modular_ports.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.delenv("RECENT_DEDUP_BACKEND", raising=False)
    monkeypatch.delenv("JOB_QUEUE_BACKEND", raising=False)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/modular-ports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["catalog"]["backend"] == "sqlite"
        assert body["job_queue"]["backend"] == "sqlite"
        assert body["job_queue"]["requested"] == "sqlite"
        assert body["recent_dedup"]["configured_backend"] == "memory"
        assert body["recent_dedup"]["active_backend"] == "memory"
        assert body["recent_dedup"]["using_memory_fallback"] is False
        assert body["random_service"]["backend"] == "default"


def test_admin_modular_ports_status_recent_redis_fallback(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_modular_ports_redis.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RECENT_DEDUP_BACKEND", "redis")
    monkeypatch.setenv("JOB_QUEUE_BACKEND", "nats")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/modular-ports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["recent_dedup"]["configured_backend"] == "redis"
        # No REDIS_URL → factory stays on memory (using_memory_fallback).
        assert body["recent_dedup"]["active_backend"] == "memory"
        assert body["recent_dedup"]["using_memory_fallback"] is True
        assert body["job_queue"]["backend"] == "sqlite"
        assert body["job_queue"]["requested"] == "nats"


def test_admin_modular_ports_status_recent_redis_active(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_modular_ports_redis_active.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RECENT_DEDUP_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/modular-ports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["recent_dedup"]["configured_backend"] == "redis"
        assert body["recent_dedup"]["active_backend"] == "redis"
        assert body["recent_dedup"]["using_memory_fallback"] is False
