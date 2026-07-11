from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_image_edge_status_default_not_ready(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_image_edge_status.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.delenv("IMAGE_EDGE_ENABLED", raising=False)
    monkeypatch.delenv("IMAGE_EDGE_SECRET", raising=False)
    monkeypatch.delenv("IMAGE_EDGE_BASE_URLS", raising=False)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/image-edge",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled_flag"] is False
        assert body["ready"] is False
        assert body["has_secret"] is False
        assert "IMAGE_EDGE_ENABLED" in body["missing"]
        assert "secret" not in body
        assert "IMAGE_EDGE_SECRET" not in str(body.get("base_urls"))


def test_admin_image_edge_status_ready_no_secret_leak(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_image_edge_status_ready.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "1")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "super-secret-do-not-leak")
    monkeypatch.setenv("IMAGE_EDGE_SECRET_PREVIOUS", "old-secret-also-hidden")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img.example.com")
    monkeypatch.setenv("IMAGE_EDGE_SIGN_TTL_SECONDS", "3600")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/image-edge",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled_flag"] is True
        assert body["ready"] is True
        assert body["has_secret"] is True
        assert body["has_secret_previous"] is True
        assert body["base_urls"] == ["https://img.example.com"]
        assert body["sign_ttl_seconds"] == 3600
        assert body["missing"] == []
        raw = resp.text
        assert "super-secret-do-not-leak" not in raw
        assert "old-secret-also-hidden" not in raw
