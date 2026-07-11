from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_r2_prewarm_status_default_not_ready(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_r2_prewarm_status.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.delenv("R2_PREWARM_ENABLED", raising=False)
    monkeypatch.delenv("R2_PREWARM_URL", raising=False)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/r2-prewarm",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled_flag"] is False
        assert body["ready"] is False
        assert body["url_configured"] is False
        assert "R2_PREWARM_ENABLED" in body["missing"]
        assert "R2_PREWARM_URL" in body["missing"]


def test_admin_r2_prewarm_status_ready_no_url_leak(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_r2_prewarm_status_ready.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("R2_PREWARM_ENABLED", "1")
    monkeypatch.setenv("R2_PREWARM_URL", "https://prewarm.example.com/hook?token=super-secret")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret-for-prewarm")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/r2-prewarm",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled_flag"] is True
        assert body["ready"] is True
        assert body["url_configured"] is True
        assert body["secret_configured"] is True
        assert body["payload_shape"] == "paths"
        assert body["url_preview"] == "https://prewarm.example.com"
        assert "super-secret" not in str(body)
        assert "edge-secret-for-prewarm" not in str(body)
        assert body["missing"] == []
