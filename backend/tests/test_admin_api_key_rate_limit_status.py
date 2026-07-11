from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_api_key_rate_limit_status_defaults(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_api_key_rl.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.delenv("PUBLIC_API_KEY_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_API_KEY_RATE_LIMIT_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PUBLIC_API_KEY_REDIS_URL", raising=False)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/api-key-rate-limit",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["required"] is False
        assert body["configured_backend"] == "memory"
        assert body["active_backend"] == "memory"
        assert body["redis_url_configured"] is False
        assert body["using_memory_fallback"] is False
        # Never leak connection strings.
        raw = resp.text.lower()
        assert "redis://" not in raw
        assert "password" not in raw or "pass_test" not in raw


def test_admin_api_key_rate_limit_status_redis_config_without_live(tmp_path: Path, monkeypatch) -> None:
    """Configured redis still reports active_backend=redis until first use; URL never leaked."""
    db_path = tmp_path / "admin_api_key_rl_redis.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("PUBLIC_API_KEY_RPM", "120")
    monkeypatch.setenv("PUBLIC_API_KEY_BURST", "20")
    monkeypatch.setenv("PUBLIC_API_KEY_RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://super-secret-host:6379/3")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/api-key-rate-limit",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["required"] is True
        assert body["rpm"] == 120
        assert body["burst"] == 20
        assert body["configured_backend"] == "redis"
        assert body["active_backend"] == "redis"
        assert body["redis_url_configured"] is True
        assert body["using_memory_fallback"] is False
        assert "super-secret-host" not in resp.text
        assert "redis://" not in resp.text
