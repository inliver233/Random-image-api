from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_cf_api_proxy_status_default_not_ready(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_cf_api_proxy_status.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.delenv("CF_API_PROXY_ENABLED", raising=False)
    monkeypatch.delenv("CF_API_PROXY_SECRET", raising=False)
    monkeypatch.delenv("CF_API_PROXY_BASE_URLS", raising=False)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/cf-api-proxy",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled_flag"] is False
        assert body["ready"] is False
        assert body["has_secret"] is False
        assert "CF_API_PROXY_ENABLED" in body["missing"]
        assert "secret" not in body
        assert "CF_API_PROXY_SECRET" not in str(body.get("base_urls"))


def test_admin_cf_api_proxy_status_ready_no_secret_leak(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_cf_api_proxy_status_ready.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("CF_API_PROXY_ENABLED", "1")
    monkeypatch.setenv("CF_API_PROXY_SECRET", "super-secret-do-not-leak")
    monkeypatch.setenv("CF_API_PROXY_BASE_URLS", "https://api.example.com")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/cf-api-proxy",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled_flag"] is True
        assert body["ready"] is True
        assert body["has_secret"] is True
        assert body["base_urls"] == ["https://api.example.com"]
        assert body["missing"] == []
        raw = resp.text
        assert "super-secret-do-not-leak" not in raw


def test_admin_cf_api_proxy_status_enabled_without_secret_not_ready(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_cf_api_proxy_status_no_secret.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("CF_API_PROXY_ENABLED", "1")
    monkeypatch.setenv("CF_API_PROXY_BASE_URLS", "https://api.example.com")
    monkeypatch.delenv("CF_API_PROXY_SECRET", raising=False)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/cf-api-proxy",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled_flag"] is True
        assert body["ready"] is False
        assert body["has_secret"] is False
        assert "CF_API_PROXY_SECRET" in body["missing"]
